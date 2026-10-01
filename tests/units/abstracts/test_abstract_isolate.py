from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import MethodType
from unittest.mock import MagicMock, Mock, call

import pytest
from cantok import DefaultToken, SimpleToken

from throng.abstracts.abstract_isolate import AbstractIsolate
from throng.abstracts.results import SimpleRunResult
from throng.errors import NotSupportedCommandError, PreparationCommandFailedError


@pytest.mark.parametrize('form', ['omitted', 'none', 'empty'])
def test_absent_preparation_does_not_execute_or_destroy(form):
    """Keep an isolate alive without running commands when no preparation is needed."""
    isolate = Mock(spec=AbstractIsolate)
    isolate.chain = MethodType(AbstractIsolate.chain, isolate)
    options = {'omitted': {}, 'none': {'prepare': None}, 'empty': {'prepare': []}}

    assert AbstractIsolate.__init__(isolate, **options[form]) is None

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
    isolate.chain = MethodType(AbstractIsolate.chain, isolate)
    results = [SimpleRunResult(True, returncode, command, 'diagnostic') for command in commands]
    isolate.run.side_effect = results

    assert AbstractIsolate.__init__(isolate, commands) is None

    token = isolate.run.call_args.kwargs['token']
    assert isinstance(token, DefaultToken)
    assert isolate.mock_calls == [call.run(command, token=token) for command in original_commands]
    assert commands == original_commands
    assert [(result.success, result.returncode, result.stdout, result.stderr) for result in results] == [
        (True, returncode, command, 'diagnostic') for command in original_commands
    ]


@pytest.mark.parametrize('blocked_command', ['first', 'last'])
def test_constructor_waits_until_preparation_has_finished(blocked_command):
    """Keep initialization blocked until every preparation command has returned."""
    isolate = Mock(spec=AbstractIsolate)
    isolate.chain = MethodType(AbstractIsolate.chain, isolate)
    entered, release = Event(), Event()

    def execute(command, **_kwargs):
        if command == blocked_command:
            entered.set()
            assert release.wait(5)
        return SimpleRunResult(True)

    isolate.run.side_effect = execute
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(AbstractIsolate.__init__, isolate, ['first', 'last'])
        try:
            assert entered.wait(5)
            assert not future.done()
        finally:
            release.set()
        assert future.result(timeout=5) is None

    assert [entry.args[0] for entry in isolate.run.call_args_list] == ['first', 'last']
    isolate.kill.assert_not_called()


@pytest.mark.parametrize('failed_at', [0, 1, 2, 'all'])
@pytest.mark.parametrize('returncode', [0, 1, -9, None])
def test_unsuccessful_preparation_finishes_chain_then_cleans_up(failed_at, returncode):
    """Report the complete ordered results and clean up once before raising."""
    commands = ['first', 'second', 'third']
    isolate = Mock(spec=AbstractIsolate)
    isolate.chain = MethodType(AbstractIsolate.chain, isolate)
    results = [
        SimpleRunResult(failed_at not in (index, 'all'), returncode, f'out-{index}', f'err-{index}')
        for index in range(3)
    ]
    isolate.run.side_effect = results

    with pytest.raises(PreparationCommandFailedError) as caught:
        AbstractIsolate.__init__(isolate, commands)

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
    result = MagicMock(success=success)
    result.__bool__.side_effect = AssertionError('Result truthiness must not be inspected.')
    isolate.chain.return_value = [result]

    if success:
        assert AbstractIsolate.__init__(isolate, ['prepare']) is None
        isolate.kill.assert_not_called()
    else:
        with pytest.raises(PreparationCommandFailedError) as caught:
            AbstractIsolate.__init__(isolate, ['prepare'])
        assert caught.value.results[0] is result
        isolate.kill.assert_called_once_with()
    result.__bool__.assert_not_called()


