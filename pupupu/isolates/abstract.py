from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, List, Optional, Union

from cantok import AbstractToken, DefaultToken
from emptylog import EmptyLogger, LoggerProtocol


class AbstractIsolate(ABC):
    @abstractmethod
    def run(self, *arguments: Union[str, Path], logger: LoggerProtocol = EmptyLogger(), split: bool = True, token: AbstractToken = DefaultToken(), timeout: Optional[Union[int, float]] = None) -> Any:  # noqa: B008
        ...

    @abstractmethod
    def load(self, dump: bytes) -> None:
        ...

    @abstractmethod
    def dump(self) -> bytes:
        ...

    @abstractmethod
    def install(self, what: Union[str, List[str]]):
        ...
