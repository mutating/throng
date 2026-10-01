from abc import ABC, abstractmethod
from typing import List, Optional

from cantok import AbstractToken, DefaultToken

from throng.abstracts.results import RunResultProtocol, SimpleRunResult
from throng.errors import PreparationCommandFailedError


class AbstractIsolate(ABC):
    def __init__(self, prepare: Optional[List[str]] = None) -> None:
        if prepare is not None:
            try:
                preparations = self.chain(*prepare)
            except BaseException as e:
                self.kill()
                if not isinstance(e, Exception):
                    raise
                raise PreparationCommandFailedError('The preparation command ended with an error.', []) from e

            for subresult in preparations:
                if not subresult.success:
                    self.kill()
                    raise PreparationCommandFailedError('The preparation command ended with an error.', preparations)

    def __del__(self) -> None:
        self.kill()

    @abstractmethod
    def run(self, command: str, token: AbstractToken = DefaultToken()) -> RunResultProtocol:  # noqa: B008
        ...  # pragma: no cover

    @abstractmethod
    def read(self) -> bytes:
        ...  # pragma: no cover

    @abstractmethod
    def kill(self) -> None:
        ...  # pragma: no cover

    @abstractmethod
    def install(self, *packages: str) -> None:
        ...  # pragma: no cover

    def chain(self, *commands: str, token: AbstractToken = DefaultToken()) -> List[RunResultProtocol]:  # noqa: B008
        results = []

        for command in commands:
            if token:
                result = self.run(command, token=token)
                results.append(result)
            else:
                results.append(
                    SimpleRunResult(
                        success=False,
                    ),
                )

        return results
