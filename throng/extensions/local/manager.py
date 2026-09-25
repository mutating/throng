from pathlib import Path
from threading import Lock
from typing import List, Optional, Union

from throng import AbstractManager
from throng.extensions.local.isolate import LocalIsolate


class LocalManager(AbstractManager):
    def __init__(self, path: Optional[Union[str, Path]], exclude: Optional[List[str]]) -> None:
        self.lock = Lock()
        super().__init__(path, exclude)

    def get(self, state: bytes) -> LocalIsolate:  # noqa: ARG002
        return LocalIsolate(self.lock)

    def read(self) -> bytes:
        return b''
