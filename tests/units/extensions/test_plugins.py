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
    exclude = ['*.tmp', 'cache/']
    forms = {
        'default': ((), {}, '.', None),
        'positional_path': ((path,), {}, path, None),
        'keyword_path': ((), {'path': path}, path, None),
        'positional_both': ((path, exclude), {}, path, exclude),
        'mixed': ((path,), {'exclude': exclude}, path, exclude),
        'keyword_both': ((), {'path': path, 'exclude': exclude}, path, exclude),
        'exclude_only': ((), {'exclude': exclude}, '.', exclude),
    }
    args, kwargs, expected_path, expected_exclude = forms[form]

    result = getattr(plugins, name)(*args, **kwargs)

    assert result is constructor.return_value
    constructor.assert_called_once_with(expected_path, expected_exclude)


@pytest.mark.parametrize(
    ('name', 'constructor_name'),
    [('local', 'LocalManager'), ('temporary_directory', 'TemporaryDirectoryManager')],
)
@pytest.mark.parametrize('exclude', [None, [], ['*.tmp']])
def test_factory_does_not_cache_managers(monkeypatch, name, constructor_name, exclude):
    """Preserve exclusion values while constructing a fresh manager on every call."""
    plugins = import_module('throng.extensions.plugins')
    first, second = Mock(), Mock()
    constructor = Mock(side_effect=[first, second])
    monkeypatch.setattr(plugins, constructor_name, constructor)

    assert getattr(plugins, name)('.', exclude) is first
    assert getattr(plugins, name)('other') is second
    assert constructor.call_args_list == [call('.', exclude), call('other', None)]


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
    constructor.assert_called_once_with('.', None)
