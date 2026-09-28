import tarfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from io import BytesIO
from pathlib import Path
from shutil import rmtree
from threading import Event
from unittest.mock import MagicMock, Mock, call

import pytest
from cantok import DefaultToken, SimpleToken

from throng.abstracts.results import SimpleRunResult
from throng.errors import CannotInstallDependencyError
from throng.extensions.temporary_directory.errors import DirectoryDoesNotExistError
from throng.extensions.temporary_directory.isolate import TemporaryDirectoryIsolate
from throng.extensions.temporary_directory.read import read_directory


@pytest.mark.parametrize(
    'files',
    [(), (('file', b''),), (('nested/файл', b'\x00\xff'), ('with spaces', b'text'))],
)
@pytest.mark.parametrize('exclude', [None, [], ['*.tmp']])
def test_constructor_restores_snapshot(files, exclude):
    """Create a live temporary directory containing exactly the supplied files."""
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        for name, data in files:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, BytesIO(data))

    isolate = TemporaryDirectoryIsolate(buffer.getvalue(), exclude)
    try:
        assert isinstance(isolate.path, Path)
        assert isolate.path == Path(isolate.directory.name)
        assert isolate.path.is_dir()
        assert isolate.used is False
        assert isolate.exclude == exclude
        assert {
            path.relative_to(isolate.path).as_posix(): path.read_bytes()
            for path in isolate.path.rglob('*')
            if path.is_file()
        } == dict(files)
    finally:
        isolate.kill()


@pytest.mark.parametrize(
    ('mode', 'module'),
    [('w', None), ('w:gz', 'gzip'), ('w:bz2', 'bz2'), ('w:xz', 'lzma')],
)
def test_constructor_accepts_supported_tar_formats(mode, module):
    """Autodetect supported TAR compression when restoring a state."""
    if module:
        pytest.importorskip(module)
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode=mode) as archive:
        member = tarfile.TarInfo('file')
        member.size = 4
        archive.addfile(member, BytesIO(b'data'))

    isolate = TemporaryDirectoryIsolate(buffer.getvalue(), None)
    try:
        assert (isolate.path / 'file').read_bytes() == b'data'
    finally:
        isolate.kill()


@pytest.mark.parametrize('change', ['add', 'replace', 'nested'])
def test_set_state_updates_live_files(tmp_path, change):
    """Apply new snapshot contents to a live isolate without changing the source."""
    (tmp_path / 'file').write_bytes(b'original')
    isolate = TemporaryDirectoryIsolate(read_directory(tmp_path, None), None)
    name = {'add': 'new', 'replace': 'file', 'nested': 'nested/new'}[change]
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        member = tarfile.TarInfo(name)
        member.size = 7
        archive.addfile(member, BytesIO(b'changed'))
    try:
        assert isolate.set_state(buffer.getvalue()) is None
        assert (isolate.path / name).read_bytes() == b'changed'
        assert (tmp_path / 'file').read_bytes() == b'original'
    finally:
        isolate.kill()


@pytest.mark.parametrize('exclude', [None, [], ['*.tmp']])
def test_read_uses_own_directory_and_exclusions(tmp_path, monkeypatch, exclude):
    """Read the isolate directory with its exclusions and preserve the snapshot bytes."""
    isolate = TemporaryDirectoryIsolate(read_directory(tmp_path, None), exclude)
    read = Mock(return_value=b'\x00\xffsnapshot')
    monkeypatch.setattr(
        'throng.extensions.temporary_directory.isolate.read_directory',
        read,
    )
    try:
        assert isolate.read() is read.return_value
        read.assert_called_once_with(isolate.path, exclude)
    finally:
        isolate.kill()


