from pathlib import Path
from threading import Lock
from unittest.mock import MagicMock, Mock, call

import pytest
from cantok import DefaultToken, SimpleToken

from throng.abstracts.results import SimpleRunResult
from throng.errors import CannotInstallDependencyError
from throng.extensions.local.isolate import LocalIsolate


@pytest.mark.parametrize('path_kind', ['default', 'existing', 'missing'])
def test_constructor_preserves_resources(tmp_path, path_kind):
    """Store the supplied lock and path without creating or validating directories."""
    lock = Lock()
    options = {
        'default': {},
        'existing': {'path': tmp_path},
        'missing': {'path': tmp_path / 'missing'},
    }

    isolate = LocalIsolate(lock, **options[path_kind])

    assert isolate.lock is lock
    assert isolate.path == options[path_kind].get('path', Path())
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    'command',
    ['', ' ', 'command', 'Привет', 'one\ntwo', '"a b"; x'],
)
@pytest.mark.parametrize('token_kind', ['default', 'active', 'cancelled'])
def test_run_forwards_command_and_token(tmp_path, monkeypatch, command, token_kind):
    """Forward commands with the source directory, capture options and cancellation."""
    isolate = LocalIsolate(Lock(), tmp_path)
    run = Mock(return_value=SimpleRunResult(True))
    monkeypatch.setattr('throng.extensions.local.isolate.run', run)
    token = SimpleToken(cancelled=token_kind == 'cancelled')

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
        directory=tmp_path,
    )
    assert result is run.return_value


@pytest.mark.parametrize(
    'outcome',
    [
        (True, 0, '\n世界\n', ' warning\n', False),
        (False, 1, '', 'error', False),
        (False, None, None, None, False),
        (False, -9, 'partial', '', True),
        (False, 0, '', '', True),
        (True, 1, 'output', 'diagnostic', False),
    ],
)
def test_run_preserves_result_and_holds_lock(tmp_path, monkeypatch, outcome):
    """Protect execution with the lock while retaining all plugin result data."""
    lock = Lock()
    isolate = LocalIsolate(lock, tmp_path)
    success, code, stdout, stderr, killed = outcome
    fields = {
        'success': success,
        'returncode': code,
        'stdout': stdout,
        'stderr': stderr,
        'killed_by_token': killed,
    }
    expected = Mock(**fields)

    def execute(*_args, **_kwargs):
        assert lock.locked()
        return expected

    monkeypatch.setattr('throng.extensions.local.isolate.run', execute)

    assert isolate.run('command') is expected
    assert {name: getattr(expected, name) for name in fields} == fields
    assert not lock.locked()


@pytest.mark.parametrize('error_type', [RuntimeError, OSError, KeyboardInterrupt])
def test_execution_error_releases_lock(tmp_path, monkeypatch, error_type):
    """Propagate executor errors without blocking subsequent commands."""
    lock = Lock()
    isolate = LocalIsolate(lock, tmp_path)
    error = error_type('execution failed')
    expected = SimpleRunResult(True)
    run = Mock(side_effect=[error, expected])
    monkeypatch.setattr('throng.extensions.local.isolate.run', run)

    with pytest.raises(error_type) as caught:
        isolate.run('failing')

    assert caught.value is error
    assert not lock.locked()
    assert isolate.run('retry') is expected
    assert not lock.locked()


@pytest.mark.parametrize('error_type', [RuntimeError, KeyboardInterrupt])
def test_lock_failure_prevents_execution(tmp_path, monkeypatch, error_type):
    """Do not reach the executor if entering the command lock fails."""
    lock = MagicMock()
    error = error_type('lock failed')
    lock.__enter__.side_effect = error
    isolate = LocalIsolate(lock, tmp_path)
    run = Mock()
    monkeypatch.setattr('throng.extensions.local.isolate.run', run)

    with pytest.raises(error_type) as caught:
        isolate.run('command')

    assert caught.value is error
    run.assert_not_called()
    lock.__exit__.assert_not_called()


