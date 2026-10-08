from importlib import import_module
from pathlib import Path
from unittest.mock import Mock, call

import pytest


@pytest.mark.parametrize(
    ('name', 'constructor_name'),
    [('local', 'LocalManager'), ('temporary_directory', 'TemporaryDirectoryManager')],
)
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
@pytest.mark.parametrize('as_string', [False, True])
def test_factory_forwards_settings_and_result(
    monkeypatch,
    name,
    constructor_name,
    form,
    as_string,
):
    """Forward supported call forms unchanged and return the constructor's manager."""
    plugins = import_module('throng.extensions.plugins')
    constructor = Mock()
    monkeypatch.setattr(plugins, constructor_name, constructor)
    source = Path('space here') / 'каталог'
    path = str(source) if as_string else source
    exclude = ['cache/', '*.tmp', '!keep.tmp']
    prepare = ['first', 'second', 'first']
    forms = {
        'default': ((), {}, '.', None, None),
        'positional_path': ((path,), {}, path, None, None),
        'keyword_path': ((), {'path': path}, path, None, None),
        'positional_both': ((path, exclude), {}, path, exclude, None),
        'mixed': ((path,), {'exclude': exclude}, path, exclude, None),
        'keyword_both': ((), {'path': path, 'exclude': exclude}, path, exclude, None),
        'exclude_only': ((), {'exclude': exclude}, '.', exclude, None),
        'prepare_only': ((), {'prepare': prepare}, '.', None, prepare),
        'positional_all': ((path, exclude, prepare), {}, path, exclude, prepare),
        'keyword_all': (
            (),
            {'path': path, 'exclude': exclude, 'prepare': prepare},
            path,
            exclude,
            prepare,
        ),
        'empty_prepare': ((), {'prepare': []}, '.', None, []),
        'none_prepare': ((), {'prepare': None}, '.', None, None),
    }
    args, kwargs, expected_path, expected_exclude, expected_prepare = forms[form]
    expected_exclude = None if expected_exclude is None else expected_exclude.copy()
    expected_prepare = None if expected_prepare is None else expected_prepare.copy()

    result = getattr(plugins, name)(*args, **kwargs)

    assert result is constructor.return_value
    constructor.assert_called_once_with(expected_path, expected_exclude, expected_prepare, None)


@pytest.mark.parametrize(
    ('name', 'constructor_name'),
    [('local', 'LocalManager'), ('temporary_directory', 'TemporaryDirectoryManager')],
)
@pytest.mark.parametrize('form', ['positional', 'keyword'])
@pytest.mark.parametrize('packages', [None, [], ['first', 'second']])
@pytest.mark.parametrize('configured', [False, True])
def test_factory_forwards_packages(monkeypatch, name, constructor_name, form, packages, configured):  # noqa: PLR0913
    """Forward the fourth setting unchanged for both supported call forms."""
    plugins = import_module('throng.extensions.plugins')
    constructor = Mock()
    monkeypatch.setattr(plugins, constructor_name, constructor)
    path = Path('source') if configured else '.'
    exclude = ['*.tmp'] if configured else None
    prepare = ['setup'] if configured else None

    if form == 'positional':
        result = getattr(plugins, name)(path, exclude, prepare, packages)
    else:
        result = getattr(plugins, name)(path=path, exclude=exclude, prepare=prepare, packages=packages)

    assert result is constructor.return_value
    constructor.assert_called_once_with(path, exclude, prepare, packages)
    assert constructor.call_args.args[1] is exclude
    assert constructor.call_args.args[2] is prepare
    assert constructor.call_args.args[3] is packages


@pytest.mark.parametrize(
    ('name', 'constructor_name'),
    [('local', 'LocalManager'), ('temporary_directory', 'TemporaryDirectoryManager')],
)
@pytest.mark.parametrize('exclude', [None, [], ['*.tmp']])
@pytest.mark.parametrize('prepare', [None, [], ['first', 'second']])
def test_factory_does_not_cache_managers(monkeypatch, name, constructor_name, exclude, prepare):
    """Preserve exclusion values while constructing a fresh manager on every call."""
    plugins = import_module('throng.extensions.plugins')
    first, second = Mock(), Mock()
    constructor = Mock(side_effect=[first, second])
    monkeypatch.setattr(plugins, constructor_name, constructor)

    assert getattr(plugins, name)('.', exclude, prepare) is first
    assert getattr(plugins, name)('other') is second
    assert constructor.call_args_list == [call('.', exclude, prepare, None), call('other', None, None, None)]


@pytest.mark.parametrize(
    ('name', 'constructor_name'),
    [('local', 'LocalManager'), ('temporary_directory', 'TemporaryDirectoryManager')],
)
@pytest.mark.parametrize('error_type', [RuntimeError, OSError])
def test_factory_preserves_constructor_error(
    monkeypatch,
    name,
    constructor_name,
    error_type,
):
    """Expose the original construction failure without retrying or wrapping it."""
    plugins = import_module('throng.extensions.plugins')
    error = error_type('constructor failed')
    constructor = Mock(side_effect=error)
    monkeypatch.setattr(plugins, constructor_name, constructor)

    with pytest.raises(error_type) as caught:
        getattr(plugins, name)()

    assert caught.value is error
    constructor.assert_called_once_with('.', None, None, None)


@pytest.mark.parametrize('name', ['local', 'temporary_directory'])
@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('prepare', [None, ['first', 'second']])
@pytest.mark.parametrize(
    ('package_setting', 'packages'),
    [('omitted', None), ('none', None), ('empty', []), ('some', ['package'])],
)
def test_factory_creation_is_lazy(  # noqa: PLR0913
    tmp_path,
    monkeypatch,
    name,
    existing,
    prepare,
    package_setting,
    packages,
):
    """Create builtin managers without reading files or allocating an isolate.

    Call each builtin factory directly so unrelated plugins may perform their own I/O.
    Restore filesystem operations before checking the source or cleaning up the test.
    """
    plugins = import_module('throng.extensions.plugins')
    manager_type = {
        'local': plugins.LocalManager,
        'temporary_directory': plugins.TemporaryDirectoryManager,
    }[name]
    path = tmp_path if existing else tmp_path / 'missing'
    read, get, create = Mock(), Mock(), Mock()
    monkeypatch.setattr(manager_type, 'read', read)
    monkeypatch.setattr(manager_type, 'get', get)
    monkeypatch.setattr(manager_type, '_get', create)
    operations = {
        operation: Mock(
            side_effect=AssertionError(f'Unexpected initialization I/O: {operation}'),
        )
        for operation in (
            'builtins.open',
            'io.open',
            'os.scandir',
            'os.listdir',
            'pathlib.Path.stat',
            'tempfile.mkdtemp',
        )
    }
    with monkeypatch.context() as patcher:
        for operation, mock in operations.items():
            patcher.setattr(operation, mock)
        options = {'prepare': prepare}
        if package_setting != 'omitted':
            options['packages'] = packages
        manager = getattr(plugins, name)(path, **options)

    assert isinstance(manager, manager_type)
    assert manager.path == path
    assert manager.prepare == prepare
    assert manager.packages is packages
    read.assert_not_called()
    get.assert_not_called()
    create.assert_not_called()
    for mock in operations.values():
        mock.assert_not_called()
    assert list(tmp_path.iterdir()) == []