@pytest.mark.parametrize('position', [0, 1, 2])
@pytest.mark.parametrize('error_type', [RuntimeError, OSError, NotSupportedCommandError])
def test_preparation_exception_stops_execution_and_preserves_cause(position, error_type):
    """Stop on an executor exception, release resources and expose the original cause."""
    commands = ['first', 'second', 'third']
    isolate = Mock(spec=AbstractIsolate)
    isolate.chain = MethodType(AbstractIsolate.chain, isolate)
    original = error_type('execution failed')
    isolate.run.side_effect = [SimpleRunResult(True)] * position + [original]

    with pytest.raises(PreparationCommandFailedError) as caught:
        AbstractIsolate.__init__(isolate, commands)

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
    isolate.chain = MethodType(AbstractIsolate.chain, isolate)
    original = error_type('interrupted')
    isolate.run.side_effect = [SimpleRunResult(True)] * position + [original]

    with pytest.raises(error_type) as caught:
        AbstractIsolate.__init__(isolate, commands)

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
    results = [Mock(success=True, extra=object()), Mock(success=False, extra=object())]
    isolate.chain.return_value = results

    with pytest.raises(PreparationCommandFailedError) as caught:
        AbstractIsolate.__init__(isolate, ['same', 'same'])

    assert caught.value.results is results
    assert isolate.mock_calls == [call.chain('same', 'same'), call.kill()]


def test_custom_chain_exception_is_wrapped_with_its_original_diagnostics():
    """Keep a plugin-specific preparation error as the cause instead of discarding it."""
    isolate = Mock(spec=AbstractIsolate)
    results = [SimpleRunResult(False, 9, '', 'plugin diagnostic')]
    original = PreparationCommandFailedError('plugin failed', results)
    isolate.chain.side_effect = original

    with pytest.raises(PreparationCommandFailedError) as caught:
        AbstractIsolate.__init__(isolate, ['prepare'])

    assert caught.value is not original
    assert caught.value.__cause__ is original
    assert original.results is results
    assert original.message == 'plugin failed'
    assert isolate.mock_calls == [call.chain('prepare'), call.kill()]


@pytest.mark.parametrize('failure', ['result', 'exception'])
def test_cleanup_errors_are_not_swallowed(failure):
    """Expose cleanup failure and retain the execution exception as its context."""
    isolate = Mock(spec=AbstractIsolate)
    isolate.chain = MethodType(AbstractIsolate.chain, isolate)
    original = RuntimeError('execution failed') if failure == 'exception' else None
    isolate.run.side_effect = original
    isolate.run.return_value = SimpleRunResult(False)
    cleanup_error = OSError('cleanup failed')
    isolate.kill.side_effect = cleanup_error

    with pytest.raises(OSError, match='cleanup failed') as caught:
        AbstractIsolate.__init__(isolate, ['prepare'])

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
        isolate.chain = MethodType(AbstractIsolate.chain, isolate)
        isolate.run.return_value = SimpleRunResult(False)
        if failure == 'exception':
            isolate.run.side_effect = RuntimeError('execution failed')
        with pytest.raises(PreparationCommandFailedError) as caught:
            AbstractIsolate.__init__(isolate, ['prepare'])
        errors.append(caught.value)

    errors[0].results.append(SimpleRunResult(True))
    assert len(errors[1].results) == (1 if failure == 'result' else 0)
    if failure == 'result':
        assert errors[0].results[0] is not errors[1].results[0]
    fresh = Mock(spec=AbstractIsolate)
    fresh.chain = MethodType(AbstractIsolate.chain, fresh)
    fresh.run.return_value = SimpleRunResult(True)

    assert AbstractIsolate.__init__(fresh, ['prepare']) is None

    fresh.run.assert_called_once()
    fresh.kill.assert_not_called()


