from concurrent.futures import ThreadPoolExecutor
from errno import EACCES
from gc import collect
from os import name as os_name
from pathlib import Path
from stat import S_IEXEC, S_IREAD, S_IWRITE
from sys import executable, version_info
from tempfile import TemporaryDirectory, gettempdir
from threading import Barrier, Condition, Event, Lock
from typing import cast

import pytest
from emptylog import MemoryLogger
from full_match import match
from locklib import LockTraceWrapper
from suby.subprocess_result import SubprocessResult

from tests.helpers import make_tar_bytes
from throng import (
    InvalidBaseDirectoryError,
    IsolateDeletedError,
    throngs,
)
from throng.plugins.directory_isolate import DirectoryIsolate, DirectoryIsolationConfig
from throng.plugins.empty_lock import EmptyLock
from throng.plugins.temporary_directory_throng import (
    TemporaryDirectoryIsolationConfig,
    TemporaryDirectoryThrong,
)


def assert_isolate_deleted(operation):
    with pytest.raises(IsolateDeletedError, match=match('Isolate has been deleted.')):
        operation()


def test_temp_base_none_uses_stdlib_temp(tmp_path, monkeypatch):
    """Verify that default temporary isolates use distinct stdlib temp roots and log both creations."""
    monkeypatch.chdir(tmp_path)

    logger = MemoryLogger()
    throng = TemporaryDirectoryThrong(logger=logger)
    first = throng.get_isolate()
    second = throng.get_isolate()

    assert first.directory.exists()
    assert second.directory.exists()
    assert first.directory != second.directory
    assert Path(gettempdir()).resolve() in first.directory.resolve().parents
    assert Path(gettempdir()).resolve() in second.directory.resolve().parents
    assert tmp_path not in first.directory.parents
    assert tmp_path not in second.directory.parents
    assert [str(call.message) for call in logger.data.info] == [
        f'Creating temporary isolate in stdlib temporary directory "{first.directory}".',
        f'Creating temporary isolate in stdlib temporary directory "{second.directory}".',
    ]


def test_temp_base_directory_uuid_subdir(tmp_path):
    """Verify that a configured temporary base receives a UUID-hex child and logs its creation."""
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path))
    logger = MemoryLogger()

    isolate = TemporaryDirectoryThrong(logger=logger, config=config).get_isolate()

    assert isolate.directory.parent == tmp_path
    assert len(isolate.directory.name) == 32
    assert all(character in '0123456789abcdef' for character in isolate.directory.name)
    assert isolate.directory.is_dir()
    assert [str(call.message) for call in logger.data.info] == [
        f'Creating temporary isolate "{isolate.directory}" inside base directory "{tmp_path}".',
    ]


def test_temp_base_missing(tmp_path):
    """Verify that a missing temporary base directory raises and logs InvalidBaseDirectoryError."""
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path / 'missing'))
    logger = MemoryLogger()

    with pytest.raises(InvalidBaseDirectoryError, match=match(f'Temporary base directory does not exist: {tmp_path / "missing"}')):
        TemporaryDirectoryThrong(logger=logger, config=config).get_isolate()

    assert [str(call.message) for call in logger.data.error] == [
        f'Temporary base directory does not exist: {tmp_path / "missing"}',
    ]


def test_temp_base_file(tmp_path):
    """Verify that a file temporary base path raises and logs InvalidBaseDirectoryError."""
    file_path = tmp_path / 'file'
    file_path.write_text('content')
    config = TemporaryDirectoryIsolationConfig(base_directory=str(file_path))
    logger = MemoryLogger()

    with pytest.raises(InvalidBaseDirectoryError, match=match(f'Temporary base path is not a directory: {file_path}')):
        TemporaryDirectoryThrong(logger=logger, config=config).get_isolate()

    assert [str(call.message) for call in logger.data.error] == [
        f'Temporary base path is not a directory: {file_path}',
    ]


def test_temp_base_symlink_to_file(tmp_path):
    """Verify that a file symlink temporary base path raises and logs InvalidBaseDirectoryError."""
    if os_name == 'nt':
        pytest.skip('symlink creation often requires elevated privileges on Windows')

    target_file = tmp_path / 'file'
    target_file.write_text('content')
    symlink_path = tmp_path / 'link'
    symlink_path.symlink_to(target_file)
    config = TemporaryDirectoryIsolationConfig(base_directory=str(symlink_path))
    logger = MemoryLogger()

    with pytest.raises(InvalidBaseDirectoryError, match=match(f'Temporary base path is not a directory: {symlink_path}')):
        TemporaryDirectoryThrong(logger=logger, config=config).get_isolate()

    assert [str(call.message) for call in logger.data.error] == [
        f'Temporary base path is not a directory: {symlink_path}',
    ]


