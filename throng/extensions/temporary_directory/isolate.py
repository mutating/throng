from tempfile import TemporaryDirectory
from threading import Lock
from pathlib import Path
import tarfile
from io import BytesIO

from cantok import AbstractToken, DefaultToken
from locklib import ContextLockProtocol
from suby import SubprocessResult, run

from throng.abstracts.abstract_isolate import AbstractIsolate
from throng.extensions.temporary_directory.read import read_directory
from throng.extensions.temporary_directory.errors import DirectoryDoesNotExistError


class TemporaryDirectoryIsolate(AbstractIsolate):
    def __init__(self, state: bytes) -> None:
        self.lock = Lock()
        self.used = False
        self.directory = TemporaryDirectory()
        self.path = Path(self.directory.name)
        self.set_state(state)

    def run(self, command: str, token: AbstractToken = DefaultToken()) -> SubprocessResult:  # noqa: B008
        with self.lock:
            if self.used:
                raise DirectoryDoesNotExistError()
            return run(command, token=token, catch_output=True, directory=self.path)

    def read(self) -> bytes:
        with self.lock:
            if self.used:
                raise DirectoryDoesNotExistError()
            return read_directory(self.path)

    def set_state(self, state: bytes) -> None:
        with self.lock:
            if self.used:
                raise DirectoryDoesNotExistError()
            with tarfile.open(fileobj=BytesIO(state), mode="r:*") as tar:
                tar.extractall(path=self.path)

    def kill(self) -> None:
        with self.lock:
            if self.used:
                raise DirectoryDoesNotExistError()
            self.directory.cleanup()
            self.used = True
