from unittest.mock import MagicMock, Mock, call

import pytest
from cantok import DefaultToken, SimpleToken

from throng.abstracts.abstract_isolate import AbstractIsolate
from throng.abstracts.results import SimpleRunResult
from throng.errors import NotSupportedCommandError


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
