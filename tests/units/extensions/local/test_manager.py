from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, Mock, call

import pytest
from cantok import DefaultToken

from throng.abstracts.results import SimpleRunResult
from throng.errors import NotSuccessfulRunError, PreparationCommandFailedError
from throng.extensions.local.isolate import LocalIsolate
from throng.extensions.local.manager import LocalManager


@pytest.mark.parametrize('as_string', [False, True])
@pytest.mark.parametrize('exclude', [None, [], ['cache/', '*.tmp', '!keep.tmp']])
@pytest.mark.parametrize('prepare', [None, [], ['first', 'second']])
def test_manager_keeps_source_settings(tmp_path, as_string, exclude, prepare):
    """Use shared manager initialization for paths and file exclusions."""
    expected_exclude = None if exclude is None else exclude.copy()
    expected_prepare = None if prepare is None else prepare.copy()
    manager = LocalManager(str(tmp_path) if as_string else tmp_path, exclude, prepare)

    assert manager.path == tmp_path
    assert isinstance(manager.path, Path)
    assert manager.exclude == expected_exclude
    assert manager.prepare == expected_prepare
    assert not manager.lock.locked()


@pytest.mark.parametrize('prepare', [None, [], ['first', 'second', 'first']])
@pytest.mark.parametrize('operation', ['get', '_get'])
def test_get_keeps_preparation_outside_isolate_constructor(tmp_path, monkeypatch, prepare, operation):
    """Pass resources to the constructor and prepare only through public get."""
    manager = LocalManager(tmp_path, None, prepare)
    expected_prepare = None if prepare is None else prepare.copy()
    constructor, read = Mock(), Mock()
    constructor.return_value.chain.return_value = [SimpleRunResult(True) for _ in prepare or []]
    monkeypatch.setattr('throng.extensions.local.manager.LocalIsolate', constructor)
    monkeypatch.setattr(manager, 'read', read)

    assert getattr(manager, operation)(b'ignored snapshot') is constructor.return_value

    constructor.assert_called_once_with(manager.lock, tmp_path)
    if operation == 'get' and prepare:
        constructor.return_value.chain.assert_called_once_with(*expected_prepare, exception=True)
    else:
        constructor.return_value.chain.assert_not_called()
    assert prepare == expected_prepare
    read.assert_not_called()
    assert not manager.lock.locked()


@pytest.mark.parametrize('failure', ['preparation', 'exception', 'interrupt'])
def test_get_preserves_constructor_failure(tmp_path, monkeypatch, failure):
    """Do not wrap or retry a failure from isolate creation."""
    manager = LocalManager(tmp_path, None, ['setup'])
    error = {
        'preparation': PreparationCommandFailedError('setup failed'),
        'exception': OSError('creation failed'),
        'interrupt': KeyboardInterrupt(),
    }[failure]
    constructor = Mock(side_effect=error)
    monkeypatch.setattr('throng.extensions.local.manager.LocalIsolate', constructor)

    with pytest.raises(type(error)) as caught:
        manager.get(b'ignored')

    assert caught.value is error
    constructor.assert_called_once_with(manager.lock, tmp_path)
    assert not manager.lock.locked()


@pytest.mark.parametrize('same_path', [False, True])
def test_managers_have_independent_locks(tmp_path, same_path):
    """Keep concurrency limits independent even for managers using the same source."""
    first = LocalManager(tmp_path, None)
    second = LocalManager(tmp_path if same_path else tmp_path / 'other', None)

    assert first.lock is not second.lock
    with first.lock:
        assert not second.lock.locked()