@pytest.mark.parametrize('change', ['add', 'modify', 'delete'])
def test_read_captures_changes_for_an_independent_isolate(tmp_path, change):
    """Transfer current isolate state without sharing future modifications or cleanup."""
    (tmp_path / 'file').write_bytes(b'old')
    first = TemporaryDirectoryIsolate(read_directory(tmp_path, None), None)
    try:
        if change == 'add':
            (first.path / 'new').write_bytes(b'new')
        elif change == 'modify':
            (first.path / 'file').write_bytes(b'new')
        else:
            (first.path / 'file').unlink()
        state = first.read()
        expected = {
            'add': {'file': b'old', 'new': b'new'},
            'modify': {'file': b'new'},
            'delete': {},
        }[change]
        with tarfile.open(fileobj=BytesIO(state)) as archive:
            assert {
                member.name: archive.extractfile(member).read()
                for member in archive.getmembers()
            } == expected
        second = TemporaryDirectoryIsolate(state, None)
        try:
            assert {
                path.name: path.read_bytes() for path in second.path.iterdir()
            } == expected
            (first.path / 'file').write_bytes(b'changed again')
            first.kill()
            assert {
                path.name: path.read_bytes() for path in second.path.iterdir()
            } == expected
            assert (tmp_path / 'file').read_bytes() == b'old'
        finally:
            second.kill()
    finally:
        first.kill()


@pytest.mark.parametrize('created_later', [False, True])
def test_read_excludes_existing_and_new_files(tmp_path, created_later):
    """Apply exclusions to snapshots without filtering the initial extraction."""
    (tmp_path / 'keep').write_bytes(b'keep')
    if not created_later:
        (tmp_path / 'drop.tmp').write_bytes(b'drop')
    isolate = TemporaryDirectoryIsolate(read_directory(tmp_path, None), ['*.tmp'])
    try:
        if created_later:
            (isolate.path / 'drop.tmp').write_bytes(b'drop')
        assert (isolate.path / 'drop.tmp').is_file()
        with tarfile.open(fileobj=BytesIO(isolate.read())) as archive:
            assert archive.getnames() == ['keep']
            assert archive.extractfile('keep').read() == b'keep'
    finally:
        isolate.kill()


@pytest.mark.parametrize(
    'state',
    [b'', b'not an archive', b'file' + bytes(508), b'\x1f\x8bcorrupt'],
)
def test_invalid_state_releases_lock(tmp_path, state):
    """Reject unreadable archives without retaining the isolate lock."""
    valid_state = read_directory(tmp_path, None)
    isolate = TemporaryDirectoryIsolate(valid_state, None)
    try:
        with pytest.raises((tarfile.ReadError, EOFError)):
            isolate.set_state(state)
        assert not isolate.lock.locked()
        assert isolate.used is False
        isolate.set_state(valid_state)
    finally:
        isolate.kill()


@pytest.mark.parametrize('error_type', [OSError, PermissionError])
def test_extraction_error_closes_archive(tmp_path, monkeypatch, error_type):
    """Close the archive and release the lock when restoring files fails."""
    isolate = TemporaryDirectoryIsolate(read_directory(tmp_path, None), None)
    error = error_type('extraction failed')
    context = MagicMock()
    archive = context.__enter__.return_value
    archive.extractall.side_effect = error
    open_archive = Mock(return_value=context)
    try:
        with monkeypatch.context() as patcher:
            patcher.setattr(
                'throng.extensions.temporary_directory.isolate.tarfile.open',
                open_archive,
            )
            with pytest.raises(error_type) as caught:
                isolate.set_state(b'opaque state')
            assert caught.value is error
            assert open_archive.call_args.kwargs['mode'] == 'r:*'
            assert (
                open_archive.call_args.kwargs['fileobj'].getvalue() == b'opaque state'
            )
            archive.extractall.assert_called_once_with(path=isolate.path)
            context.__exit__.assert_called_once()
            assert context.__exit__.call_args.args[1] is error
            assert not isolate.lock.locked()
    finally:
        isolate.kill()


