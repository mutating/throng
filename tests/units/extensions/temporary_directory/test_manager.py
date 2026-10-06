from contextlib import nullcontext
from pathlib import Path
from shutil import rmtree
from unittest.mock import Mock, call

import pytest
from cantok import DefaultToken, SimpleToken

from throng.abstracts.results import SimpleRunResult
from throng.errors import NotSuccessfulRunError, PreparationCommandFailedError
from throng.extensions.temporary_directory.isolate import TemporaryDirectoryIsolate
from throng.extensions.temporary_directory.manager import TemporaryDirectoryManager


@pytest.mark.parametrize('exclude', [None, [], ['cache/', '*.tmp', '!keep.tmp']])
def test_read_delegates_source_settings(tmp_path, monkeypatch, exclude):
    """Read the configured source directory with the manager's exclusions."""
    expected_exclude = None if exclude is None else exclude.copy()
    manager = TemporaryDirectoryManager(tmp_path, exclude)
    read = Mock(return_value=b'\x00\xffstate')
    monkeypatch.setattr(
        'throng.extensions.temporary_directory.manager.read_directory',
        read,
    )

    assert manager.read() is read.return_value
    read.assert_called_once_with(tmp_path, expected_exclude)


@pytest.mark.parametrize('state', [b'', b'state', b'\x00\xff'])
@pytest.mark.parametrize('exclude', [None, [], ['cache/', '*.tmp', '!keep.tmp']])
@pytest.mark.parametrize('prepare', [None, [], ['first', 'second']])
@pytest.mark.parametrize('operation', ['get', '_get'])
def test_get_delegates_snapshot_without_reading_source(
    monkeypatch,
    state,
    exclude,
    prepare,
    operation,
):
    """Restore the supplied opaque snapshot without rereading the source directory."""
    expected_exclude = None if exclude is None else exclude.copy()
    expected_prepare = None if prepare is None else prepare.copy()
    manager = TemporaryDirectoryManager('unread source', exclude, prepare)
    constructor, read, read_source = Mock(), Mock(), Mock()
    constructor.return_value.chain.return_value = [SimpleRunResult(True) for _ in prepare or []]
    monkeypatch.setattr(
        'throng.extensions.temporary_directory.manager.TemporaryDirectoryIsolate',
        constructor,
    )
    monkeypatch.setattr(manager, 'read', read)
    monkeypatch.setattr(
        'throng.extensions.temporary_directory.manager.read_directory',
        read_source,
    )

    assert getattr(manager, operation)(state) is constructor.return_value
    constructor.assert_called_once_with(state, expected_exclude)
    if operation == 'get' and prepare:
        token = constructor.return_value.chain.call_args.kwargs['token']
        assert isinstance(token, DefaultToken)
        constructor.return_value.chain.assert_called_once_with(*expected_prepare, exception=True, token=token)
    else:
        constructor.return_value.chain.assert_not_called()
    assert prepare == expected_prepare
    read.assert_not_called()
    read_source.assert_not_called()


def test_get_passes_explicit_token_to_preparation_executor(tmp_path, monkeypatch):
    """Use the caller's token for each setup command in a real copied isolate."""
    execute = Mock(return_value=SimpleRunResult(True))
    monkeypatch.setattr('throng.extensions.temporary_directory.isolate.run', execute)
    commands = ['first', 'second']
    manager = TemporaryDirectoryManager(tmp_path, prepare=commands)
    token = SimpleToken()

    isolate = manager.get(manager.read(), token=token)
    try:
        assert isinstance(isolate, TemporaryDirectoryIsolate)
        assert execute.call_args_list == [
            call(command, token=token, catch_output=True, catch_exceptions=True, directory=isolate.path)
            for command in commands
        ]
        assert all(entry.kwargs['token'] is token for entry in execute.call_args_list)
    finally:
        isolate.kill()


@pytest.mark.parametrize('operation', ['read', 'get'])
def test_delegate_error_is_preserved(tmp_path, monkeypatch, operation):
    """Keep the original cause when snapshot reading or isolate creation fails."""
    error = OSError('delegate failed')
    dependency = Mock(side_effect=error)
    name = 'read_directory' if operation == 'read' else 'TemporaryDirectoryIsolate'
    monkeypatch.setattr(
        f'throng.extensions.temporary_directory.manager.{name}',
        dependency,
    )
    manager = TemporaryDirectoryManager(tmp_path)

    with pytest.raises(OSError, match='delegate failed') as caught:
        getattr(manager, operation)(*(() if operation == 'read' else (b'state',)))

    assert caught.value is error
    assert dependency.call_count == 1


