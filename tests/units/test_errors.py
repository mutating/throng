import pytest

from throng.abstracts.results import SimpleRunResult
from throng.errors import (
    CannotCancelNonExistingIsolateError,
    CannotInstallDependencyError,
    NotSupportedCommandError,
    PreparationCommandFailedError,
)


@pytest.mark.parametrize(
    'error_type',
    [
        CannotCancelNonExistingIsolateError,
        CannotInstallDependencyError,
        NotSupportedCommandError,
        PreparationCommandFailedError,
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
        (CannotCancelNonExistingIsolateError, PreparationCommandFailedError),
        (CannotInstallDependencyError, PreparationCommandFailedError),
        (NotSupportedCommandError, PreparationCommandFailedError),
    ],
)
def test_library_errors_have_distinct_types(first, second):
    """Keep failure types distinct so callers can handle each cause separately."""
    assert first is not second


@pytest.mark.parametrize('message', ['', 'preparation failed', '失敗\n詳細'])
@pytest.mark.parametrize('populated', [False, True])
@pytest.mark.parametrize('keyword_arguments', [False, True])
def test_preparation_error_keeps_message_and_original_results(message, populated, keyword_arguments):
    """Retain caller diagnostics unchanged for positional and keyword construction."""
    results = [SimpleRunResult(True, 0, 'ready', ''), SimpleRunResult(False, None, None, 'failed')] if populated else []
    originals = tuple(results)

    if keyword_arguments:
        error = PreparationCommandFailedError(message=message, results=results)
    else:
        error = PreparationCommandFailedError(message, results)

    assert error.message == message
    assert error.results is results
    assert tuple(error.results) == originals
    assert all(result is original for result, original in zip(error.results, originals))