@pytest.mark.parametrize(
    'command',
    ['', ' ', 'command', 'Привет', 'one\ntwo', '"a b"; x'],
)
@pytest.mark.parametrize('token_kind', ['default', 'active', 'cancelled'])
def test_run_forwards_command_and_token(tmp_path, monkeypatch, command, token_kind):
    """Execute unchanged commands in the temporary directory with cancellation."""
    isolate = TemporaryDirectoryIsolate(read_directory(tmp_path, None), None)
    run = Mock(return_value=SimpleRunResult(True))
    monkeypatch.setattr('throng.extensions.temporary_directory.isolate.run', run)
    token = SimpleToken(cancelled=token_kind == 'cancelled')
    try:
        result = isolate.run(
            command,
            **({} if token_kind == 'default' else {'token': token}),
        )
        passed_token = run.call_args.kwargs['token']
        if token_kind == 'default':
            assert isinstance(passed_token, DefaultToken)
        else:
            assert passed_token is token
        run.assert_called_once_with(
            command,
            token=passed_token,
            catch_output=True,
            catch_exceptions=True,
            directory=isolate.path,
        )
        assert result is run.return_value
    finally:
        isolate.kill()


@pytest.mark.parametrize(
    ('success', 'code', 'killed'),
    [(True, 0, False), (False, 1, False), (False, None, False), (False, -9, True)],
)
def test_run_preserves_plugin_result(tmp_path, monkeypatch, success, code, killed):
    """Preserve unsuccessful and cancelled results, including plugin-specific fields."""
    isolate = TemporaryDirectoryIsolate(read_directory(tmp_path, None), None)
    expected = Mock(success=success, returncode=code, killed_by_token=killed)
    monkeypatch.setattr(
        'throng.extensions.temporary_directory.isolate.run',
        Mock(return_value=expected),
    )
    try:
        assert isolate.run('command') is expected
    finally:
        isolate.kill()


@pytest.mark.parametrize('error_type', [RuntimeError, OSError, KeyboardInterrupt])
def test_execution_error_allows_retry(tmp_path, monkeypatch, error_type):
    """Keep the isolate usable and its lock free after an executor exception."""
    isolate = TemporaryDirectoryIsolate(read_directory(tmp_path, None), None)
    error = error_type('execution failed')
    expected = SimpleRunResult(True)
    run = Mock(side_effect=[error, expected])
    monkeypatch.setattr('throng.extensions.temporary_directory.isolate.run', run)
    try:
        with pytest.raises(error_type) as caught:
            isolate.run('failing')
        assert caught.value is error
        assert not isolate.lock.locked()
        assert isolate.used is False
        assert isolate.run('retry') is expected
    finally:
        isolate.kill()


@pytest.mark.parametrize(
    'packages',
    [(), ('package',), ('one', 'two', 'one'), ('pkg==1.2', 'pkg[extra]')],
)
def test_install_preserves_package_order(tmp_path, monkeypatch, packages):
    """Install requested packages sequentially and skip an empty request."""
    isolate = TemporaryDirectoryIsolate(read_directory(tmp_path, None), None)
    run = Mock(return_value=SimpleRunResult(True))
    monkeypatch.setattr(isolate, 'run', run)
    try:
        assert isolate.install(*packages) is None
        assert run.call_args_list == [
            call(f'pip install {package}') for package in packages
        ]
    finally:
        isolate.kill()


@pytest.mark.parametrize('position', [0, 1, 2])
@pytest.mark.parametrize('returncode', [0, 1, None])
def test_install_stops_at_unsuccessful_result(
    tmp_path,
    monkeypatch,
    position,
    returncode,
):
    """Stop installing at the first false success flag regardless of the exit code."""
    isolate = TemporaryDirectoryIsolate(read_directory(tmp_path, None), None)
    packages = ('one', 'two', 'three')
    run = Mock(
        side_effect=[SimpleRunResult(True)] * position
        + [SimpleRunResult(False, returncode)],
    )
    monkeypatch.setattr(isolate, 'run', run)
    try:
        with pytest.raises(CannotInstallDependencyError):
            isolate.install(*packages)
        assert run.call_args_list == [
            call(f'pip install {package}') for package in packages[: position + 1]
        ]
    finally:
        isolate.kill()


