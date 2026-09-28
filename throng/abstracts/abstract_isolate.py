from abc import ABC, abstractmethod
from typing import List

from cantok import AbstractToken, DefaultToken

from throng.abstracts.results import RunResultProtocol, SimpleRunResult


class AbstractIsolate(ABC):
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
