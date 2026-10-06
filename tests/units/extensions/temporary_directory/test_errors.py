from throng.errors import (
    InterruptedChainError,
    NotSuccessfulRunError,
    PreparationCommandFailedError,
    ThrongError,
)
from throng.extensions.temporary_directory.errors import DirectoryDoesNotExistError


def test_destroyed_directory_has_specific_error_type():
    """Let callers distinguish a destroyed directory from other execution failures."""
    assert issubclass(DirectoryDoesNotExistError, Exception)
    assert issubclass(DirectoryDoesNotExistError, ThrongError)
    assert DirectoryDoesNotExistError is not ThrongError
    assert DirectoryDoesNotExistError is not Exception
    assert not issubclass(
        DirectoryDoesNotExistError,
        (InterruptedChainError, NotSuccessfulRunError, PreparationCommandFailedError),
    )
    assert (
        DirectoryDoesNotExistError.__module__
        == 'throng.extensions.temporary_directory.errors'
    )
