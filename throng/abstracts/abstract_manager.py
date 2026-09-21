from abc import ABC, abstractmethod
from types import TracebackType
from typing import Union, Optional
from pathlib import Path

from cantok import AbstractToken, DefaultToken

from throng.abstracts.abstract_isolate import AbstractIsolate
from throng.abstracts.result_protocol import RunResultProtocol
from throng.errors import CannotCancelNonExistingIsolateError


class ContextIsolateManager:
    manager: 'AbstractManager'
    isolate: Optional[AbstractIsolate]

    def __init__(self, manager: 'AbstractManager') -> None:
        self.manager = manager
        self.isolate = None

    def __enter__(self):
        state = self.manager.read()
        self.isolate = self.manager.get(state)
        return self.isolate

    def __exit__(self, exc_type: Optional[type[BaseException]], exc_value: Optional[BaseException], traceback: Optional[TracebackType]) -> None:
        if self.isolate is None:
            raise CannotCancelNonExistingIsolateError()
        self.isolate.kill()


class AbstractManager(ABC):
    path: Path

    def __init__(self, path: Optional[Union[str, Path]]) -> None:
        if path is None:
            real_path: Path = Path.cwd()
        elif isinstance(path, str):
            real_path = Path(path)
        else:
            real_path = path

        self.path = real_path

    def run(self, command: str, token: AbstractToken = DefaultToken()) -> RunResultProtocol:  # noqa: B008
        state = self.read()
        isolate = self.get(state)
        return isolate.run(command, token=token)

    @property
    def scope(self) -> ContextIsolateManager:
        return ContextIsolateManager(self)

    @abstractmethod
    def get(self, state: bytes) -> AbstractIsolate:
        ...

    @abstractmethod
    def read(self) -> bytes:
        ...
