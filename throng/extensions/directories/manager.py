from cantok import AbstractToken, DefaultToken

from throng import AbstractManager
from throng.extensions.directories.isolate import DirectoriesIsolate
from throng.extensions.directories.read import read_directory


class DirectoriesManager(AbstractManager):
    def _get(self, state: bytes, token: AbstractToken = DefaultToken()) -> DirectoriesIsolate:  # noqa: B008, ARG002
        return DirectoriesIsolate(state, self.exclude)

    def read(self) -> bytes:
        return read_directory(self.path, self.exclude)
