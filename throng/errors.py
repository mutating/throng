from typing import Optional

from throng.result import RunResult


class CommandExecutionError(Exception):
    """Report a command that did not complete successfully during ``run``."""

    def __init__(self, message: str, result: Optional[RunResult] = None) -> None:
        """Create an error, optionally retaining the failed command's RunResult."""
        self.result = result
        super().__init__(message)


class InstallError(Exception):
    """Report dependency installation or virtual-environment creation failure."""


class InvalidBaseDirectoryError(Exception):
    """Report an unusable configured root for temporary isolate directories."""


class InvalidVirtualEnvPathError(Exception):
    """Report a virtual-environment location or executable that cannot be used."""


class ArchiveUnpackError(Exception):
    """
    Report a ``load`` failure caused by input or filesystem application errors.

    The archive may be malformed or unsafe, or staging/commit may have failed
    while applying otherwise valid bytes to the isolate filesystem.
    """


class OperationCancelledError(Exception):
    """Report cancellation of a cancellable isolate operation."""


class IsolateDeletedError(Exception):
    """Report an attempted operation on an isolate already disposed of."""
