from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from threading import Event
from types import MethodType
from unittest.mock import MagicMock, Mock, call

import pytest
from cantok import DefaultToken, SimpleToken

from throng.abstracts.abstract_isolate import AbstractIsolate
from throng.abstracts.abstract_manager import AbstractManager, ContextIsolateManager
from throng.abstracts.results import SimpleRunResult
from throng.errors import (
    CannotCancelNonExistingIsolateError,
    NotSupportedCommandError,
    PreparationCommandFailedError,
)
from throng.extensions.local.manager import LocalManager
from throng.extensions.temporary_directory.manager import TemporaryDirectoryManager


@pytest.mark.parametrize('form', ['omitted', 'none', 'empty'])
def test_absent_preparation_does_not_execute_or_destroy(form):
    """Keep an isolate alive without running commands when no preparation is needed."""
    isolate = Mock(spec=AbstractIsolate)
    isolate.chain = MethodType(AbstractIsolate.chain, isolate)
    options = {'omitted': {}, 'none': {'prepare': None}, 'empty': {'prepare': []}}

    manager = Mock(spec=AbstractManager)
    AbstractManager.__init__(manager, '.', **options[form])
    manager._get.return_value = isolate

    assert AbstractManager.get(manager, b'snapshot') is isolate
    manager._get.assert_called_once_with(b'snapshot')

    assert isolate.mock_calls == []
    assert options['empty']['prepare'] == []


@pytest.mark.parametrize(
    'commands',
    [['one'], ['first', 'second', 'first'], ['', ' ', 'Привет', 'a\nb', '"a b"; x']],
)
@pytest.mark.parametrize('returncode', [0, 7, None])
def test_successful_preparation_preserves_commands_and_keeps_isolate_alive(commands, returncode):
    """Execute unchanged commands in order and trust success rather than exit codes."""
    original_commands = commands.copy()
    isolate = Mock(spec=AbstractIsolate)
    manager = Mock(spec=AbstractManager, prepare=commands)
    manager._get.return_value = isolate
    isolate.chain = MethodType(AbstractIsolate.chain, isolate)
    results = [SimpleRunResult(True, returncode, command, 'diagnostic') for command in commands]
    isolate.run.side_effect = results

    assert AbstractManager.get(manager, b'snapshot') is isolate

    token = isolate.run.call_args.kwargs['token']
    assert isinstance(token, DefaultToken)
    assert isolate.mock_calls == [call.run(command, token=token) for command in original_commands]
    assert commands == original_commands
    assert [(result.success, result.returncode, result.stdout, result.stderr) for result in results] == [
        (True, returncode, command, 'diagnostic') for command in original_commands
    ]


@pytest.mark.parametrize('blocked_command', ['first', 'last'])
def test_get_waits_until_preparation_has_finished(blocked_command):
    """Keep get blocked until every preparation command has returned."""
    isolate = Mock(spec=AbstractIsolate)
    manager = Mock(spec=AbstractManager, prepare=['first', 'last'])
    manager._get.return_value = isolate
    isolate.chain = MethodType(AbstractIsolate.chain, isolate)
    entered, release = Event(), Event()

    def execute(command, **_kwargs):
        if command == blocked_command:
            entered.set()
            assert release.wait(5)
        return SimpleRunResult(True)

    isolate.run.side_effect = execute
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(AbstractManager.get, manager, b'snapshot')
        try:
            assert entered.wait(5)
            assert not future.done()
        finally:
            release.set()
        assert future.result(timeout=5) is isolate

    assert [entry.args[0] for entry in isolate.run.call_args_list] == ['first', 'last']
    isolate.kill.assert_not_called()


