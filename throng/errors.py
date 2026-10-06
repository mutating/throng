
from throng.abstracts.results import RunResultProtocol


class ThrongError(Exception):
    ...  # pragma: no cover


class CannotCancelNonExistingIsolateError(ThrongError, RuntimeError):
    ...  # pragma: no cover


class NotSupportedCommandError(ThrongError, RuntimeError):
    ...  # pragma: no cover


class CannotInstallDependencyError(ThrongError, RuntimeError):
    ...  # pragma: no cover


class PreparationCommandFailedError(ThrongError, RuntimeError):
    ...  # pragma: no cover


class InterruptedChainError(ThrongError, RuntimeError):
    ...  # pragma: no cover


class NotSuccessfulRunError(ThrongError, RuntimeError):
    def __init__(self, message: str, result: RunResultProtocol) -> None:
        super().__init__(message)
        self.message = message
        self.result = result
