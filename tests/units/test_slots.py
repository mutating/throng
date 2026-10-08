from inspect import signature
from pathlib import Path
from unittest.mock import Mock

import pytest
from cantok import DefaultToken, SimpleToken

from throng import local, temporary_directory, throng
from throng.abstracts.abstract_isolate import AbstractIsolate
from throng.abstracts.abstract_manager import AbstractManager
from throng.abstracts.results import SimpleRunResult
from throng.errors import NotSuccessfulRunError, PreparationCommandFailedError
from throng.extensions.local.manager import LocalManager
from throng.extensions.temporary_directory.manager import TemporaryDirectoryManager


@pytest.mark.parametrize(
    'form',
    [
        'default',
        'positional_path',
        'keyword_path',
        'positional_both',
        'mixed',
        'keyword_both',
        'exclude_only',
        'prepare_only',
        'positional_all',
        'keyword_all',
        'empty_prepare',
        'none_prepare',
    ],
)
def test_slot_provides_builtin_managers(tmp_path, monkeypatch, form):
    """Provide both documented managers with the requested source settings."""
    monkeypatch.chdir(tmp_path)
    path = tmp_path / 'source'
    exclude = ['*.tmp']
    prepare = ['first', 'second', 'first']
    forms = {
        'default': ((), {}, Path(), None, None),
        'positional_path': ((path,), {}, path, None, None),
        'keyword_path': ((), {'path': path}, path, None, None),
        'positional_both': ((path, exclude), {}, path, exclude, None),
        'mixed': ((path,), {'exclude': exclude}, path, exclude, None),
        'keyword_both': ((), {'path': path, 'exclude': exclude}, path, exclude, None),
        'exclude_only': ((), {'exclude': exclude}, Path(), exclude, None),
        'prepare_only': ((), {'prepare': prepare}, Path(), None, prepare),
        'positional_all': ((path, exclude, prepare), {}, path, exclude, prepare),
        'keyword_all': (
            (),
            {'path': path, 'exclude': exclude, 'prepare': prepare},
            path,
            exclude,
            prepare,
        ),
        'empty_prepare': ((), {'prepare': []}, Path(), None, []),
        'none_prepare': ((), {'prepare': None}, Path(), None, None),
    }
    args, kwargs, expected_path, expected_exclude, expected_prepare = forms[form]

    managers = throng(*args, **kwargs)

    assert isinstance(managers, dict)
    assert isinstance(managers['local'], LocalManager)
    assert isinstance(managers['temporary_directory'], TemporaryDirectoryManager)
    for name in ('local', 'temporary_directory'):
        assert managers[name].path == expected_path
        assert managers[name].exclude == expected_exclude
        assert managers[name].prepare == expected_prepare


@pytest.mark.parametrize('form', ['positional', 'keyword'])
@pytest.mark.parametrize(
    ('setting', 'packages'),
    [('omitted', None), ('none', None), ('empty', []), ('some', ['first', 'second'])],
)
@pytest.mark.parametrize('configured', [False, True])
def test_slot_forwards_packages_lazily_to_builtin_managers(tmp_path, monkeypatch, form, setting, packages, configured):  # noqa: PLR0913
    """Keep the package setting intact without installing during slot construction."""
    local_install = Mock(side_effect=AssertionError('unexpected installation'))
    temporary_install = Mock(side_effect=AssertionError('unexpected installation'))
    monkeypatch.setattr('throng.extensions.local.isolate.LocalIsolate.install', local_install)
    monkeypatch.setattr(
        'throng.extensions.temporary_directory.isolate.TemporaryDirectoryIsolate.install',
        temporary_install,
    )
    exclude = ['*.tmp'] if configured else None
    prepare = ['setup'] if configured else None

    if form == 'positional':
        managers = (
            throng(tmp_path, exclude, prepare)
            if setting == 'omitted'
            else throng(tmp_path, exclude, prepare, packages)
        )
    else:
        options = {'exclude': exclude, 'prepare': prepare}
        if setting != 'omitted':
            options['packages'] = packages
        managers = throng(tmp_path, **options)

    for name in ('local', 'temporary_directory'):
        assert managers[name].packages is packages
        assert managers[name].exclude is exclude
        assert managers[name].prepare is prepare
    local_install.assert_not_called()
    temporary_install.assert_not_called()