@pytest.mark.parametrize('position', [0, 1, 2])
@pytest.mark.parametrize('error_type', [RuntimeError, OSError, KeyboardInterrupt])
def test_install_preserves_execution_exception(
    tmp_path,
    monkeypatch,
    position,
    error_type,
):
    """Keep installer exceptions intact and avoid executing later packages."""
    isolate = TemporaryDirectoryIsolate(read_directory(tmp_path, None), None)
    error = error_type('installer failed')
    run = Mock(side_effect=[SimpleRunResult(True)] * position + [error])
    monkeypatch.setattr(isolate, 'run', run)
    try:
        with pytest.raises(error_type) as caught:
            isolate.install('one', 'two', 'three')
        assert caught.value is error
        assert run.call_count == position + 1
    finally:
        isolate.kill()


@pytest.mark.parametrize('tree', ['empty', 'populated', 'already_removed'])
@pytest.mark.parametrize('repetitions', [1, 3])
def test_kill_removes_only_own_directory(tmp_path, tree, repetitions):
    """Safely repeat cleanup without deleting source files or another isolate."""
    (tmp_path / 'source').write_bytes(b'keep')
    state = read_directory(tmp_path, None)
    first = TemporaryDirectoryIsolate(state, None)
    try:
        second = TemporaryDirectoryIsolate(state, None)
        try:
            (first.path / 'source').unlink()
            if tree == 'populated':
                (first.path / 'nested').mkdir()
                (first.path / 'nested' / 'file').write_bytes(b'data')
            elif tree == 'already_removed':
                rmtree(first.path)
            for _ in range(repetitions):
                assert first.kill() is None
            first.__del__()
            assert first.used is True
            assert not first.path.exists()
            assert (second.path / 'source').read_bytes() == b'keep'
            assert (tmp_path / 'source').read_bytes() == b'keep'
        finally:
            second.kill()
    finally:
        first.kill()


@pytest.mark.parametrize(
    ('operation', 'arguments', 'message'),
    [
        ('run', ('command',), 'reuse'),
        ('read', (), 're-read'),
        ('set_state', (b'invalid',), 'reuse'),
        ('install', ('one', 'two'), 'reuse'),
    ],
)
def test_destroyed_isolate_rejects_work_before_external_calls(
    tmp_path,
    monkeypatch,
    operation,
    arguments,
    message,
):
    """Reject destroyed isolates before running, reading, restoring or installing."""
    isolate = TemporaryDirectoryIsolate(read_directory(tmp_path, None), None)
    isolate.kill()
    run, read, open_archive = Mock(), Mock(), Mock()
    monkeypatch.setattr('throng.extensions.temporary_directory.isolate.run', run)
    monkeypatch.setattr(
        'throng.extensions.temporary_directory.isolate.read_directory',
        read,
    )
    monkeypatch.setattr(
        'throng.extensions.temporary_directory.isolate.tarfile.open',
        open_archive,
    )

    with pytest.raises(DirectoryDoesNotExistError, match=message):
        getattr(isolate, operation)(*arguments)

    run.assert_not_called()
    read.assert_not_called()
    open_archive.assert_not_called()
    assert not isolate.path.exists()
    assert not isolate.lock.locked()


def test_cleanup_can_be_retried_after_failure(tmp_path, monkeypatch):
    """Report cleanup failure without retaining the lock or preventing a retry."""
    isolate = TemporaryDirectoryIsolate(read_directory(tmp_path, None), None)
    cleanup = isolate.directory.cleanup
    try:
        with monkeypatch.context() as patcher:
            patcher.setattr(
                isolate.directory,
                'cleanup',
                Mock(side_effect=OSError('cleanup failed')),
            )
            with pytest.raises(OSError, match='cleanup failed'):
                isolate.kill()
            assert not isolate.lock.locked()
            assert isolate.path.is_dir()
        isolate.kill()
        assert not isolate.path.exists()
        assert isolate.used is True
    finally:
        cleanup()