def test_temp_base_not_writable(tmp_path, request):
    """Verify that a non-writable temporary base directory is rejected and logged where permissions apply."""
    if os_name == 'nt':
        pytest.skip('permission mode semantics differ on Windows')

    base_directory = tmp_path / 'base'
    base_directory.mkdir()
    request.addfinalizer(lambda: base_directory.chmod(S_IREAD | S_IWRITE | S_IEXEC))
    base_directory.chmod(S_IREAD | S_IEXEC)
    config = TemporaryDirectoryIsolationConfig(base_directory=str(base_directory))
    logger = MemoryLogger()

    with pytest.raises(InvalidBaseDirectoryError, match=match(f'Temporary base directory is not writable: {base_directory}')):
        TemporaryDirectoryThrong(logger=logger, config=config).get_isolate()

    assert [str(call.message) for call in logger.data.error] == [
        f'Temporary base directory is not writable: {base_directory}',
    ]


def test_temp_isolates_are_distinct(tmp_path):
    """Verify that temporary isolates get separate directories and do not share files."""
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path))
    throng = TemporaryDirectoryThrong(config=config)
    first = throng.get_isolate()
    second = throng.get_isolate()

    (first.directory / 'only-first').write_text('content')

    assert first.directory != second.directory
    assert (first.directory / 'only-first').read_text() == 'content'
    assert not (second.directory / 'only-first').exists()


def test_temp_cross_isolate_file_contamination(tmp_path):
    """Verify that commands in one temporary isolate cannot see files created in another isolate."""
    throng = TemporaryDirectoryThrong(config=TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path)))
    first = throng.get_isolate()
    second = throng.get_isolate()

    first.run(executable, '-c', 'from pathlib import Path; Path("created.txt").write_text("first")', split=False)

    assert (first.directory / 'created.txt').read_text() == 'first'
    assert not (second.directory / 'created.txt').exists()


def test_temp_no_shared_command_lock(tmp_path, monkeypatch):
    """Verify that run calls on different temporary isolates can overlap because they do not share a command lock."""
    barrier = Barrier(2)

    def fake_run(*_args, **_kwargs):
        barrier.wait(timeout=5)
        return SubprocessResult(id='temp-distinct', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)
    throng = TemporaryDirectoryThrong(config=TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path)))
    first = throng.get_isolate()
    second = throng.get_isolate()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(first.run, 'anything'),
            executor.submit(second.run, 'anything'),
        ]
        for future in futures:
            future.result()

    assert barrier.n_waiting == 0


def test_temp_same_isolate_operations_are_not_serialized(tmp_path, monkeypatch):
    """Verify that run and install on one temporary isolate can overlap because the isolate uses EmptyLock."""
    entered = 0
    barrier = Barrier(2)
    guard = Lock()

    def fake_run(*_args, **_kwargs):
        nonlocal entered
        with guard:
            entered += 1
        barrier.wait(timeout=5)
        return SubprocessResult(id='temp-serialized', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=False)
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(isolate.run, 'anything'),
            executor.submit(isolate.install, 'example'),
        ]
        for future in futures:
            future.result()

    assert entered == 2
    assert barrier.n_waiting == 0


def test_temporary_throng_isolate_empty_lock_does_not_serialize_operations(tmp_path, monkeypatch):
    """Verify that an isolate returned by TemporaryDirectoryThrong enters a non-serializing lock for every operation."""
    entered = 0
    barrier = Barrier(5)
    guard = Lock()
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=False)
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()
    lock_trace = LockTraceWrapper(isolate.lock)
    isolate.lock = lock_trace

    def traced_operation(name, operation_result):
        def wrapper(*_args, **_kwargs):
            nonlocal entered
            lock_trace.notify(name)
            with guard:
                entered += 1
            barrier.wait(timeout=5)
            return operation_result

        return wrapper

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', traced_operation('run', SubprocessResult(id='temp-run', returncode=0)))
    monkeypatch.setattr(DirectoryIsolate, '_install_unlocked', traced_operation('install', None))
    monkeypatch.setattr(DirectoryIsolate, '_dump_unlocked', traced_operation('dump', b''))
    monkeypatch.setattr(DirectoryIsolate, '_load_unlocked', traced_operation('load', None))
    monkeypatch.setattr(DirectoryIsolate, '_delete_unlocked', traced_operation('delete', None))

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [
            executor.submit(isolate.run, 'anything'),
            executor.submit(isolate.install, 'example'),
            executor.submit(isolate.dump),
            executor.submit(isolate.load, b''),
            executor.submit(isolate.delete),
        ]
        for future in futures:
            future.result()

    assert lock_trace.was_event_locked('run')
    assert lock_trace.was_event_locked('install')
    assert lock_trace.was_event_locked('dump')
    assert lock_trace.was_event_locked('load')
    assert lock_trace.was_event_locked('delete')
    assert entered == 5
    assert barrier.n_waiting == 0


