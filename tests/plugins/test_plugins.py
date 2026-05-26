from subprocess import run as run_process
from sys import executable

import pytest
from emptylog import EmptyLogger, LoggerProtocol
from full_match import match
from pristan.errors import PrimadonnaPluginError

from throng import AbstractIsolate, AbstractThrong, throngs


def test_plugins_return_builtins():
    """Verify that pristan entry point loading returns the built-in local and temporary_directory plugins."""
    plugins = throngs()

    assert set(plugins) >= {'local', 'temporary_directory'}
    assert isinstance(plugins['local'], AbstractThrong)
    assert isinstance(plugins['temporary_directory'], AbstractThrong)


def test_builtin_plugins_are_discovered_from_entry_points():
    """Verify that built-in plugins are discovered through entry points instead of eager imports from the slot module."""
    completed_process = run_process(
        [
            executable,
            '-c',
            (
                'from throng.slots import throngs\n'
                'print(len(throngs))\n'
                'from throng.plugins.directory_isolate import DirectoryIsolate\n'
                'print(len(throngs))\n'
                'print(",".join(sorted(throngs().keys())))\n'
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed_process.stdout.splitlines() == ['0', '0', 'local,temporary_directory']


def test_builtin_plugins_read_independent_skelet_sources(monkeypatch):
    """Verify that each built-in plugin reads settings only from its plugin-specific skelet source."""
    monkeypatch.setenv('LOCAL_COMPRESSION', 'bz2')
    monkeypatch.setenv('TEMPORARY_DIRECTORY_COMPRESSION', 'lzma')

    completed_process = run_process(
        [
            executable,
            '-c',
            (
                'from throng import throngs\n'
                'plugins = throngs()\n'
                'print(plugins["local"].config.compression)\n'
                'print(plugins["temporary_directory"].config.compression)\n'
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed_process.stdout.splitlines() == ['bz2', 'lzma']


def test_builtin_plugin_name_collision():
    """Verify that registering a plugin with a built-in name is rejected as a name collision."""

    class OtherLocalThrong(AbstractThrong):
        def get_isolate(self) -> AbstractIsolate:
            raise RuntimeError('not needed')

    with pytest.raises(PrimadonnaPluginError, match=match('Plugin "local" claims to be unique, but there are other plugins with the same name.')):
        @throngs.plugin('local')
        def local(_logger: LoggerProtocol = EmptyLogger()) -> AbstractThrong:  # noqa: B008
            return OtherLocalThrong()
