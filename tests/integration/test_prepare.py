import shlex
import sys
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from pathlib import Path
from shutil import rmtree
from threading import Event

import pytest

from throng import temporary_directory, throng
from throng.abstracts.results import SimpleRunResult
from throng.errors import PreparationCommandFailedError


def run_python_command(source):
    return shlex.join([Path(sys.executable).as_posix(), '-c', source])


@pytest.fixture
def recorded_executions(monkeypatch, builtin_plugin_name):
    module = import_module(f'throng.extensions.{builtin_plugin_name}.isolate')
    original = module.run
    calls = []

    def execute(command, **kwargs):
        result = original(command, **kwargs)
        calls.append((command, kwargs['directory'], result))
        return result

    monkeypatch.setattr(module, 'run', execute)
    return calls


@pytest.mark.parametrize('factory', ['slot', 'direct'])
@pytest.mark.parametrize('setting', ['omitted', 'none', 'empty'])
def test_optional_prepare(tmp_path, builtin_plugin_name, factory, setting, recorded_executions):
    """Accept omitted, None and empty preparation without executing anything."""
    options = {'omitted': {}, 'none': {'prepare': None}, 'empty': {'prepare': []}}[
        setting
    ]
    if factory == 'slot':
        manager = throng(tmp_path, **options)[builtin_plugin_name]
    else:
        manager = getattr(import_module('throng.extensions.plugins'), builtin_plugin_name)(
            tmp_path, **options,
        )
    state = manager.read()
    isolate = manager.get(state)
    try:
        assert recorded_executions == []
        assert list(isolate.path.iterdir()) == []
    finally:
        isolate.kill()


@pytest.mark.parametrize('factory', ['slot', 'direct'])
@pytest.mark.parametrize('operation', ['get', 'scope', 'run', 'chain'])
def test_prepare_finishes_in_order_before_use(
    tmp_path, builtin_plugin_name, factory, operation, recorded_executions,
):
    """Finish ordered preparation in the isolate before returning it or running user commands."""
    source = tmp_path / 'source with spaces'
    source.mkdir()
    (source / 'seed').write_text('initial')
    (source / 'ignored.tmp').write_text('excluded')
    first = run_python_command(
        "from pathlib import Path; Path('prepared').write_text(Path('seed').read_text() + ':1')",
    )
    append = run_python_command(
        "from pathlib import Path; p = Path('prepared'); p.write_text(p.read_text() + ':2')",
    )
    prepare = [first, append, append]
    original_prepare = list(prepare)
    if factory == 'slot':
        manager = throng(source, ['*.tmp'], prepare)[builtin_plugin_name]
    else:
        manager = getattr(import_module('throng.extensions.plugins'), builtin_plugin_name)(
            source, prepare=prepare, exclude=['*.tmp'],
        )
    state = manager.read()
    assert recorded_executions == []
    assert not (source / 'prepared').exists()
    read = run_python_command(
        "from pathlib import Path; print(Path('prepared').read_text())",
    )

    def check_isolate(isolate):
        assert [command for command, _, _ in recorded_executions] == prepare
        assert (isolate.path / 'prepared').read_text() == 'initial:1:2:2'
        assert (isolate.path / 'ignored.tmp').exists() is (builtin_plugin_name == 'local')

    if operation == 'get':
        isolate = manager.get(state)
        try:
            check_isolate(isolate)
            result = isolate.run(read)
        finally:
            isolate.kill()
    elif operation == 'scope':
        with manager.scope as isolate:
            check_isolate(isolate)
            result = isolate.run(read)
    elif operation == 'run':
        result = manager.run(read)
    else:
        results = manager.chain(read, read)
        assert len(results) == 2
        assert all(result.stdout == 'initial:1:2:2\n' for result in results)
        result = results[-1]
    assert result.success
    assert result.stdout == 'initial:1:2:2\n'
    assert [command for command, _, _ in recorded_executions] == prepare + [read] * (
        2 if operation == 'chain' else 1
    )
    assert all(result.success for _, _, result in recorded_executions)
    assert len({directory for _, directory, _ in recorded_executions}) == 1
    directory = recorded_executions[0][1]
    assert directory.exists() is (builtin_plugin_name == 'local')
    assert (source / 'prepared').exists() is (builtin_plugin_name == 'local')
    assert prepare == original_prepare


def test_prepare_runs_for_each_new_isolate(tmp_path, builtin_plugin_name, recorded_executions):
    """Prepare each new isolate once without rerunning preparation for later commands."""
    command = run_python_command(
        "from pathlib import Path; p = Path('count'); p.write_text((p.read_text() if p.exists() else '') + 'x')",
    )
    manager = throng(tmp_path, prepare=[command])[builtin_plugin_name]
    state = manager.read()
    first = manager.get(state)
    try:
        assert (first.path / 'count').read_text() == 'x'
        second = manager.get(state)
        try:
            assert second is not first
            assert (second.path / 'count').read_text() == (
                'xx' if builtin_plugin_name == 'local' else 'x'
            )
            assert [entry[0] for entry in recorded_executions] == [command, command]
            first.run(run_python_command('pass'))
            assert len(recorded_executions) == 3
        finally:
            second.kill()
    finally:
        first.kill()


