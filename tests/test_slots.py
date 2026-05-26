from typing import cast

import pytest
from emptylog import EmptyLogger, LoggerProtocol
from full_match import match

from throng import AbstractIsolate, AbstractThrong, throngs
from throng import throngs as package_throngs
from throng.slots import throngs as slot_throngs


def test_slots_source_and_reexport():
    """Verify that throng.throngs is the exact slot object exported from throng.slots."""
    assert package_throngs is slot_throngs


def test_external_plugin_registration(request):
    """Verify that an external plugin registered through the slot is returned as an AbstractThrong instance."""

    class CustomThrong(AbstractThrong):
        def get_isolate(self) -> AbstractIsolate:
            raise RuntimeError('not needed')

    custom_throng = CustomThrong()
    request.addfinalizer(lambda: throngs.pop('custom_registration_test', None))

    @throngs.plugin('custom_registration_test')
    def custom_registration_test(_logger: LoggerProtocol = EmptyLogger()) -> AbstractThrong:  # noqa: B008
        return custom_throng

    registered_throngs = throngs()

    assert registered_throngs['custom_registration_test'] is custom_throng
    assert isinstance(registered_throngs['custom_registration_test'], AbstractThrong)


def test_plugin_returning_non_throng_fails(request):
    """Verify that a plugin returning a non-AbstractThrong value is rejected by slot type checking."""
    request.addfinalizer(lambda: throngs.pop('invalid_registration_test', None))

    @throngs.plugin('invalid_registration_test')
    def invalid_registration_test(_logger: LoggerProtocol = EmptyLogger()) -> AbstractThrong:  # noqa: B008
        return cast(AbstractThrong, 'not a throng')

    with pytest.raises(TypeError, match=match('The type str of the plugin\'s "invalid_registration_test" return value \'not a throng\' does not match the expected type AbstractThrong.')):
        throngs()
