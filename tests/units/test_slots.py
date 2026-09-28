from pathlib import Path
from unittest.mock import Mock

import pytest
from cantok import SimpleToken

from throng import local, temporary_directory, throng
from throng.abstracts.results import SimpleRunResult
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
    ],
)
def test_slot_provides_builtin_managers(tmp_path, monkeypatch, form):
    """Provide both documented managers with the requested source settings."""
    monkeypatch.chdir(tmp_path)
    path = tmp_path / 'source'
    exclude = ['*.tmp']
    forms = {
        'default': ((), {}, Path(), None),
        'positional_path': ((path,), {}, path, None),
        'keyword_path': ((), {'path': path}, path, None),
        'positional_both': ((path, exclude), {}, path, exclude),
        'mixed': ((path,), {'exclude': exclude}, path, exclude),
        'keyword_both': ((), {'path': path, 'exclude': exclude}, path, exclude),
        'exclude_only': ((), {'exclude': exclude}, Path(), exclude),
    }
    args, kwargs, expected_path, expected_exclude = forms[form]

    managers = throng(*args, **kwargs)

    assert isinstance(managers, dict)
    assert isinstance(managers['local'], LocalManager)
    assert isinstance(managers['temporary_directory'], TemporaryDirectoryManager)
    for name in ('local', 'temporary_directory'):
        assert managers[name].path == expected_path
        assert managers[name].exclude == expected_exclude


def test_slot_uses_throng_entrypoint_group():
    """Discover third-party implementations in the package's own entrypoint group."""
    assert throng.entrypoint_group == 'throng'


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
@pytest.mark.parametrize('exclude', [None, [], ['*.tmp', 'cache/']])
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

    managers = throng(path, exclude)

    for name in ('local', 'temporary_directory'):
        assert managers[name].path == Path(path)
        assert managers[name].exclude == exclude


def test_slot_calls_do_not_share_managers_or_settings(tmp_path):
    """Create independent managers without leaking earlier settings into defaults."""
    first = throng(tmp_path, ['*.tmp'])
    second = throng(tmp_path, ['*.tmp'])
    for name in ('local', 'temporary_directory'):
        first[name].path = tmp_path / 'changed'
        first[name].exclude.append('extra')
    defaults = throng()

    for name in ('local', 'temporary_directory'):
        assert first[name] is not second[name]
        assert second[name].path == tmp_path
        assert second[name].exclude == ['*.tmp']
        assert defaults[name].path == Path()
        assert defaults[name].exclude is None


@pytest.mark.parametrize('existing', [False, True])
def test_slot_creation_is_lazy(tmp_path, monkeypatch, existing):
    """Obtain managers without reading files or allocating execution environments."""
    path = tmp_path if existing else tmp_path / 'missing'
    local_read, temporary_read, local_get, temporary_get = (Mock() for _ in range(4))
    monkeypatch.setattr(LocalManager, 'read', local_read)
    monkeypatch.setattr(LocalManager, 'get', local_get)
    monkeypatch.setattr(TemporaryDirectoryManager, 'read', temporary_read)
    monkeypatch.setattr(TemporaryDirectoryManager, 'get', temporary_get)

    managers = throng(path)

    assert {'local', 'temporary_directory'} <= managers.keys()
    for dependency in (local_read, temporary_read, local_get, temporary_get):
        dependency.assert_not_called()
    assert list(tmp_path.iterdir()) == []


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
