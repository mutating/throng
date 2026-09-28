import pytest

from throng.errors import (
    CannotCancelNonExistingIsolateError,
    CannotInstallDependencyError,
    NotSupportedCommandError,
)


@pytest.mark.parametrize(
    'error_type',
    [
        CannotCancelNonExistingIsolateError,
        CannotInstallDependencyError,
        NotSupportedCommandError,
    ],
)
def test_library_errors_specialize_runtime_error(error_type):
    """Let callers handle throng failures specifically or as general runtime errors."""
    assert issubclass(error_type, RuntimeError)
    assert error_type is not RuntimeError