@pytest.mark.parametrize(
    'state',
    [b'', b'state', b'\x00\xff', b'not really a tar archive'],
)
@pytest.mark.parametrize('same_state', [False, True])
def test_get_ignores_state_and_shares_manager_lock(tmp_path, state, same_state):
    """Create distinct isolates using the original directory and one manager lock."""
    manager = LocalManager(tmp_path, None)

    first = manager.get(state)
    second = manager.get(state if same_state else b'different state')

    assert isinstance(first, LocalIsolate)
    assert isinstance(second, LocalIsolate)
    assert first is not second
    assert first.path is second.path is manager.path
    assert first.lock is second.lock is manager.lock
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('exclude', [None, [], ['*']])
@pytest.mark.parametrize('populated', [False, True])
@pytest.mark.parametrize('operation', ['read', 'get'])
def test_state_operations_do_not_snapshot_or_modify_files(
    tmp_path,
    monkeypatch,
    exclude,
    populated,
    operation,
):
    """Read state and recreate local isolates without snapshotting or changing files."""
    manager = LocalManager(tmp_path, exclude)
    if populated:
        (tmp_path / 'nested').mkdir()
    for content in (b'original', b'changed'):
        if populated:
            (tmp_path / 'nested' / 'file').write_bytes(content)
        operations = {
            name: Mock(
                side_effect=AssertionError(f'Local state must not read files: {name}'),
            )
            for name in ('builtins.open', 'io.open', 'os.scandir', 'os.listdir')
        }
        with monkeypatch.context() as patcher:
            for name, spy in operations.items():
                patcher.setattr(name, spy)
            if operation == 'read':
                assert manager.read() == b''
            else:
                assert isinstance(manager.get(b'ignored'), LocalIsolate)
        for spy in operations.values():
            spy.assert_not_called()
        if populated:
            assert (tmp_path / 'nested' / 'file').read_bytes() == content
    assert sorted(
        path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob('*')
    ) == (['nested', 'nested/file'] if populated else [])


@pytest.mark.parametrize('change', ['modify', 'delete'])
def test_get_keeps_current_source_contents(tmp_path, change):
    """Keep current local files rather than restoring an earlier snapshot."""
    source = tmp_path / 'file'
    source.write_bytes(b'old')
    manager = LocalManager(tmp_path, None)
    state = manager.read()
    if change == 'modify':
        source.write_bytes(b'new')
    else:
        source.unlink()

    isolate = manager.get(state)

    assert isolate.path == tmp_path
    if change == 'modify':
        assert (isolate.path / 'file').read_bytes() == b'new'
    else:
        assert not (isolate.path / 'file').exists()


@pytest.mark.parametrize('fail_first', [False, True])
def test_isolates_of_one_manager_serialize_commands(tmp_path, monkeypatch, fail_first):
    """Release the shared lock before another isolate executes, including on failure.

    Events confirm the second worker attempts acquisition while the first owns it.
    Acquisition times out so a leaked lock fails instead of blocking pool shutdown.
    """
    manager = LocalManager(tmp_path, None)
    lock = manager.lock
    first_entered, second_attempted, release_first, second_entered = (
        Event() for _ in range(4)
    )
    observed_lock = MagicMock()

    def acquire():
        if first_entered.is_set():
            second_attempted.set()
        assert lock.acquire(timeout=5), 'The shared lock was not released.'

    observed_lock.__enter__.side_effect = acquire
    observed_lock.__exit__.side_effect = lambda *_args: lock.release()
    monkeypatch.setattr(manager, 'lock', observed_lock)
    first, second = manager.get(b''), manager.get(b'')

    def execute(command, **_kwargs):
        if command == 'first':
            first_entered.set()
            assert release_first.wait(5)
            if fail_first:
                raise RuntimeError('first failed')
        else:
            second_entered.set()
        return SimpleRunResult(True)

    monkeypatch.setattr('throng.extensions.local.isolate.run', execute)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(first.run, 'first')
        try:
            assert first_entered.wait(5)
            second_future = pool.submit(second.run, 'second')
            assert second_attempted.wait(5)
            assert not second_entered.is_set()
        finally:
            release_first.set()
        if fail_first:
            with pytest.raises(RuntimeError, match='first failed'):
                first_future.result(timeout=5)
        else:
            assert first_future.result(timeout=5).success
        assert second_future.result(timeout=5).success
    assert not lock.locked()


