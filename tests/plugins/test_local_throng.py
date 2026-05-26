from concurrent.futures import ThreadPoolExecutor
from sys import executable
from threading import Barrier, Lock

import pytest
from cantok import SimpleToken
from full_match import match
from locklib import LockTraceWrapper
from suby.subprocess_result import SubprocessResult

from tests.helpers import make_tar_bytes
from throng import (
    CommandExecutionError,
    IsolateDeletedError,
    OperationCancelledError,
    throngs,
)
from throng.plugins.directory_isolate import DirectoryIsolate, DirectoryIsolationConfig
from throng.plugins.local_throng import LocalDirectoryThrong
from throng.result import RunResult


def assert_isolate_deleted(operation):
    with pytest.raises(IsolateDeletedError, match=match('Isolate has been deleted.')):
        operation()


def test_local_uses_current_working_directory(tmp_path, monkeypatch):
    """Verify that the local plugin runs commands in the current working directory."""
    monkeypatch.chdir(tmp_path)

    marker = tmp_path / 'cwd-marker.txt'

    result = throngs()['local'].get_isolate().run(
        executable,
        '-c',
        'from pathlib import Path; Path("cwd-marker.txt").write_text("ok")',
        catch_output=True,
        split=False,
    )

    assert result.success is True
    assert marker.read_text() == 'ok'


def test_local_same_throng_runs_enter_per_throng_lock(tmp_path, monkeypatch):
    """Verify that run calls from two isolates of one local throng enter its shared lock."""
    monkeypatch.chdir(tmp_path)

    local_throng = LocalDirectoryThrong()
    lock_trace = LockTraceWrapper(Lock())
    local_throng.lock = lock_trace
    first = local_throng.get_isolate()
    second = local_throng.get_isolate()

    def fake_run(*args, **_kwargs):
        lock_trace.notify(str(args[0]))
        return SubprocessResult(id=str(args[0]), returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)

    first.run('first')
    second.run('second')

    assert lock_trace.was_event_locked('first')
    assert lock_trace.was_event_locked('second')


def test_local_different_throng_instances_do_not_share_lock(tmp_path, monkeypatch):
    """Verify that explicit separate LocalDirectoryThrong objects use separate locks and can overlap."""
    monkeypatch.chdir(tmp_path)

    barrier = Barrier(2)

    def fake_run(*_args, **_kwargs):
        barrier.wait(timeout=5)
        return SubprocessResult(id='different-throng', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)
    first = LocalDirectoryThrong().get_isolate()
    second = LocalDirectoryThrong().get_isolate()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(first.run, 'anything'),
            executor.submit(second.run, 'anything'),
        ]
        for future in futures:
            future.result()

    assert barrier.n_waiting == 0


def test_local_lock_released_after_success(tmp_path, monkeypatch):
    """Verify that the local lock is released after a successful command."""
    monkeypatch.chdir(tmp_path)

    isolate = throngs()['local'].get_isolate()

    isolate.run('printf first', catch_output=True)
    result = isolate.run('printf second', catch_output=True)

    assert result.stdout == 'second'


def test_local_lock_released_after_command_error(tmp_path, monkeypatch):
    """Verify that the local lock is released after CommandExecutionError."""
    monkeypatch.chdir(tmp_path)

    isolate = throngs()['local'].get_isolate()

    expected_message = f'Command failed or output decoding failed: Error when executing the command "{executable} -c "import sys; sys.exit(3)"".'
    with pytest.raises(CommandExecutionError, match=match(expected_message)):
        isolate.run(executable, '-c', 'import sys; sys.exit(3)', split=False)

    result = isolate.run('printf after-error', catch_output=True)

    assert result.stdout == 'after-error'


def test_local_lock_released_after_cancellation(tmp_path, monkeypatch):
    """Verify that the local lock is released after OperationCancelledError."""
    monkeypatch.chdir(tmp_path)

    isolate = throngs()['local'].get_isolate()

    with pytest.raises(OperationCancelledError, match=match('The operation was cancelled.')):
        isolate.run('printf never', token=SimpleToken().cancel())

    result = isolate.run('printf after-cancel', catch_output=True)

    assert result.stdout == 'after-cancel'


def test_local_delete_does_not_remove_current_directory(tmp_path, monkeypatch):
    """Verify that local delete marks the isolate deleted without removing the current working directory."""
    monkeypatch.chdir(tmp_path)

    marker = tmp_path / 'kept.txt'
    marker.write_text('content')
    isolate = throngs()['local'].get_isolate()

    isolate.delete()

    assert tmp_path.is_dir()
    assert marker.read_text() == 'content'