@pytest.mark.parametrize('failed_at', [0, 1, 2, 'all'])
@pytest.mark.parametrize('returncode', [0, 1, -9, None])
def test_unsuccessful_preparation_finishes_chain_then_cleans_up(failed_at, returncode):
    """Report the complete ordered results and clean up once before raising."""
    commands = ['first', 'second', 'third']
    isolate = Mock(spec=AbstractIsolate)
    manager = Mock(spec=AbstractManager, prepare=commands)
    manager._get.return_value = isolate
    isolate.chain = MethodType(AbstractIsolate.chain, isolate)
    results = [
        SimpleRunResult(failed_at not in (index, 'all'), returncode, f'out-{index}', f'err-{index}')
        for index in range(3)
    ]
    isolate.run.side_effect = results

    with pytest.raises(PreparationCommandFailedError) as caught:
        AbstractManager.get(manager, b'snapshot')

    token = isolate.run.call_args.kwargs['token']
    assert isinstance(token, DefaultToken)
    assert isolate.mock_calls == [call.run(command, token=token) for command in commands] + [call.kill()]
    assert commands == ['first', 'second', 'third']
    assert len(caught.value.results) == len(results)
    assert all(actual is original for actual, original in zip(caught.value.results, results))
    assert [(result.success, result.returncode, result.stdout, result.stderr) for result in results] == [
        (failed_at not in (index, 'all'), returncode, f'out-{index}', f'err-{index}')
        for index in range(3)
    ]
    assert caught.value.message
    assert caught.value.__cause__ is None


@pytest.mark.parametrize('success', [False, True])
def test_preparation_uses_success_instead_of_result_truthiness(success):
    """Interpret only the protocol's success flag, never the result object's truthiness."""
    isolate = Mock(spec=AbstractIsolate)
    manager = Mock(spec=AbstractManager, prepare=['prepare'])
    manager._get.return_value = isolate
    result = MagicMock(success=success)
    result.__bool__.side_effect = AssertionError('Result truthiness must not be inspected.')
    isolate.chain.return_value = [result]

    if success:
        assert AbstractManager.get(manager, b'snapshot') is isolate
        isolate.kill.assert_not_called()
    else:
        with pytest.raises(PreparationCommandFailedError) as caught:
            AbstractManager.get(manager, b'snapshot')
        assert caught.value.results[0] is result
        isolate.kill.assert_called_once_with()
    result.__bool__.assert_not_called()


@pytest.mark.parametrize('position', [0, 1, 2])
@pytest.mark.parametrize('error_type', [RuntimeError, OSError, NotSupportedCommandError])
def test_preparation_exception_stops_execution_and_preserves_cause(position, error_type):
    """Stop on an executor exception, release resources and expose the original cause."""
    commands = ['first', 'second', 'third']
    isolate = Mock(spec=AbstractIsolate)
    manager = Mock(spec=AbstractManager, prepare=commands)
    manager._get.return_value = isolate
    isolate.chain = MethodType(AbstractIsolate.chain, isolate)
    original = error_type('execution failed')
    isolate.run.side_effect = [SimpleRunResult(True)] * position + [original]

    with pytest.raises(PreparationCommandFailedError) as caught:
        AbstractManager.get(manager, b'snapshot')

    token = isolate.run.call_args.kwargs['token']
    assert isolate.mock_calls == [
        call.run(command, token=token) for command in commands[:position + 1]
    ] + [call.kill()]
    assert caught.value.__cause__ is original
    assert caught.value.__suppress_context__ is True
    assert caught.value.results == []
    assert caught.value.message
    assert commands == ['first', 'second', 'third']


@pytest.mark.parametrize('position', [0, 1, 2])
@pytest.mark.parametrize('error_type', [KeyboardInterrupt, SystemExit, GeneratorExit, BaseException])
def test_preparation_interruption_cleans_up_and_keeps_original_exception(position, error_type):
    """Preserve process-control exceptions by identity after releasing resources."""
    commands = ['first', 'second', 'third']
    isolate = Mock(spec=AbstractIsolate)
    manager = Mock(spec=AbstractManager, prepare=commands)
    manager._get.return_value = isolate
    isolate.chain = MethodType(AbstractIsolate.chain, isolate)
    original = error_type('interrupted')
    isolate.run.side_effect = [SimpleRunResult(True)] * position + [original]

    with pytest.raises(error_type) as caught:
        AbstractManager.get(manager, b'snapshot')

    token = isolate.run.call_args.kwargs['token']
    assert isolate.mock_calls == [
        call.run(command, token=token) for command in commands[:position + 1]
    ] + [call.kill()]
    assert caught.value is original
    assert caught.value.args == ('interrupted',)
    assert caught.value.__cause__ is None