@pytest.mark.parametrize('populated', [False, True])
def test_same_snapshot_produces_independent_isolates(tmp_path, populated):
    """Keep directories, modifications and cleanup independent for each isolate."""
    if populated:
        (tmp_path / 'file').write_bytes(b'original')
    manager = TemporaryDirectoryManager(tmp_path)
    state = manager.read()
    first = manager.get(state)
    try:
        second = manager.get(state)
        try:
            assert first is not second
            assert first.path != second.path
            (first.path / 'file').write_bytes(b'changed')
            if populated:
                assert (second.path / 'file').read_bytes() == b'original'
            else:
                assert not (second.path / 'file').exists()
            first.kill()
            assert not first.lock.locked()
            assert second.path.is_dir()
            assert tmp_path.is_dir()
        finally:
            second.directory.cleanup()
            if second.lock.locked():
                second.lock.release()
    finally:
        first.directory.cleanup()
        if first.lock.locked():
            first.lock.release()


@pytest.mark.parametrize('change', ['modify_file', 'delete_file', 'delete_directory'])
def test_snapshot_survives_source_changes(tmp_path, change):
    """Restore saved contents even after the source file or whole directory disappears."""
    directory = tmp_path / 'source'
    directory.mkdir()
    source = directory / 'file'
    source.write_bytes(b'saved')
    manager = TemporaryDirectoryManager(directory)
    state = manager.read()
    if change == 'modify_file':
        source.write_bytes(b'new')
    elif change == 'delete_file':
        source.unlink()
    else:
        rmtree(directory)
    isolate = manager.get(state)
    try:
        assert (isolate.path / 'file').read_bytes() == b'saved'
    finally:
        isolate.directory.cleanup()
        if isolate.lock.locked():
            isolate.lock.release()


@pytest.mark.parametrize('fail_body', [False, True])
def test_scope_discards_copy_and_keeps_source(tmp_path, fail_body):
    """Remove the temporary copy on every exit without changing source files."""
    (tmp_path / 'file').write_bytes(b'original')
    manager = TemporaryDirectoryManager(tmp_path)
    expectation = (
        pytest.raises(ValueError, match='body failed') if fail_body else nullcontext()
    )

    with expectation, manager.scope as isolate:
        assert isolate.path != tmp_path
        assert (isolate.path / 'file').read_bytes() == b'original'
        (isolate.path / 'file').write_bytes(b'changed')
        (isolate.path / 'new').write_bytes(b'new')
        if fail_body:
            raise ValueError('body failed')

    assert not isolate.path.exists()
    assert (tmp_path / 'file').read_bytes() == b'original'
    assert not (tmp_path / 'new').exists()