@pytest.mark.parametrize('populated', [False, True])
def test_read_returns_empty_state_without_changes(tmp_path, monkeypatch, populated):
    """Return empty local state without reading or modifying the user's files."""
    isolate = LocalIsolate(Lock(), tmp_path)

    for content in (b'original', b'changed'):
        if populated:
            (tmp_path / 'file').write_bytes(content)
        operations = {
            name: Mock(
                side_effect=AssertionError(f'Local state must not read files: {name}'),
            )
            for name in ('builtins.open', 'io.open', 'os.scandir', 'os.listdir')
        }
        with monkeypatch.context() as patcher:
            for name, operation in operations.items():
                patcher.setattr(name, operation)
            assert isolate.read() == b''
        for operation in operations.values():
            operation.assert_not_called()
        if populated:
            assert (tmp_path / 'file').read_bytes() == content
    assert list(tmp_path.iterdir()) == ([tmp_path / 'file'] if populated else [])


@pytest.mark.parametrize('repetitions', [1, 3])
@pytest.mark.parametrize('populated', [False, True])
def test_kill_preserves_source_files(tmp_path, repetitions, populated):
    """Leave the original local directory intact when releasing an isolate."""
    if populated:
        (tmp_path / 'nested').mkdir()
        (tmp_path / 'nested' / 'file').write_bytes(b'keep')
    isolate = LocalIsolate(Lock(), tmp_path)

    for _ in range(repetitions):
        assert isolate.kill() is None

    assert tmp_path.is_dir()
    if populated:
        assert (tmp_path / 'nested' / 'file').read_bytes() == b'keep'
    else:
        assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    'packages',
    [(), ('package',), ('one', 'two', 'one'), ('pkg==1.2', 'pkg[extra]')],
)
@pytest.mark.parametrize(
    ('returncode', 'stderr'),
    [(None, None), (0, ''), (0, 'installer warning'), (1, ''), (-9, '')],
)
def test_install_preserves_package_order(
    tmp_path,
    monkeypatch,
    packages,
    returncode,
    stderr,
):
    """Install in order, trusting success regardless of the exit code or diagnostics."""
    isolate = LocalIsolate(Lock(), tmp_path)
    run = Mock(return_value=SimpleRunResult(True, returncode, 'installed', stderr))
    monkeypatch.setattr(isolate, 'run', run)

    assert isolate.install(*packages) is None
    assert run.call_args_list == [
        call(f'pip install {package}') for package in packages
    ]


@pytest.mark.parametrize('position', [0, 1, 2])
@pytest.mark.parametrize('returncode', [0, 1, None])
def test_install_stops_at_unsuccessful_result(
    tmp_path,
    monkeypatch,
    position,
    returncode,
):
    """Stop on the first unsuccessful install using success rather than the exit code."""
    isolate = LocalIsolate(Lock(), tmp_path)
    packages = ('one', 'two', 'three')
    run = Mock(
        side_effect=[SimpleRunResult(True)] * position
        + [SimpleRunResult(False, returncode)],
    )
    monkeypatch.setattr(isolate, 'run', run)

    with pytest.raises(CannotInstallDependencyError):
        isolate.install(*packages)

    assert run.call_args_list == [
        call(f'pip install {package}') for package in packages[: position + 1]
    ]


@pytest.mark.parametrize('position', [0, 1, 2])
@pytest.mark.parametrize('error_type', [RuntimeError, OSError, KeyboardInterrupt])
def test_install_preserves_execution_exception(
    tmp_path,
    monkeypatch,
    position,
    error_type,
):
    """Propagate installation execution errors without trying later packages."""
    isolate = LocalIsolate(Lock(), tmp_path)
    error = error_type('installer failed')
    run = Mock(side_effect=[SimpleRunResult(True)] * position + [error])
    monkeypatch.setattr(isolate, 'run', run)

    with pytest.raises(error_type) as caught:
        isolate.install('one', 'two', 'three')

    assert caught.value is error
    assert run.call_count == position + 1
