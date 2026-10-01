from pathlib import Path
from threading import Lock
from typing import List, Optional, Union

from throng import AbstractManager
from throng.extensions.local.isolate import LocalIsolate


class LocalManager(AbstractManager):
    def __init__(self, path: Union[str, Path], exclude: Optional[List[str]], prepare: Optional[List[str]] = None) -> None:
        self.lock = Lock()
        super().__init__(path, exclude, prepare)

    def get(self, state: bytes) -> LocalIsolate:  # noqa: ARG002
        return LocalIsolate(self.lock, self.path, self.prepare)

    def read(self) -> bytes:
        return b''