@pytest.mark.parametrize('operation', ['run', 'read', 'set_state', 'kill'])
@pytest.mark.parametrize('fail', [False, True])
def test_operations_hold_and_release_lock(tmp_path, monkeypatch, operation, fail):
    """Protect each directory operation and release the lock on success or failure."""
    state = read_directory(tmp_path, None)
    isolate = TemporaryDirectoryIsolate(state, None)
    error = OSError('operation failed')

    def dependency(*_args, **_kwargs):
        assert isolate.lock.locked()
        if fail:
            raise error
        return b'state' if operation == 'read' else SimpleRunResult(True)

    try:
        with monkeypatch.context() as patcher:
            if operation in ('run', 'read'):
                name = 'run' if operation == 'run' else 'read_directory'
                patcher.setattr(
                    f'throng.extensions.temporary_directory.isolate.{name}',
                    dependency,
                )
            elif operation == 'set_state':
                patcher.setattr(tarfile.TarFile, 'extractall', dependency)
            else:
                patcher.setattr(isolate.directory, 'cleanup', dependency)
            arguments = {
                'run': ('command',),
                'read': (),
                'set_state': (state,),
                'kill': (),
            }[operation]
            expectation = (
                pytest.raises(OSError, match='operation failed')
                if fail
                else nullcontext()
            )
            with expectation as caught:
                getattr(isolate, operation)(*arguments)
            if fail:
                assert caught.value is error
            assert not isolate.lock.locked()
    finally:
        isolate.kill()


def test_isolates_have_independent_locks(tmp_path):
    """Keep operations on separate temporary isolates independently lockable."""
    state = read_directory(tmp_path, None)
    first = TemporaryDirectoryIsolate(state, None)
    try:
        second = TemporaryDirectoryIsolate(state, None)
        try:
            assert first.lock is not second.lock
            with first.lock:
                assert not second.lock.locked()
        finally:
            second.kill()
    finally:
        first.kill()


@pytest.mark.parametrize('operation', ['run', 'read', 'set_state'])
def test_kill_waits_for_current_operation(tmp_path, monkeypatch, operation):
    """Keep the directory alive until an in-progress protected operation finishes.

    An observed lock confirms cleanup has attempted entry before it is released.
    """
    state = read_directory(tmp_path, None)
    isolate = TemporaryDirectoryIsolate(state, None)
    lock = isolate.lock
    entered, kill_attempted, release = (Event() for _ in range(3))
    observed_lock = MagicMock()

    def acquire():
        if entered.is_set():
            kill_attempted.set()
        lock.acquire()

    observed_lock.__enter__.side_effect = acquire
    observed_lock.__exit__.side_effect = lambda *_args: lock.release()
    monkeypatch.setattr(isolate, 'lock', observed_lock)

    def dependency(*_args, **_kwargs):
        entered.set()
        assert release.wait(5)
        assert isolate.path.is_dir()
        return b'state' if operation == 'read' else SimpleRunResult(True)

    try:
        with monkeypatch.context() as patcher:
            if operation == 'set_state':
                patcher.setattr(tarfile.TarFile, 'extractall', dependency)
            else:
                name = 'run' if operation == 'run' else 'read_directory'
                patcher.setattr(
                    f'throng.extensions.temporary_directory.isolate.{name}',
                    dependency,
                )
            arguments = {'run': ('command',), 'read': (), 'set_state': (state,)}[
                operation
            ]
            with ThreadPoolExecutor(max_workers=2) as pool:
                work = pool.submit(getattr(isolate, operation), *arguments)
                try:
                    assert entered.wait(5)
                    cleanup = pool.submit(isolate.kill)
                    assert kill_attempted.wait(5)
                    assert not cleanup.done()
                    assert isolate.path.is_dir()
                finally:
                    release.set()
                work.result(timeout=5)
                cleanup.result(timeout=5)
        assert not isolate.path.exists()
        assert isolate.used is True
    finally:
        isolate.kill()