@pytest.mark.parametrize('token_kind', ['default', 'active', 'cancelled', 'unreadable'])
def test_empty_chain_does_not_inspect_token(token_kind):
    """Return a fresh empty list without execution or cancellation checks."""
    isolate = Mock(spec=AbstractIsolate)
    token = MagicMock()
    token.__bool__.side_effect = AssertionError('The token must not be inspected.')
    options = {
        'default': {},
        'active': {'token': SimpleToken()},
        'cancelled': {'token': SimpleToken(cancelled=True)},
        'unreadable': {'token': token},
    }

    first = AbstractIsolate.chain(isolate, **options[token_kind])
    second = AbstractIsolate.chain(isolate, **options[token_kind])

    assert first == second == []
    assert first is not second
    first.append(SimpleRunResult(True))
    assert second == []
    assert isolate.mock_calls == []
    token.__bool__.assert_not_called()


@pytest.mark.parametrize(
    'commands',
    [('one',), ('one', 'two', 'one'), ('', ' ', 'Привет', 'a\nb', '"a b"; x')],
)
@pytest.mark.parametrize('explicit_token', [False, True])
def test_chain_preserves_commands_results_and_token(commands, explicit_token):
    """Keep command order, plugin results and the same token throughout a chain."""
    isolate = Mock(spec=AbstractIsolate)
    fields = [
        {
            'success': True,
            'returncode': 0,
            'stdout': command,
            'stderr': ' diagnostic\n',
            'extra': object(),
        }
        for command in commands
    ]
    expected = [Mock(**values) for values in fields]
    isolate.run.side_effect = expected
    token = SimpleToken()

    results = AbstractIsolate.chain(
        isolate,
        *commands,
        **({'token': token} if explicit_token else {}),
    )

    passed_token = isolate.run.call_args.kwargs['token']
    if explicit_token:
        assert passed_token is token
    else:
        assert isinstance(passed_token, DefaultToken)
    assert isolate.mock_calls == [
        call.run(command, token=passed_token) for command in commands
    ]
    assert len(results) == len(expected)
    assert all(actual is original for actual, original in zip(results, expected))
    for result, values in zip(results, fields):
        assert {name: getattr(result, name) for name in values} == values


@pytest.mark.parametrize('failed_at', [0, 1, 2, 'all'])
@pytest.mark.parametrize('returncode', [0, 1, -9, None])
def test_chain_continues_after_unsuccessful_results(failed_at, returncode):
    """Leave stopping decisions to cancellation rather than command success."""
    isolate = Mock(spec=AbstractIsolate)
    expected = [
        SimpleRunResult(success=failed_at not in (index, 'all'), returncode=returncode)
        for index in range(3)
    ]
    isolate.run.side_effect = expected

    results = AbstractIsolate.chain(isolate, 'first', 'second', 'third')

    assert isolate.run.call_count == 3
    assert all(actual is original for actual, original in zip(results, expected))
    assert [
        (result.success, result.returncode, result.stdout, result.stderr)
        for result in results
    ] == [
        (failed_at not in (index, 'all'), returncode, None, None) for index in range(3)
    ]


@pytest.mark.parametrize(
    ('command_count', 'completed'),
    [(1, 0), (4, 0), (4, 1), (4, 2), (4, 4)],
)
@pytest.mark.parametrize('success', [False, True])
def test_chain_keeps_completed_results_when_cancelled(
    command_count,
    completed,
    success,
):
    """Keep completed results and mark every command skipped after cancellation."""
    commands = ('first', 'second', 'third', 'fourth')[:command_count]
    token = SimpleToken(cancelled=completed == 0)
    isolate = Mock(spec=AbstractIsolate)
    executed = []

    def execute(_command, *, token):
        result = SimpleRunResult(success, 0 if success else 1)
        executed.append(result)
        if len(executed) == completed:
            token.cancel()
        return result

    isolate.run.side_effect = execute

    results = AbstractIsolate.chain(isolate, *commands, token=token)

    assert isolate.mock_calls == [
        call.run(command, token=token) for command in commands[:completed]
    ]
    assert len(results) == len(commands)
    assert all(
        result is executed[index] for index, result in enumerate(results[:completed])
    )
    assert results[completed:] == [SimpleRunResult(False) for _ in commands[completed:]]
    assert (
        len({id(result) for result in results[completed:]}) == len(commands) - completed
    )