@pytest.mark.parametrize('blocked_command', ['first', 'last'])
def test_get_waits_for_prepare_completion(
    tmp_path, monkeypatch, builtin_plugin_name, blocked_command,
):
    """Keep get blocked until the first and last preparation commands have finished."""
    entered, release = Event(), Event()
    calls = []

    def execute(command, **_kwargs):
        if command == blocked_command:
            entered.set()
            assert release.wait(5)
        calls.append(command)
        return SimpleRunResult(True)

    monkeypatch.setattr(f'throng.extensions.{builtin_plugin_name}.isolate.run', execute)
    manager = throng(tmp_path, prepare=['first', 'last'])[builtin_plugin_name]
    state = manager.read()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(manager.get, state)
        try:
            assert entered.wait(5)
            assert not future.done()
        finally:
            release.set()
            isolate = future.result(timeout=5)
        try:
            assert calls == ['first', 'last']
        finally:
            isolate.kill()


def test_prepare_uses_supplied_snapshot(tmp_path, builtin_plugin_name):
    """Prepare restored snapshot contents while the local plugin uses current files."""
    (tmp_path / 'seed').write_text('saved')
    prepare = [
        run_python_command(
            "from pathlib import Path; Path('prepared').write_text(Path('seed').read_text())",
        ),
    ]
    manager = throng(tmp_path, prepare=prepare)[builtin_plugin_name]
    state = manager.read()
    (tmp_path / 'seed').write_text('new')
    isolate = manager.get(state)
    try:
        assert (isolate.path / 'prepared').read_text() == (
            'new' if builtin_plugin_name == 'local' else 'saved'
        )
    finally:
        isolate.kill()


@pytest.mark.parametrize('failure', ['nonzero_exit', 'missing_executable'])
@pytest.mark.parametrize('operation', ['get', 'scope', 'run', 'chain'])
def test_failed_prepare_prevents_use(tmp_path, builtin_plugin_name, failure, operation, recorded_executions):
    """Reject failed preparation and expose every command result in order."""
    failed = (
        run_python_command('raise SystemExit(17)')
        if failure == 'nonzero_exit'
        else 'throng_nonexistent_prepare_command_6d5e16cc'
    )
    after = run_python_command(
        "from pathlib import Path; Path('continued').write_text('yes')",
    )
    manager = throng(tmp_path, prepare=[failed, after])[builtin_plugin_name]
    expectation = pytest.raises(PreparationCommandFailedError)
    if operation == 'get':
        with expectation as caught:
            manager.get(manager.read())
    elif operation == 'scope':
        with expectation as caught, manager.scope:
            pytest.fail('A failed preparation must prevent context entry.')
    else:
        with expectation as caught:
            getattr(manager, operation)('must not execute')
    assert [call[0] for call in recorded_executions] == [failed, after]
    assert recorded_executions[0][2].success is False
    if failure == 'nonzero_exit':
        assert recorded_executions[0][2].returncode == 17
    assert recorded_executions[1][2].success is True
    assert len(caught.value.results) == 2
    assert all(
        result is call[2] for result, call in zip(caught.value.results, recorded_executions)
    )


@pytest.mark.parametrize('operation', ['get', 'scope', 'run', 'chain'])
@pytest.mark.parametrize(
    'failure',
    [
        'nonzero_exit',
        'missing_executable',
        'empty_command',
        'unclosed_quote',
        'exception',
        'keyboard_interrupt',
        'system_exit',
    ],
)
def test_failed_prepare_removes_directory_before_propagating(
    tmp_path, monkeypatch, operation, failure,
):
    """Remove allocated resources even while the caller retains the failure traceback."""
    commands = {
        'nonzero_exit': run_python_command('raise SystemExit(17)'),
        'missing_executable': 'throng_nonexistent_prepare_command_6d5e16cc',
        'empty_command': '',
        'unclosed_quote': 'python "',
    }
    interruption = {
        'exception': RuntimeError('executor failed'),
        'keyboard_interrupt': KeyboardInterrupt(),
        'system_exit': SystemExit(1),
    }.get(failure)
    module = import_module('throng.extensions.temporary_directory.isolate')
    original_run = module.run
    directories = []

    def execute(command, **kwargs):
        directories.append(Path(kwargs['directory']))
        if interruption is not None:
            raise interruption
        return original_run(command, **kwargs)

    monkeypatch.setattr(module, 'run', execute)
    manager = temporary_directory(tmp_path, prepare=[commands.get(failure, 'prepare')])
    expected_type = (
        type(interruption)
        if failure in ('keyboard_interrupt', 'system_exit')
        else PreparationCommandFailedError
    )
    try:
        expectation = pytest.raises(expected_type)
        if operation == 'get':
            with expectation as caught:
                manager.get(manager.read())
        elif operation == 'scope':
            with expectation as caught, manager.scope:
                pytest.fail('A failed preparation must prevent context entry.')
        else:
            with expectation as caught:
                getattr(manager, operation)('must not execute')
        assert caught.value.__traceback__ is not None
        if interruption is not None:
            if failure == 'exception':
                assert caught.value.__cause__ is interruption
            else:
                assert caught.value is interruption
        assert len(directories) == 1
        assert not directories[0].exists(), (
            'Cleanup must happen before the caller receives the error.'
        )
    finally:
        for directory in directories:
            if directory.exists():
                rmtree(directory)
