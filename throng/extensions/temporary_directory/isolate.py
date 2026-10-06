import tarfile
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from typing import List, Optional

from cantok import AbstractToken, DefaultToken
from suby import SubprocessResult, run

from throng.abstracts.abstract_isolate import AbstractIsolate
from throng.errors import CannotInstallDependencyError
from throng.extensions.temporary_directory.errors import DirectoryDoesNotExistError
from throng.extensions.temporary_directory.read import read_directory


class TemporaryDirectoryIsolate(AbstractIsolate):
    def __init__(self, state: bytes, exclude: Optional[List[str]]) -> None:
        self.lock = Lock()
        self.exclude = exclude
        self.used = False
        self.directory = TemporaryDirectory()
        self.path = Path(self.directory.name)
        self.set_state(state)

    def run(self, command: str, token: AbstractToken = DefaultToken()) -> SubprocessResult:  # noqa: B008
        with self.lock:
            if self.used:
                raise DirectoryDoesNotExistError('You cannot reuse a destroyed isolate.')
            return run(command, token=token, catch_output=True, catch_exceptions=True, directory=self.path)

    def read(self) -> bytes:
        with self.lock:
            if self.used:
                raise DirectoryDoesNotExistError('You cannot re-read the state of a destroyed isolate.')
            return read_directory(self.path, self.exclude)

    def set_state(self, state: bytes) -> None:
        with self.lock:
            if self.used:
                raise DirectoryDoesNotExistError('You cannot reuse a destroyed isolate.')
            with tarfile.open(fileobj=BytesIO(state), mode="r:*") as tar:
                tar.extractall(path=self.path)

    def kill(self) -> None:
        with self.lock:
            self.directory.cleanup()
            self.used = True

    def install(self, *packages: str) -> None:
        for package in packages:
            install_result = self.run(f'pip install {package}')
            if not install_result.success:
                raise CannotInstallDependencyError
