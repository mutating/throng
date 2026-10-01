from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock, Mock, call

import pytest
from cantok import DefaultToken, SimpleToken

from throng.abstracts.abstract_manager import AbstractManager, ContextIsolateManager
from throng.abstracts.results import SimpleRunResult
from throng.errors import (
    CannotCancelNonExistingIsolateError,
    PreparationCommandFailedError,
)
from throng.extensions.local.manager import LocalManager
from throng.extensions.temporary_directory.manager import TemporaryDirectoryManager


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


@pytest.mark.parametrize('implemented', [(), ('read',), ('get',), ('read', 'get')])
def test_manager_requires_read_and_get(implemented):
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
    """Propagate creation failures unchanged and leave cleanup to the failed constructor."""
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