def test_custom_chain_results_are_preserved_without_reexecuting_commands():
    """Allow plugins to override chain and keep their full result list on failure."""
    isolate = Mock(spec=AbstractIsolate)
    manager = Mock(spec=AbstractManager, prepare=['same', 'same'])
    manager._get.return_value = isolate
    results = [Mock(success=True, extra=object()), Mock(success=False, extra=object())]
    isolate.chain.return_value = results

    with pytest.raises(PreparationCommandFailedError) as caught:
        AbstractManager.get(manager, b'snapshot')

    assert caught.value.results is results
    assert isolate.mock_calls == [call.chain('same', 'same'), call.kill()]


def test_custom_chain_exception_is_wrapped_with_its_original_diagnostics():
    """Keep a plugin-specific preparation error as the cause instead of discarding it."""
    isolate = Mock(spec=AbstractIsolate)
    manager = Mock(spec=AbstractManager, prepare=['prepare'])
    manager._get.return_value = isolate
    results = [SimpleRunResult(False, 9, '', 'plugin diagnostic')]
    original = PreparationCommandFailedError('plugin failed', results)
    isolate.chain.side_effect = original

    with pytest.raises(PreparationCommandFailedError) as caught:
        AbstractManager.get(manager, b'snapshot')

    assert caught.value is not original
    assert caught.value.__cause__ is original
    assert original.results is results
    assert original.message == 'plugin failed'
    assert isolate.mock_calls == [call.chain('prepare'), call.kill()]


@pytest.mark.parametrize('failure', ['result', 'exception'])
def test_cleanup_errors_are_not_swallowed(failure):
    """Expose cleanup failure and retain the execution exception as its context."""
    isolate = Mock(spec=AbstractIsolate)
    manager = Mock(spec=AbstractManager, prepare=['prepare'])
    manager._get.return_value = isolate
    isolate.chain = MethodType(AbstractIsolate.chain, isolate)
    original = RuntimeError('execution failed') if failure == 'exception' else None
    isolate.run.side_effect = original
    isolate.run.return_value = SimpleRunResult(False)
    cleanup_error = OSError('cleanup failed')
    isolate.kill.side_effect = cleanup_error

    with pytest.raises(OSError, match='cleanup failed') as caught:
        AbstractManager.get(manager, b'snapshot')

    assert caught.value is cleanup_error
    assert caught.value.__context__ is original
    isolate.run.assert_called_once()
    isolate.kill.assert_called_once_with()


@pytest.mark.parametrize('failure', ['result', 'exception'])
def test_preparation_failures_do_not_share_results_with_other_isolates(failure):
    """Keep diagnostic lists independent and allow a later isolate to prepare normally."""
    errors = []
    for _ in range(2):
        isolate = Mock(spec=AbstractIsolate)
        manager = Mock(spec=AbstractManager, prepare=['prepare'])
        manager._get.return_value = isolate
        isolate.chain = MethodType(AbstractIsolate.chain, isolate)
        isolate.run.return_value = SimpleRunResult(False)
        if failure == 'exception':
            isolate.run.side_effect = RuntimeError('execution failed')
        with pytest.raises(PreparationCommandFailedError) as caught:
            AbstractManager.get(manager, b'snapshot')
        errors.append(caught.value)

    errors[0].results.append(SimpleRunResult(True))
    assert len(errors[1].results) == (1 if failure == 'result' else 0)
    if failure == 'result':
        assert errors[0].results[0] is not errors[1].results[0]
    fresh = Mock(spec=AbstractIsolate)
    fresh.chain = MethodType(AbstractIsolate.chain, fresh)
    fresh.run.return_value = SimpleRunResult(True)

    manager._get.return_value = fresh

    assert AbstractManager.get(manager, b'snapshot') is fresh

    fresh.run.assert_called_once()
    fresh.kill.assert_not_called()


