from threading import Lock
from pathlib import Path

from throng import AbstractManager
from throng.extensions.local.isolate import LocalIsolate


class LocalManager(AbstractManager):
    def __init__(self, path: Path) -> None:
        self.lock = Lock()
        super().__init__(path)

    def get(self, state: bytes) -> LocalIsolate:
        return LocalIsolate(self.lock)

    def read(self) -> bytes:
        return b''
