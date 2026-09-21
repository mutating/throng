from abc import ABC, abstractmethod

from cantok import AbstractToken, DefaultToken

from throng.abstracts.result_protocol import RunResultProtocol


class AbstractIsolate(ABC):
    @abstractmethod
    def run(self, command: str, token: AbstractToken = DefaultToken()) -> RunResultProtocol:
        ...

    @abstractmethod
    def read(self) -> bytes:
        ...

    @abstractmethod
    def kill(self) -> None:
        ...
