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


@pytest.mark.parametrize(
    ('first', 'second'),
    [
        (CannotCancelNonExistingIsolateError, CannotInstallDependencyError),
        (CannotCancelNonExistingIsolateError, NotSupportedCommandError),
        (CannotInstallDependencyError, NotSupportedCommandError),
    ],
)
def test_library_errors_have_distinct_types(first, second):
    """Keep failure types distinct so callers can handle each cause separately."""
    assert first is not second
