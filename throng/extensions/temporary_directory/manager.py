from pathlib import Path
from threading import Lock

from throng import AbstractManager
from throng.extensions.temporary_directory.isolate import TemporaryDirectoryIsolate
from throng.extensions.temporary_directory.read import read_directory


class TemporaryDirectoryManager(AbstractManager):
    def get(self, state: bytes) -> TemporaryDirectoryIsolate:  # noqa: ARG002
        return TemporaryDirectoryIsolate(self.read())

    def read(self) -> bytes:
        return read_directory(self.path)