@pytest.mark.parametrize('operation', ['run', 'install', 'dump', 'load', 'delete'])
def test_local_operations_after_delete_raise(tmp_path, monkeypatch, operation):
    """Verify that every local isolate operation raises IsolateDeletedError after delete."""
    monkeypatch.chdir(tmp_path)

    isolate = throngs()['local'].get_isolate()
    isolate.delete()

    operations = {
        'run': lambda: isolate.run('printf never'),
        'install': lambda: isolate.install('example'),
        'dump': isolate.dump,
        'load': lambda: isolate.load(make_tar_bytes({'file.txt': b'content'})),
        'delete': isolate.delete,
    }

    assert_isolate_deleted(operations[operation])


def test_local_plugin_dump_install_load_serialized_with_run(tmp_path, monkeypatch):
    """Verify that operations from different local isolates all enter the one lock owned by their throng."""
    monkeypatch.chdir(tmp_path)

    local_throng = LocalDirectoryThrong(config=DirectoryIsolationConfig(use_venv=False))
    lock_trace = LockTraceWrapper(Lock())
    local_throng.lock = lock_trace

    def fake_run(_self, *_args, **_kwargs):
        lock_trace.notify('run')
        return RunResult(id='run', stdout=None, stderr=None, returncode=0)

    def fake_install(_self, *_args, **_kwargs):
        lock_trace.notify('install')

    def fake_dump(_self, *_args, **_kwargs):
        lock_trace.notify('dump')
        return b''

    def fake_load(_self, *_args, **_kwargs):
        lock_trace.notify('load')

    monkeypatch.setattr(DirectoryIsolate, '_run_unlocked', fake_run)
    monkeypatch.setattr(DirectoryIsolate, '_install_unlocked', fake_install)
    monkeypatch.setattr(DirectoryIsolate, '_dump_unlocked', fake_dump)
    monkeypatch.setattr(DirectoryIsolate, '_load_unlocked', fake_load)

    run_isolate = local_throng.get_isolate()
    install_isolate = local_throng.get_isolate()
    dump_isolate = local_throng.get_isolate()
    load_isolate = local_throng.get_isolate()

    run_isolate.run('anything')
    install_isolate.install('example')
    dump_isolate.dump()
    load_isolate.load(b'')

    assert lock_trace.was_event_locked('run')
    assert lock_trace.was_event_locked('install')
    assert lock_trace.was_event_locked('dump')
    assert lock_trace.was_event_locked('load')


def test_local_isolate_operations_are_inside_per_throng_lock(tmp_path, monkeypatch):
    """Verify with LockTraceWrapper that every local isolate operation enters the per-throng lock."""
    monkeypatch.chdir(tmp_path)

    local_throng = LocalDirectoryThrong()
    lock_trace = LockTraceWrapper(Lock())
    local_throng.lock = lock_trace
    isolate = local_throng.get_isolate()

    def traced_run(_self, *_args, **_kwargs):
        lock_trace.notify('run')
        return RunResult(id='run', stdout=None, stderr=None, returncode=0)

    def traced_install(_self, *_args, **_kwargs):
        lock_trace.notify('install')

    def traced_dump(_self, *_args, **_kwargs):
        lock_trace.notify('dump')
        return b''

    def traced_load(_self, *_args, **_kwargs):
        lock_trace.notify('load')

    def traced_delete(_self, *_args, **_kwargs):
        lock_trace.notify('delete')

    monkeypatch.setattr(DirectoryIsolate, '_run_unlocked', traced_run)
    monkeypatch.setattr(DirectoryIsolate, '_install_unlocked', traced_install)
    monkeypatch.setattr(DirectoryIsolate, '_dump_unlocked', traced_dump)
    monkeypatch.setattr(DirectoryIsolate, '_load_unlocked', traced_load)
    monkeypatch.setattr(DirectoryIsolate, '_delete_unlocked', traced_delete)

    isolate.run('anything')
    isolate.install('example')
    isolate.dump()
    isolate.load(b'')
    isolate.delete()

    assert lock_trace.was_event_locked('run')
    assert lock_trace.was_event_locked('install')
    assert lock_trace.was_event_locked('dump')
    assert lock_trace.was_event_locked('load')
    assert lock_trace.was_event_locked('delete')
