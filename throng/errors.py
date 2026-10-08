
from typing import Optional

from throng.abstracts.results import RunResultProtocol


class ThrongError(Exception):
    ...  # pragma: no cover


class CannotCancelNonExistingIsolateError(ThrongError, RuntimeError):
    ...  # pragma: no cover


class NotSupportedCommandError(ThrongError, RuntimeError):
    ...  # pragma: no cover


class CannotInstallDependencyError(ThrongError, RuntimeError):
    def __init__(self, message: Optional[str] = None, result: Optional[RunResultProtocol] = None) -> None:
        if message is None:
            super().__init__()
        else:
            super().__init__(message)
        self.result = result


class PreparationCommandFailedError(ThrongError, RuntimeError):
    ...  # pragma: no cover


class InterruptedChainError(ThrongError, RuntimeError):
    ...  # pragma: no cover


class InterruptedInstallationError(ThrongError, RuntimeError):
    ...  # pragma: no cover


class NotSuccessfulRunError(ThrongError, RuntimeError):
    def __init__(self, message: str, result: RunResultProtocol) -> None:
        super().__init__(message)
        self.message = message
        self.result = result