@pytest.mark.parametrize('path_kind', ['string', 'path', 'absolute'])
@pytest.mark.parametrize('name', ['.', 'missing', 'space here/каталог'])
@pytest.mark.parametrize('exclude', [None, [], ['cache/', '*.tmp', '!keep.tmp']])
def test_initialization_is_lazy(tmp_path, monkeypatch, path_kind, name, exclude):
    """Store source settings without reading or creating the source directory."""
    monkeypatch.chdir(tmp_path)
    path = {'string': name, 'path': Path(name), 'absolute': tmp_path / name}[path_kind]
    expected_exclude = None if exclude is None else exclude.copy()

    manager = TemporaryDirectoryManager(path, exclude)

    assert manager.path == Path(path)
    assert manager.exclude == expected_exclude
    assert list(tmp_path.iterdir()) == []


def test_default_exclusions_are_absent():
    """Leave file exclusions unset when callers omit them."""
    assert TemporaryDirectoryManager('.').exclude is None


@pytest.mark.parametrize('as_string', [False, True])
@pytest.mark.parametrize('prepare', [None, [], ['first', 'second', 'first']])
def test_initialization_stores_preparation_without_executing(as_string, prepare):
    """Store preparation independently of path normalization, without doing any work."""
    manager = Mock(spec=AbstractManager)
    path = Path('not created') / 'каталог'
    expected = None if prepare is None else prepare.copy()

    AbstractManager.__init__(manager, str(path) if as_string else path, prepare=prepare)

    assert manager.path == path
    assert manager.exclude is None
    assert manager.prepare == expected
    assert prepare == expected
    assert manager.mock_calls == []


def test_default_preparation_is_absent():
    """Keep preparation optional for managers that inherit the base constructor."""
    manager = Mock(spec=AbstractManager)

    AbstractManager.__init__(manager, '.')

    assert manager.prepare is None
    assert manager.mock_calls == []


@pytest.mark.parametrize(
    ('manager_type', 'name'),
    [
        (LocalManager, 'LocalManager'),
        (TemporaryDirectoryManager, 'TemporaryDirectoryManager'),
    ],
)
@pytest.mark.parametrize(
    ('path', 'exclude', 'expected'),
    [
        ('.', None, "('.')"),
        ('.', [], "('.', exclude=[])"),
        (
            'каталог',
            ['*.tmp'],
            "('каталог', exclude=['*.tmp'])",
        ),
    ],
)
def test_representation_includes_explicit_settings(
    manager_type,
    name,
    path,
    exclude,
    expected,
):
    """Show the concrete manager, source path and explicitly supplied exclusions."""
    assert repr(manager_type(path, exclude)) == name + expected


@pytest.mark.parametrize('implemented', [(), ('read',), ('_get',), ('read', '_get')])
def test_manager_requires_read_and_creation_hook(implemented):
    """Reject incomplete plugins while allowing managers implementing both operations."""
    manager_type = type(
        'Manager',
        (AbstractManager,),
        {name: Mock() for name in implemented},
    )

    if len(implemented) == 2:
        assert isinstance(manager_type('.'), AbstractManager)
    else:
        with pytest.raises(TypeError, match='abstract'):
            manager_type('.')