def test_slot_exposes_optional_packages_default():
    """Keep the public slot signature aligned with omitted package behavior."""
    assert signature(throng).parameters['packages'].default is None


def test_slot_uses_throng_entrypoint_group():
    """Discover third-party implementations in the package's own entrypoint group."""
    assert throng.entrypoint_group == 'throng'


@pytest.mark.parametrize('selection', ['all', 'by_name'])
@pytest.mark.parametrize(
    ('setting', 'operation'),
    [
        ('omitted', 'scope'),
        ('none', 'scope'),
        ('empty', 'scope'),
        ('commands', 'scope'),
        ('commands', 'run'),
        ('commands', 'chain'),
        ('failed', 'get'),
        ('failed', 'scope'),
        ('failed', 'run'),
        ('failed', 'chain'),
    ],
)
def test_external_plugin_inherits_preparation_and_execution(tmp_path, selection, setting, operation):  # noqa: PLR0915
    """Prepare a registered plugin whose isolate constructor knows only its snapshot."""
    executions = []
    cleaned = []
    options = {
        'omitted': {},
        'none': {'prepare': None},
        'empty': {'prepare': []},
        'commands': {'prepare': ['first', 'second', 'first', 'third']},
        'failed': {'prepare': ['first', 'fail', 'unreachable']},
    }[setting]

    class PluginIsolate(AbstractIsolate):
        def __init__(self, state):
            self.state = state

        def _run(self, command, token=None):
            result = SimpleRunResult(command != 'fail', 7 if command == 'fail' else 0)
            executions.append((command, token, result))
            return result

        def read(self):
            return self.state

        def kill(self):
            cleaned.append(self)

        def install(self, *_dependencies):
            pass

    class PluginManager(AbstractManager):
        def _get(self, state, token=DefaultToken()):  # noqa: B008, ARG002
            return PluginIsolate(state)

        def read(self):
            return b'external snapshot'

    @throng.plugin(unique=True)
    def external_test_plugin(path='.', exclude=None, prepare=None):
        return PluginManager(path, exclude, prepare)

    try:
        factory = throng if selection == 'all' else throng['external_test_plugin']
        manager = factory(tmp_path, exclude=['*.tmp'], **options)['external_test_plugin']
        assert isinstance(manager, PluginManager)
        assert manager.path == tmp_path
        assert manager.exclude == ['*.tmp']
        if setting == 'failed':
            expectation = pytest.raises(PreparationCommandFailedError)
            if operation == 'get':
                with expectation as caught:
                    manager.get(manager.read())
            elif operation == 'scope':
                with expectation as caught, manager.scope:
                    pytest.fail('Failed preparation must prevent context entry.')
            else:
                with expectation as caught:
                    getattr(manager, operation)('work', exception=ValueError)
            assert [command for command, _, _ in executions] == ['first', 'fail']
            assert isinstance(caught.value.__cause__, NotSuccessfulRunError)
            assert caught.value.__cause__.result is executions[-1][2]
            assert len(cleaned) == 1
            assert isinstance(cleaned[0], PluginIsolate)
            assert cleaned[0].read() == b'external snapshot'
            assert all(isinstance(token, DefaultToken) for _, token, _ in executions)
            return

        if operation in ('run', 'chain'):
            actual = getattr(manager, operation)('work', exception=True)
            result = actual if operation == 'run' else actual[0]
            assert result is executions[-1][2]
            assert [command for command, _, _ in executions] == options['prepare'] + ['work']
            assert len(cleaned) == 1
            return

        with manager.scope as isolate:
            assert isolate.read() == b'external snapshot'
            assert [command for command, _, _ in executions] == (options.get('prepare') or [])
            assert cleaned == []
            token = SimpleToken()
            result = isolate.run('work', token=token, exception=True)
            assert result is executions[-1][2]
            with pytest.raises(NotSuccessfulRunError) as caught:
                isolate.chain('fail', 'unreachable', token=token, exception=True)
            assert caught.value.result is executions[-1][2]
            assert [command for command, _, _ in executions] == (options.get('prepare') or []) + ['work', 'fail']
            assert all(passed_token is token for _, passed_token, _ in executions[-2:])
            assert cleaned == []
        assert cleaned == [isolate]
    finally:
        del throng['external_test_plugin']