@pytest.mark.parametrize('prepare', [None, [], ['first'], ['first', 'second', 'first']])
@pytest.mark.parametrize('exclude', [None, ['*.tmp']])
def test_get_restores_state_before_preparation(tmp_path, monkeypatch, prepare, exclude):
    """Restore the snapshot and initialize all resources before executing preparation."""
    isolate = object.__new__(TemporaryDirectoryIsolate)
    directory = Mock()
    directory.name = str(tmp_path / 'allocated')
    events = Mock()
    events.allocate.return_value = directory
    original_commands = None if prepare is None else prepare.copy()
    manager = TemporaryDirectoryManager(tmp_path, exclude, prepare)
    events.attach_mock(directory.cleanup, 'cleanup')

    def restore(state):
        assert state == b'snapshot'
        assert isolate.directory is directory
        assert isolate.path == Path(directory.name)
        assert isolate.exclude == exclude
        assert isolate.used is False
        assert not isolate.lock.locked()

    def execute(_command, **kwargs):
        events.restore.assert_called_once_with(b'snapshot')
        assert isolate.lock.locked()
        assert isolate.used is False
        assert kwargs['directory'] == Path(directory.name)
        assert kwargs['catch_output'] is True
        assert kwargs['catch_exceptions'] is True
        assert isinstance(kwargs['token'], DefaultToken)
        return SimpleRunResult(True)

    events.restore.side_effect = restore
    events.execute.side_effect = execute
    monkeypatch.setattr('throng.extensions.temporary_directory.isolate.TemporaryDirectory', events.allocate)
    monkeypatch.setattr('throng.extensions.temporary_directory.isolate.run', events.execute)
    monkeypatch.setattr(isolate, 'set_state', events.restore)

    def create(state, exclusions):
        TemporaryDirectoryIsolate.__init__(isolate, state, exclusions)
        return isolate

    constructor = Mock(side_effect=create)
    monkeypatch.setattr('throng.extensions.temporary_directory.manager.TemporaryDirectoryIsolate', constructor)
    try:
        assert manager.get(b'snapshot') is isolate
        constructor.assert_called_once_with(b'snapshot', exclude)

        expected = [call.allocate(), call.restore(b'snapshot')]
        if prepare:
            token = events.execute.call_args.kwargs['token']
            expected += [
                call.execute(command, token=token, catch_output=True, catch_exceptions=True, directory=Path(directory.name))
                for command in original_commands
            ]
        assert events.mock_calls == expected
        assert isolate.used is False
        assert not isolate.lock.locked()
        assert prepare == original_commands
    finally:
        isolate.kill()


@pytest.mark.parametrize('failure', ['result', 'exception', 'interrupt', 'exit'])
def test_failed_preparation_immediately_destroys_allocated_directory(tmp_path, monkeypatch, failure):
    """Clean up before propagation even if the caller retains the isolate and traceback."""
    isolate = object.__new__(TemporaryDirectoryIsolate)
    directory = Mock()
    directory.name = str(tmp_path / 'allocated')
    events = Mock()
    events.allocate.return_value = directory
    events.attach_mock(directory.cleanup, 'cleanup')
    error = {
        'result': None,
        'exception': OSError('executor failed'),
        'interrupt': KeyboardInterrupt(),
        'exit': SystemExit(3),
    }[failure]

    def execute(command, **_kwargs):
        events.restore.assert_called_once_with(b'snapshot')
        assert isolate.lock.locked()
        if command == 'bad' and error is not None:
            raise error
        return SimpleRunResult(command != 'bad')

    def cleanup():
        assert isolate.lock.locked()
        assert isolate.used is False

    events.execute.side_effect = execute
    events.cleanup.side_effect = cleanup
    monkeypatch.setattr('throng.extensions.temporary_directory.isolate.TemporaryDirectory', events.allocate)
    monkeypatch.setattr('throng.extensions.temporary_directory.isolate.run', events.execute)
    monkeypatch.setattr(isolate, 'set_state', events.restore)

    def create(state, exclusions):
        TemporaryDirectoryIsolate.__init__(isolate, state, exclusions)
        return isolate

    constructor = Mock(side_effect=create)
    monkeypatch.setattr('throng.extensions.temporary_directory.manager.TemporaryDirectoryIsolate', constructor)
    manager = TemporaryDirectoryManager(tmp_path, prepare=['first', 'bad', 'last'])
    expected_type = type(error) if failure in ('interrupt', 'exit') else PreparationCommandFailedError
    try:
        with pytest.raises(expected_type) as caught:
            manager.get(b'snapshot')

        constructor.assert_called_once_with(b'snapshot', None)

        commands = ['first', 'bad']
        token = events.execute.call_args.kwargs['token']
        assert events.mock_calls == [call.allocate(), call.restore(b'snapshot')] + [
            call.execute(command, token=token, catch_output=True, catch_exceptions=True, directory=Path(directory.name))
            for command in commands
        ] + [call.cleanup()]
        assert isolate.used is True
        assert not isolate.lock.locked()
        assert caught.value.__traceback__ is not None
        if failure in ('interrupt', 'exit'):
            assert caught.value is error
        elif failure == 'exception':
            assert caught.value.__cause__ is error
        else:
            assert isinstance(caught.value.__cause__, NotSuccessfulRunError)
            assert caught.value.__cause__.result.success is False
    finally:
        events.cleanup.side_effect = None
