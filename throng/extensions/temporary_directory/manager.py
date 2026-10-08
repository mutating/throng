from cantok import AbstractToken, DefaultToken

from throng import AbstractManager
from throng.extensions.temporary_directory.isolate import TemporaryDirectoryIsolate
from throng.extensions.temporary_directory.read import read_directory


class TemporaryDirectoryManager(AbstractManager):
    def _get(self, state: bytes, token: AbstractToken = DefaultToken()) -> TemporaryDirectoryIsolate:  # noqa: B008, ARG002
        return TemporaryDirectoryIsolate(state, self.exclude)

    def read(self) -> bytes:
        return read_directory(self.path, self.exclude)