def test_temporary_throng_isolate_empty_lock_allows_concurrent_runs(tmp_path, monkeypatch):
    """Verify that an isolate returned by TemporaryDirectoryThrong permits concurrent runs."""
    entered = 0
    barrier = Barrier(2)
    guard = Lock()
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=False)
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()
    lock_trace = LockTraceWrapper(isolate.lock)
    isolate.lock = lock_trace

    def traced_run(*_args, **_kwargs):
        nonlocal entered
        lock_trace.notify('run')
        with guard:
            entered += 1
        barrier.wait(timeout=5)
        return SubprocessResult(id='temp-run', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', traced_run)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(isolate.run, 'anything'),
            executor.submit(isolate.run, 'anything'),
        ]
        for future in futures:
            future.result()

    assert lock_trace.was_event_locked('run')
    assert entered == 2
    assert barrier.n_waiting == 0


def test_temp_delete_removes_owned_directory(tmp_path):
    """Verify that delete removes an owned temporary-directory isolate when removal is deterministic."""
    throng = TemporaryDirectoryThrong(config=TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path)))
    isolate = throng.get_isolate()
    isolate_directory = isolate.directory

    isolate.delete()

    assert not isolate_directory.exists()


def test_temp_delete_operation_logger_is_added_to_inherited_logger(tmp_path):
    """Verify that delete lifecycle records are emitted to inherited and operation loggers."""
    inherited_logger = MemoryLogger()
    operation_logger = MemoryLogger()
    throng = TemporaryDirectoryThrong(
        logger=inherited_logger,
        config=TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path)),
    )
    isolate = throng.get_isolate()
    inherited_info_before_delete = len(inherited_logger.data.info)

    isolate.delete(logger=operation_logger)

    expected_delete_messages = [
        f'Deleting isolate "{isolate.directory}".',
        'Delete completed successfully.',
    ]
    assert [str(call.message) for call in inherited_logger.data.info[inherited_info_before_delete:]] == expected_delete_messages
    assert [str(call.message) for call in operation_logger.data.info] == expected_delete_messages


def test_temp_configured_base_is_cleaned_when_isolate_is_collected(tmp_path):
    """Verify that a temporary isolate inside a configured base is cleaned up if its owner is collected."""
    throng = TemporaryDirectoryThrong(config=TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path)))
    isolate = throng.get_isolate()
    isolate_directory = isolate.directory

    del isolate
    collect()

    assert not isolate_directory.exists()


def test_temp_delete_failure_raises_and_does_not_log_success(tmp_path, request):
    """
    Verify that a natural delete permission failure is logged and retryable.

    CPython 3.12 preserves the ``Path`` argument in ``shutil.rmtree`` error
    text, while earlier supported versions stringify it; the operation
    contract is the same in both forms.
    """
    if os_name == 'nt':
        pytest.skip('permission mode semantics differ on Windows')

    base_directory = tmp_path / 'base'
    base_directory.mkdir()
    logger = MemoryLogger()
    throng = TemporaryDirectoryThrong(logger=logger, config=TemporaryDirectoryIsolationConfig(base_directory=str(base_directory)))
    isolate = throng.get_isolate()
    isolate_directory = isolate.directory
    request.addfinalizer(lambda: base_directory.chmod(S_IREAD | S_IWRITE | S_IEXEC))
    base_directory.chmod(S_IREAD | S_IEXEC)
    denied_path = isolate_directory if version_info >= (3, 12) else str(isolate_directory)
    permission_error_message = str(PermissionError(EACCES, 'Permission denied', denied_path))

    with pytest.raises(PermissionError, match=match(permission_error_message)):
        isolate.delete()

    assert isolate_directory.exists()
    assert any('Delete failed' in str(call.message) for call in logger.data.exception)
    assert all(str(call.message) != 'Delete completed successfully.' for call in logger.data.info)

    base_directory.chmod(S_IREAD | S_IWRITE | S_IEXEC)
    isolate.delete()

    assert not isolate_directory.exists()
    assert [str(call.message) for call in logger.data.info].count('Delete completed successfully.') == 1