def test_external_plugin_uses_explicit_get_token_for_creation_and_preparation(tmp_path):
    """Let a registered plugin consume the same token used by its setup commands."""
    executions = []
    cleaned = []

    class TokenAwareIsolate(AbstractIsolate):
        def __init__(self, state, creation_token):
            self.state = state
            self.creation_token = creation_token

        def _run(self, command, token=DefaultToken()):  # noqa: B008
            executions.append((command, token))
            return SimpleRunResult(True)

        def read(self):
            return self.state

        def kill(self):
            cleaned.append(self)

        def install(self, *_dependencies):
            pass

    class TokenAwareManager(AbstractManager):
        def _get(self, state, token=DefaultToken()):  # noqa: B008
            return TokenAwareIsolate(state, token)

        def read(self):
            return b'external snapshot'

    @throng.plugin(unique=True)
    def token_aware_test_plugin(path='.', exclude=None, prepare=None):
        return TokenAwareManager(path, exclude, prepare)

    try:
        manager = throng['token_aware_test_plugin'](tmp_path, prepare=['first', 'second'])['token_aware_test_plugin']
        token = SimpleToken()
        isolate = manager.get(manager.read(), token=token)

        assert isolate.read() == b'external snapshot'
        assert isolate.creation_token is token
        assert [command for command, _ in executions] == ['first', 'second']
        assert all(passed_token is token for _, passed_token in executions)
        assert cleaned == []
        isolate.kill()
        assert cleaned == [isolate]
    finally:
        del throng['token_aware_test_plugin']


@pytest.mark.parametrize('selection', ['all', 'by_name'])
def test_external_plugin_installs_slot_packages_before_preparation(tmp_path, selection):
    """Apply the shared package setting to registered third-party managers."""
    events = []
    packages = ['first', 'second']

    class PackageAwareIsolate(AbstractIsolate):
        def __init__(self, state):
            self.state = state

        def _run(self, command, token=DefaultToken()):  # noqa: B008
            events.append(('prepare', command, token))
            return SimpleRunResult(True)

        def read(self):
            return self.state

        def kill(self):
            events.append(('kill', self))

        def install(self, *requested, token=DefaultToken()):  # noqa: B008
            events.append(('install', requested, token))

    class PackageAwareManager(AbstractManager):
        def _get(self, state, token=DefaultToken()):  # noqa: B008
            events.append(('create', state, token))
            return PackageAwareIsolate(state)

        def read(self):
            return b'external snapshot'

    @throng.plugin(unique=True)
    def package_aware_test_plugin(path='.', exclude=None, prepare=None, packages=None):
        events.append(('factory', packages))
        return PackageAwareManager(path, exclude, prepare, packages)

    try:
        factory = throng if selection == 'all' else throng['package_aware_test_plugin']
        manager = factory(tmp_path, prepare=['setup'], packages=packages)['package_aware_test_plugin']
        token = SimpleToken()
        isolate = manager.get(manager.read(), token=token)

        assert events == [
            ('factory', packages),
            ('create', b'external snapshot', token),
            ('install', ('first', 'second'), token),
            ('prepare', 'setup', token),
        ]
        assert manager.packages is packages
        assert events[0][1] is packages
        assert all(event[2] is token for event in events[1:])
        isolate.kill()
        assert events[-1] == ('kill', isolate)
    finally:
        del throng['package_aware_test_plugin']


@pytest.mark.parametrize(
    ('name', 'factory'),
    [('local', local), ('temporary_directory', temporary_directory)],
)
def test_builtin_registration_is_unique(name, factory):
    """Register each builtin factory once under its documented unique name."""
    plugins = [plugin for plugin in throng if plugin.name == name]

    assert len(plugins) == 1
    assert plugins[0].unique is True
    assert plugins[0].plugin_function is factory


