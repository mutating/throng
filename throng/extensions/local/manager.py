from pathlib import Path
from threading import Lock
from typing import List, Optional, Union

from cantok import AbstractToken, DefaultToken

from throng import AbstractManager
from throng.extensions.local.isolate import LocalIsolate


class LocalManager(AbstractManager):
    def __init__(self, path: Union[str, Path], exclude: Optional[List[str]], prepare: Optional[List[str]] = None, packages: Optional[List[str]] = None) -> None:
        self.lock = Lock()
        super().__init__(path, exclude, prepare, packages)

    def _get(self, state: bytes, token: AbstractToken = DefaultToken()) -> LocalIsolate:  # noqa: B008, ARG002
        return LocalIsolate(self.lock, self.path)

    def read(self) -> bytes:
        return b''
