from typing import List

from throng.abstracts.results import RunResultProtocol


class CannotCancelNonExistingIsolateError(RuntimeError):
    ...  # pragma: no cover


class NotSupportedCommandError(RuntimeError):
    ...  # pragma: no cover


class CannotInstallDependencyError(RuntimeError):
    ...  # pragma: no cover


class PreparationCommandFailedError(RuntimeError):
    def __init__(self, message: str, results: List[RunResultProtocol]) -> None:
        self.message = message
        self.results = results