@pytest.mark.parametrize(
    'path_kind',
    ['relative_string', 'relative_path', 'absolute_string', 'absolute_path'],
)
@pytest.mark.parametrize('exclude', [None, [], ['cache/', '*.tmp', '!keep.tmp']])
def test_slot_preserves_path_and_exclusion_values(
    tmp_path,
    monkeypatch,
    path_kind,
    exclude,
):
    """Keep meaningful path and exclusion values consistent across builtin plugins."""
    monkeypatch.chdir(tmp_path)
    relative = Path('space here') / 'каталог'
    path = {
        'relative_string': str(relative),
        'relative_path': relative,
        'absolute_string': str(tmp_path / relative),
        'absolute_path': tmp_path / relative,
    }[path_kind]
    expected_exclude = None if exclude is None else exclude.copy()

    managers = throng(path, exclude)

    for name in ('local', 'temporary_directory'):
        assert managers[name].path == Path(path)
        assert managers[name].exclude == expected_exclude


def test_slot_calls_do_not_share_managers_or_settings(tmp_path):
    """Create independent managers without leaking earlier settings into defaults."""
    first = throng(tmp_path, ['*.tmp'], ['first'])
    second = throng(tmp_path, ['*.tmp'], ['first'])
    for name in ('local', 'temporary_directory'):
        first[name].path = tmp_path / 'changed'
        first[name].exclude.append('extra')
        first[name].prepare.append('extra')
    defaults = throng()

    for name in ('local', 'temporary_directory'):
        assert first[name] is not second[name]
        assert second[name].path == tmp_path
        assert second[name].exclude == ['*.tmp']
        assert second[name].prepare == ['first']
        assert defaults[name].path == Path()
        assert defaults[name].exclude is None
        assert defaults[name].prepare is None


@pytest.mark.parametrize('plugin', ['local', 'temporary_directory'])
@pytest.mark.parametrize('mode', ['open', 'scope', 'run', 'chain'])
@pytest.mark.parametrize('success', [False, True])
def test_builtin_lifecycle_through_public_api(
    tmp_path,
    monkeypatch,
    plugin,
    mode,
    success,
):
    """Use the same lifecycle API while preserving each plugin's directory behavior."""
    (tmp_path / 'source').write_bytes(b'data')
    manager = throng(tmp_path)[plugin]
    token = SimpleToken()
    expected = SimpleRunResult(success, 0 if success else 1, 'output', 'diagnostic')
    directories = []

    def execute(command, **kwargs):
        assert command == 'command'
        assert kwargs['token'] is token
        assert kwargs['catch_output'] is True
        assert kwargs['catch_exceptions'] is True
        directory = kwargs['directory']
        assert (directory / 'source').read_bytes() == b'data'
        directories.append(directory)
        return expected

    monkeypatch.setattr(f'throng.extensions.{plugin}.isolate.run', execute)
    if mode == 'open':
        isolate = manager.get(manager.read())
        try:
            result = isolate.run('command', token=token)
        finally:
            isolate.kill()
    elif mode == 'scope':
        with manager.scope as isolate:
            result = isolate.run('command', token=token)
    else:
        result = getattr(manager, mode)('command', token=token)

    if mode == 'chain':
        assert len(result) == 1
        assert result[0] is expected
    else:
        assert result is expected
    assert len(directories) == 1
    assert (directories[0] == tmp_path) is (plugin == 'local')
    assert directories[0].exists() is (plugin == 'local')
    assert (tmp_path / 'source').read_bytes() == b'data'


@pytest.mark.parametrize('plugin', ['local', 'temporary_directory'])
@pytest.mark.parametrize('closed', [False, True])
def test_chained_commands_share_the_same_environment(
    tmp_path,
    monkeypatch,
    plugin,
    closed,
):
    """Keep command changes visible within a chain and isolated according to the plugin."""
    manager = throng(tmp_path)[plugin]
    directories = []

    def execute(command, *, directory, **_kwargs):
        directories.append(directory)
        if command == 'write':
            (directory / 'created').write_bytes(b'shared')
            return SimpleRunResult(True, 0, '', '')
        return SimpleRunResult(True, 0, (directory / 'created').read_text(), '')

    monkeypatch.setattr(f'throng.extensions.{plugin}.isolate.run', execute)
    if closed:
        results = manager.chain('write', 'read')
    else:
        with manager.scope as isolate:
            results = isolate.chain('write', 'read')

    assert results == [
        SimpleRunResult(True, 0, '', ''),
        SimpleRunResult(True, 0, 'shared', ''),
    ]
    assert len(directories) == 2
    assert directories[0] == directories[1]
    assert (tmp_path / 'created').exists() is (plugin == 'local')
    assert directories[0].exists() is (plugin == 'local')