@pytest.mark.parametrize('prepare', [None, [], ['prepare']])
def test_scope_is_lazy_and_independent(monkeypatch, prepare):
    """Allocate a fresh context on demand without creating an isolate early."""
    manager = TemporaryDirectoryManager('.', prepare=prepare)
    read, get = Mock(), Mock()
    monkeypatch.setattr(manager, 'read', read)
    monkeypatch.setattr(manager, 'get', get)

    first, second = manager.scope, manager.scope

    assert first is not second
    assert first.manager is second.manager is manager
    assert first.isolate is second.isolate is None
    read.assert_not_called()
    get.assert_not_called()


@pytest.mark.parametrize('operation', ['scope', 'run', 'chain'])
@pytest.mark.parametrize('failure', ['preparation', 'interrupt', 'exit'])
def test_failed_preparation_never_exposes_an_isolate(monkeypatch, operation, failure):
    """Propagate creation failures unchanged and leave cleanup to get."""
    manager = TemporaryDirectoryManager('.', prepare=['setup'])
    results = [SimpleRunResult(False, 1, '', 'setup failed')]
    error = {
        'preparation': PreparationCommandFailedError('setup failed', results),
        'interrupt': KeyboardInterrupt(),
        'exit': SystemExit(2),
    }[failure]
    events = Mock()
    events.read.return_value = b'snapshot'
    events.get.side_effect = error
    monkeypatch.setattr(manager, 'read', events.read)
    monkeypatch.setattr(manager, 'get', events.get)
    context = manager.scope
    expectation = pytest.raises(type(error))

    if operation == 'scope':
        with expectation as caught, context:
            pytest.fail('Failed preparation must prevent entry.')
        assert context.isolate is None
    else:
        with expectation as caught:
            getattr(manager, operation)('must not run')

    assert caught.value is error
    assert events.mock_calls == [call.read(), call.get(b'snapshot')]
    events.get.return_value.run.assert_not_called()
    events.get.return_value.chain.assert_not_called()
    events.get.return_value.kill.assert_not_called()
    if failure == 'preparation':
        assert caught.value.results is results


@pytest.mark.parametrize('state', [b'', b'snapshot', b'\x00\xff'])
def test_context_passes_opaque_state(state):
    """Create the isolate from the exact snapshot returned by the manager."""
    manager = Mock()
    manager.read.return_value = state
    context = ContextIsolateManager(manager)

    isolate = context.__enter__()

    assert manager.mock_calls == [call.read(), call.get(state)]
    assert isolate is context.isolate is manager.get.return_value
    context.__exit__(None, None, None)
    isolate.kill.assert_called_once_with()


@pytest.mark.parametrize('stage', ['read', 'get'])
def test_failed_context_entry_preserves_error(stage):
    """Stop construction immediately when reading or creating an isolate fails."""
    manager = Mock()
    error = OSError('creation failed')
    getattr(manager, stage).side_effect = error
    context = ContextIsolateManager(manager)

    with pytest.raises(OSError, match='creation failed') as caught, context:
        pytest.fail('The context body must not be entered.')

    assert caught.value is error
    assert context.isolate is None
    assert manager.mock_calls == [call.read()] + (
        [call.get(manager.read.return_value)] if stage == 'get' else []
    )


@pytest.mark.parametrize(
    'error_type',
    [None, ValueError, KeyboardInterrupt, SystemExit],
)
@pytest.mark.parametrize('truthy_isolate', [False, True])
def test_context_cleans_up_on_every_exit(error_type, truthy_isolate):
    """Clean up even a false-valued isolate without suppressing body exceptions."""
    manager = Mock()
    isolate = MagicMock()
    isolate.__bool__.return_value = truthy_isolate
    manager.get.return_value = isolate
    error = error_type('body failed') if error_type else None

    expectation = pytest.raises(error_type) if error_type else nullcontext()
    with expectation as caught, ContextIsolateManager(manager) as actual:
        assert actual is isolate
        if error is not None:
            raise error

    if error is not None:
        assert caught.value is error
    isolate.kill.assert_called_once_with()