def test_temp_delete_after_delete_raises(tmp_path):
    """Verify that a second delete call is rejected and logged as a delete operation."""
    logger = MemoryLogger()
    throng = TemporaryDirectoryThrong(logger=logger, config=TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path)))
    isolate = throng.get_isolate()

    isolate.delete()

    assert_isolate_deleted(isolate.delete)
    assert any(str(call.message) == 'Delete rejected because the isolate has been deleted.' for call in logger.data.error)


def test_temp_delete_stdlib_temp_manager(tmp_path, monkeypatch):
    """Verify that delete removes stdlib-managed temporary isolate directories."""
    monkeypatch.chdir(tmp_path)

    isolate = cast(DirectoryIsolate, throngs()['temporary_directory'].get_isolate())
    isolate_directory = isolate.directory

    isolate.delete()

    assert not isolate_directory.exists()


def test_temp_delete_does_not_wait_for_running_operation(tmp_path, monkeypatch):
    """Verify that temporary isolate delete is not serialized behind an in-flight run and does not hide the run result."""
    started = Event()
    release = Event()
    delete_finished = Event()

    def blocking_run(*_args, **_kwargs):
        started.set()
        release.wait(timeout=5)
        return SubprocessResult(id='blocking-run', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', blocking_run)
    throng = TemporaryDirectoryThrong(config=TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path)))
    isolate = throng.get_isolate()
    isolate_directory = isolate.directory

    with ThreadPoolExecutor(max_workers=2) as executor:
        run_future = executor.submit(isolate.run, 'anything')
        assert started.wait(timeout=5)
        delete_future = executor.submit(isolate.delete)
        delete_future.add_done_callback(lambda _future: delete_finished.set())

        assert delete_finished.wait(timeout=5)
        assert not isolate_directory.exists()

        release.set()
        delete_future.result()
        assert run_future.result().id == 'blocking-run'


def test_temp_concurrent_delete_only_succeeds_once(tmp_path):
    """Verify that concurrent temporary isolate deletion has one winner and then rejects the already-deleted isolate."""
    release_cleanup = Event()
    first_cleanup_entered = Event()
    second_future_done = False
    cleanup_entries = 0
    condition = Condition()

    class BlockingCleanupManager:
        def cleanup(self):
            nonlocal cleanup_entries
            with condition:
                cleanup_entries += 1
                if cleanup_entries == 1:
                    first_cleanup_entered.set()
                condition.notify_all()
            release_cleanup.wait(timeout=5)

    isolate = DirectoryIsolate(
        tmp_path,
        DirectoryIsolationConfig(),
        lock=EmptyLock(),
        temporary_directory_manager=cast('TemporaryDirectory[str]', BlockingCleanupManager()),
    )

    def delete_once():
        try:
            isolate.delete()
        except IsolateDeletedError:
            return 'already-deleted'
        return 'deleted'

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(delete_once)
        assert first_cleanup_entered.wait(timeout=5)
        second_future = executor.submit(delete_once)

        def mark_second_done(_future):
            nonlocal second_future_done
            with condition:
                second_future_done = True
                condition.notify_all()

        second_future.add_done_callback(mark_second_done)
        with condition:
            assert condition.wait_for(lambda: cleanup_entries == 2 or second_future_done, timeout=5)
        release_cleanup.set()
        results = [first_future.result(), second_future.result()]

    assert sorted(results) == ['already-deleted', 'deleted']


@pytest.mark.parametrize('operation', ['run', 'install', 'dump', 'load', 'delete'])
def test_temp_operations_after_delete_raise(tmp_path, operation):
    """Verify that every temporary isolate operation raises IsolateDeletedError after delete."""
    isolate = TemporaryDirectoryThrong(config=TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path))).get_isolate()
    isolate.delete()

    operations = {
        'run': lambda: isolate.run('printf never'),
        'install': lambda: isolate.install('example'),
        'dump': isolate.dump,
        'load': lambda: isolate.load(make_tar_bytes({'file.txt': b'content'})),
        'delete': isolate.delete,
    }

    assert_isolate_deleted(operations[operation])
