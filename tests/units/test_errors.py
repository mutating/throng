import pytest

from throng.abstracts.results import SimpleRunResult
from throng.errors import (
    ArgumentsRedefinitionError,
    CannotCancelNonExistingIsolateError,
    CannotInstallDependencyError,
    InterruptedChainError,
    InterruptedInstallationError,
    NotSuccessfulRunError,
    NotSupportedCommandError,
    PreparationCommandFailedError,
    ThrongError,
)


@pytest.mark.parametrize(
    'error_type',
    [
        ArgumentsRedefinitionError,
        CannotCancelNonExistingIsolateError,
        CannotInstallDependencyError,
        InterruptedChainError,
        InterruptedInstallationError,
        NotSuccessfulRunError,
        NotSupportedCommandError,
        PreparationCommandFailedError,
    ],
)
def test_library_errors_specialize_runtime_error(error_type):
    """Let callers handle throng failures specifically or as general runtime errors."""
    assert issubclass(error_type, RuntimeError)
    assert issubclass(error_type, ThrongError)
    assert error_type is not RuntimeError


@pytest.mark.parametrize(
    ('first', 'second'),
    [
        (ArgumentsRedefinitionError, CannotCancelNonExistingIsolateError),
        (ArgumentsRedefinitionError, CannotInstallDependencyError),
        (ArgumentsRedefinitionError, InterruptedChainError),
        (ArgumentsRedefinitionError, InterruptedInstallationError),
        (ArgumentsRedefinitionError, NotSuccessfulRunError),
        (ArgumentsRedefinitionError, NotSupportedCommandError),
        (ArgumentsRedefinitionError, PreparationCommandFailedError),
        (CannotCancelNonExistingIsolateError, CannotInstallDependencyError),
        (CannotCancelNonExistingIsolateError, NotSupportedCommandError),
        (CannotInstallDependencyError, NotSupportedCommandError),
        (CannotCancelNonExistingIsolateError, PreparationCommandFailedError),
        (CannotInstallDependencyError, PreparationCommandFailedError),
        (NotSupportedCommandError, PreparationCommandFailedError),
        (NotSuccessfulRunError, PreparationCommandFailedError),
        (InterruptedChainError, PreparationCommandFailedError),
        (NotSuccessfulRunError, InterruptedChainError),
        (InterruptedInstallationError, CannotInstallDependencyError),
        (InterruptedInstallationError, PreparationCommandFailedError),
        (InterruptedInstallationError, InterruptedChainError),
    ],
)
def test_library_errors_have_distinct_types(first, second):
    """Keep failure types distinct so callers can handle each cause separately."""
    assert first is not second
    assert not issubclass(first, second)
    assert not issubclass(second, first)


@pytest.mark.parametrize('message', ['', 'command failed', '失敗\n詳細'])
@pytest.mark.parametrize('populated', [False, True])
@pytest.mark.parametrize('keyword_arguments', [False, True])
def test_run_error_keeps_message_and_original_result(message, populated, keyword_arguments):
    """Keep diagnostics and standard exception text for positional and keyword construction."""
    result = SimpleRunResult(False, 7, 'output', 'diagnostic') if populated else SimpleRunResult(False)
    original = (result.success, result.returncode, result.stdout, result.stderr)

    if keyword_arguments:
        error = NotSuccessfulRunError(message=message, result=result)
    else:
        error = NotSuccessfulRunError(message, result)

    assert error.message == message
    assert str(error) == message
    assert error.args == (message,)
    assert error.result is result
    assert (result.success, result.returncode, result.stdout, result.stderr) == original


@pytest.mark.parametrize('message', ['', 'package failed', 'details\nmore details'])
@pytest.mark.parametrize('with_result', [False, True])
def test_installation_error_keeps_message_and_failed_result(with_result, message):
    """Expose pip diagnostics while retaining construction without a result."""
    result = SimpleRunResult(False, 17, 'stdout', 'stderr')
    error = CannotInstallDependencyError(message, result) if with_result else CannotInstallDependencyError(message)

    assert str(error) == message
    assert error.args == (message,)
    assert error.result is (result if with_result else None)


def test_installation_error_retains_zero_argument_construction():
    """Keep the former RuntimeError constructor form available to callers."""
    error = CannotInstallDependencyError()

    assert error.args == ()
    assert str(error) == ''
    assert error.result is None