@pytest.mark.parametrize('failed_stage', [None, 'read', 'get'])
def test_exit_without_isolate_is_rejected(failed_stage):
    """Explain why a context without a successfully created isolate cannot close."""
    manager = Mock()
    context = ContextIsolateManager(manager)
    if failed_stage:
        getattr(manager, failed_stage).side_effect = OSError('entry failed')
        with pytest.raises(OSError, match='entry failed'):
            context.__enter__()

    with pytest.raises(CannotCancelNonExistingIsolateError, match="haven't entered"):
        context.__exit__(None, None, None)

    manager.get.return_value.kill.assert_not_called()


@pytest.mark.parametrize('nested', [False, True])
def test_contexts_manage_separate_isolates(monkeypatch, nested):
    """Keep separate scopes independent and close nested isolates in reverse order."""
    manager = TemporaryDirectoryManager('.')
    events = Mock()
    first, second = Mock(), Mock()
    events.attach_mock(first, 'first')
    events.attach_mock(second, 'second')
    read = Mock(side_effect=[b'first', b'second'])
    get = Mock(side_effect=[first, second])
    monkeypatch.setattr(manager, 'read', read)
    monkeypatch.setattr(manager, 'get', get)

    with manager.scope as outer:
        assert outer is first
        if nested:
            with manager.scope as inner:
                assert inner is second
            first.kill.assert_not_called()
    if not nested:
        with manager.scope as later:
            assert later is second

    assert read.call_count == 2
    assert get.call_args_list == [call(b'first'), call(b'second')]
    assert events.mock_calls == (
        [call.second.kill(), call.first.kill()]
        if nested
        else [call.first.kill(), call.second.kill()]
    )


@pytest.mark.parametrize(
    ('method', 'commands'),
    [
        ('run', ('',)),
        ('run', (' \t世界\n"a b"; x\n',)),
        ('chain', ()),
        ('chain', ('one',)),
        ('chain', ('one', '', ' two\n', 'one')),
    ],
)
@pytest.mark.parametrize('token_kind', ['default', 'active', 'cancelled'])
@pytest.mark.parametrize(
    'outcome',
    [
        (False, None, None, None),
        (True, 0, '\n世界\n', ' warning\n'),
        (False, 0, '', ''),
        (True, 1, '', 'diagnostic'),
    ],
)
def test_closed_execution_delegates_and_cleans_up(
    monkeypatch,
    method,
    commands,
    token_kind,
    outcome,
):
    """Delegate unchanged commands and tokens, returning the result only after cleanup."""
    manager = TemporaryDirectoryManager('.')
    events = Mock()
    isolate = events.isolate
    events.read.return_value = b'\xffstate'
    events.get.return_value = isolate
    success, returncode, stdout, stderr = outcome
    fields = {
        'success': success,
        'returncode': returncode,
        'stdout': stdout,
        'stderr': stderr,
        'extra': object(),
    }
    result = Mock(**fields)
    expected = result if method == 'run' else [result for _ in commands]
    getattr(isolate, method).return_value = expected
    monkeypatch.setattr(manager, 'read', events.read)
    monkeypatch.setattr(manager, 'get', events.get)
    token = SimpleToken(cancelled=token_kind == 'cancelled')

    actual = getattr(manager, method)(
        *commands,
        **({} if token_kind == 'default' else {'token': token}),
    )

    passed_token = getattr(isolate, method).call_args.kwargs['token']
    if token_kind == 'default':
        assert isinstance(passed_token, DefaultToken)
    else:
        assert passed_token is token
    assert actual is expected
    for result in [actual] if method == 'run' else actual:
        assert {name: getattr(result, name) for name in fields} == fields
    assert events.mock_calls == [
        call.read(),
        call.get(b'\xffstate'),
        getattr(call.isolate, method)(*commands, token=passed_token),
        call.isolate.kill(),
    ]