@pytest.mark.parametrize('same_path', [False, True])
def test_different_managers_can_execute_together(tmp_path, monkeypatch, same_path):
    """Let independent managers enter the executor before either command finishes."""
    first = LocalManager(tmp_path, None).get(b'')
    second = LocalManager(tmp_path if same_path else tmp_path / 'other', None).get(b'')
    entered = {'first': Event(), 'second': Event()}
    release = Event()

    def execute(command, **_kwargs):
        entered[command].set()
        assert release.wait(5)
        return SimpleRunResult(True)

    monkeypatch.setattr(
        'throng.extensions.local.isolate.run',
        Mock(side_effect=execute),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(isolate.run, command)
            for isolate, command in ((first, 'first'), (second, 'second'))
        ]
        try:
            assert entered['first'].wait(5)
            assert entered['second'].wait(5)
        finally:
            release.set()
        assert all(future.result(timeout=5).success for future in futures)


@pytest.mark.parametrize('prepare', [None, [], ['first'], ['first', 'second', 'first']])
@pytest.mark.parametrize('default_path', [False, True])
def test_get_prepares_with_initialized_path_and_lock(tmp_path, monkeypatch, prepare, default_path):
    """Make execution resources available before the first preparation command."""
    expected_path = Path() if default_path else tmp_path
    manager = LocalManager(expected_path, None, prepare)
    lock = manager.lock
    original_commands = None if prepare is None else prepare.copy()

    def execute(_command, **kwargs):
        assert lock.locked()
        assert kwargs['directory'] == expected_path
        assert kwargs['catch_output'] is True
        assert kwargs['catch_exceptions'] is True
        assert isinstance(kwargs['token'], DefaultToken)
        return SimpleRunResult(True)

    run = Mock(side_effect=execute)
    monkeypatch.setattr('throng.extensions.local.isolate.run', run)

    isolate = manager.get(b'ignored')

    assert isolate.lock is lock
    assert isolate.path == expected_path
    assert not lock.locked()
    assert [entry.args[0] for entry in run.call_args_list] == (original_commands or [])
    assert prepare == original_commands
    isolate.kill()


@pytest.mark.parametrize('failure', ['result', 'exception', 'interrupt', 'exit'])
def test_failed_preparation_releases_lock_before_cleanup(tmp_path, monkeypatch, failure):
    """Clean up a failed local isolate immediately, after releasing its execution lock."""
    manager = LocalManager(tmp_path, None, ['first', 'bad', 'last'])
    lock = manager.lock
    isolate = LocalIsolate(lock, tmp_path)
    constructor = Mock(return_value=isolate)
    monkeypatch.setattr('throng.extensions.local.manager.LocalIsolate', constructor)
    events = Mock()
    error = {
        'result': None,
        'exception': OSError('executor failed'),
        'interrupt': KeyboardInterrupt(),
        'exit': SystemExit(3),
    }[failure]

    def execute(command, **_kwargs):
        assert isolate.lock is lock
        assert isolate.path == tmp_path
        assert lock.locked()
        if command == 'bad' and error is not None:
            raise error
        return SimpleRunResult(command != 'bad')

    def cleanup():
        assert not lock.locked()

    events.execute.side_effect = execute
    events.kill.side_effect = cleanup
    monkeypatch.setattr('throng.extensions.local.isolate.run', events.execute)
    monkeypatch.setattr(isolate, 'kill', events.kill)
    expected_type = type(error) if failure in ('interrupt', 'exit') else PreparationCommandFailedError

    with pytest.raises(expected_type) as caught:
        manager.get(b'ignored')

    constructor.assert_called_once_with(lock, tmp_path)

    commands = ['first', 'bad']
    token = events.execute.call_args.kwargs['token']
    assert events.mock_calls == [
        call.execute(command, token=token, catch_output=True, catch_exceptions=True, directory=tmp_path)
        for command in commands
    ] + [call.kill()]
    assert not lock.locked()
    if failure in ('interrupt', 'exit'):
        assert caught.value is error
    elif failure == 'exception':
        assert caught.value.__cause__ is error
    else:
        assert isinstance(caught.value.__cause__, NotSuccessfulRunError)
        assert caught.value.__cause__.result.success is False
