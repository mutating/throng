from cantok import AbstractToken, DefaultToken
from locklib import ContextLockProtocol
from suby import SubprocessResult, run

from throng.abstracts.abstract_isolate import AbstractIsolate
from throng.errors import CannotInstallDependencyError


class LocalIsolate(AbstractIsolate):
    def __init__(self, lock: ContextLockProtocol) -> None:
        self.lock = lock

    def run(self, command: str, token: AbstractToken = DefaultToken()) -> SubprocessResult:  # noqa: B008
        with self.lock:
            return run(command, token=token, catch_output=True, catch_exceptions=True)

    def read(self) -> bytes:
        return b''

    def kill(self) -> None:
        pass

    def install(self, *packages: str) -> None:
        for package in packages:
            install_result = self.run(f'pip install {package}')
            if not install_result.success:
                raise CannotInstallDependencyError