@pytest.mark.parametrize(
    'outcomes',
    [
        ((True, 0), (False, 1), (False, None)),
        ((False, None), (True, 0), (False, 1)),
    ],
)
def test_chain_preserves_mixed_plugin_results(monkeypatch, outcomes):
    """Return successful, failed and skipped plugin results unchanged and in order.

    Save the original order separately to detect mutations of the returned list.
    """
    manager = TemporaryDirectoryManager('.')
    isolate = Mock()
    originals = tuple(
        Mock(success=success, returncode=code, extra=object())
        for success, code in outcomes
    )
    extras = [result.extra for result in originals]
    expected = list(originals)
    isolate.chain.return_value = expected
    monkeypatch.setattr(manager, 'read', Mock(return_value=b'state'))
    monkeypatch.setattr(manager, 'get', Mock(return_value=isolate))

    actual = manager.chain('first', 'second', 'third')

    assert actual is expected
    assert len(actual) == len(originals)
    assert all(result is original for result, original in zip(actual, originals))
    assert [(result.success, result.returncode) for result in actual] == list(outcomes)
    assert [result.extra for result in actual] == extras
    isolate.kill.assert_called_once_with()


@pytest.mark.parametrize('method', ['run', 'chain'])
@pytest.mark.parametrize('stage', ['read', 'get', 'execute', 'kill'])
def test_closed_execution_errors_and_retry(monkeypatch, method, stage):
    """Propagate stage errors, clean up created isolates and allow a fresh retry."""
    manager = TemporaryDirectoryManager('.')
    events = Mock()
    isolate = events.isolate
    events.read.return_value = b'state'
    events.get.return_value = isolate
    monkeypatch.setattr(manager, 'read', events.read)
    monkeypatch.setattr(manager, 'get', events.get)
    target = {
        'read': events.read,
        'get': events.get,
        'execute': getattr(isolate, method),
        'kill': isolate.kill,
    }[stage]
    error = OSError('stage failed')
    target.side_effect = error

    with pytest.raises(OSError, match='stage failed') as caught:
        getattr(manager, method)('command')

    assert caught.value is error
    expected_calls = [call.read()]
    if stage != 'read':
        expected_calls.append(call.get(b'state'))
    if stage in ('execute', 'kill'):
        passed_token = getattr(isolate, method).call_args.kwargs['token']
        expected_calls.extend(
            [
                getattr(call.isolate, method)('command', token=passed_token),
                call.isolate.kill(),
            ],
        )
    assert events.mock_calls == expected_calls
    events.reset_mock()
    target.side_effect = None
    replacement = Mock()
    events.attach_mock(replacement, 'replacement')
    events.get.return_value = replacement

    result = getattr(manager, method)('retry')

    assert result is getattr(replacement, method).return_value
    passed_token = getattr(replacement, method).call_args.kwargs['token']
    assert events.mock_calls == [
        call.read(),
        call.get(b'state'),
        getattr(call.replacement, method)('retry', token=passed_token),
        call.replacement.kill(),
    ]


@pytest.mark.parametrize(
    ('first_method', 'second_method'),
    [('run', 'run'), ('chain', 'chain'), ('run', 'chain'), ('chain', 'run')],
)
def test_closed_calls_read_fresh_state(monkeypatch, first_method, second_method):
    """Create a new isolate from fresh state for each independent manager call."""
    manager = TemporaryDirectoryManager('.')
    first, second = Mock(), Mock()
    read = Mock(side_effect=[b'old', b'new'])
    get = Mock(side_effect=[first, second])
    monkeypatch.setattr(manager, 'read', read)
    monkeypatch.setattr(manager, 'get', get)

    assert (
        getattr(manager, first_method)('one')
        is getattr(first, first_method).return_value
    )
    assert (
        getattr(manager, second_method)('two')
        is getattr(second, second_method).return_value
    )
    assert read.call_count == 2
    assert get.call_args_list == [call(b'old'), call(b'new')]
    first.kill.assert_called_once_with()
    second.kill.assert_called_once_with()