def test_skipped_results_are_independent():
    """Allow callers to update one skipped result without changing its neighbors."""
    isolate = Mock(spec=AbstractIsolate)

    results = AbstractIsolate.chain(
        isolate,
        'one',
        'two',
        token=SimpleToken(cancelled=True),
    )
    results[0].stdout = 'annotated'

    assert results[1].stdout is None
    assert isolate.mock_calls == []


@pytest.mark.parametrize('cancel_first', [False, True])
def test_chains_do_not_share_results_or_cancellation(cancel_first):
    """Start each chain independently while preserving repeated plugin objects."""
    isolate = Mock(spec=AbstractIsolate)
    expected = SimpleRunResult(True)
    isolate.run.return_value = expected

    first = AbstractIsolate.chain(
        isolate,
        'one',
        'two',
        token=SimpleToken(cancelled=cancel_first),
    )
    second = AbstractIsolate.chain(isolate, 'one', 'two')

    assert first is not second
    assert second[0] is expected
    assert second[1] is expected
    assert isolate.run.call_count == (2 if cancel_first else 4)


@pytest.mark.parametrize('position', [0, 1, 2])
@pytest.mark.parametrize(
    'error_type',
    [RuntimeError, NotSupportedCommandError, KeyboardInterrupt],
)
def test_chain_stops_on_execution_exception(position, error_type):
    """Stop at an execution exception and propagate the original cause."""
    isolate = Mock(spec=AbstractIsolate)
    error = error_type('execution failed')
    isolate.run.side_effect = [SimpleRunResult(True)] * position + [error]
    token = SimpleToken()
    commands = ('one', 'two', 'three')

    with pytest.raises(error_type) as caught:
        AbstractIsolate.chain(isolate, *commands, token=token)

    assert caught.value is error
    assert isolate.mock_calls == [
        call.run(command, token=token) for command in commands[: position + 1]
    ]


@pytest.mark.parametrize('completed', [0, 1])
def test_chain_stops_on_token_exception(completed):
    """Do not execute a command whose cancellation check failed."""
    isolate = Mock(spec=AbstractIsolate)
    token = MagicMock()
    error = RuntimeError('token failed')
    token.__bool__.side_effect = [True] * completed + [error]

    with pytest.raises(RuntimeError) as caught:
        AbstractIsolate.chain(isolate, 'one', 'two', token=token)

    assert caught.value is error
    assert isolate.run.call_count == completed
    isolate.kill.assert_not_called()


def test_destructor_delegates_cleanup():
    """Delegate final resource cleanup to the concrete isolate implementation."""
    isolate = Mock(spec=AbstractIsolate)

    AbstractIsolate.__del__(isolate)

    assert isolate.mock_calls == [call.kill()]


def test_isolate_requires_concrete_operations():
    """Require plugins to implement execution, snapshots, cleanup and installation."""
    assert AbstractIsolate.__abstractmethods__ == {'run', 'read', 'kill', 'install'}
    with pytest.raises(TypeError, match='abstract'):
        AbstractIsolate()


@pytest.mark.parametrize('explicit_init', [False, True])
def test_existing_isolate_subclasses_can_omit_prepare(explicit_init):
    """Keep legacy subclasses constructible, including a no-argument super call."""
    methods = {name: Mock() for name in ('run', 'read', 'kill', 'install')}
    if explicit_init:
        methods['__init__'] = lambda self: super(type(self), self).__init__()
    isolate_type = type('LegacyIsolate', (AbstractIsolate,), methods)

    isolate = isolate_type()

    methods['run'].assert_not_called()
    methods['kill'].assert_not_called()
    isolate.kill()
