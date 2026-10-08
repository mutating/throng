from pathlib import Path

from cantok import AbstractToken, DefaultToken
from locklib import ContextLockProtocol
from suby import SubprocessResult, run

from throng.abstracts.abstract_isolate import AbstractIsolate
from throng.errors import CannotInstallDependencyError, InterruptedInstallationError


class LocalIsolate(AbstractIsolate):
    def __init__(self, lock: ContextLockProtocol, path: Path = Path()) -> None:
        self.lock = lock
        self.path = path

    def _run(self, command: str, token: AbstractToken = DefaultToken()) -> SubprocessResult:  # noqa: B008
        with self.lock:
            return run(command, token=token, catch_output=True, catch_exceptions=True, directory=self.path)

    def read(self) -> bytes:
        return b''

    def kill(self) -> None:
        pass

    def install(self, *packages: str, token: AbstractToken = DefaultToken()) -> None:  # noqa: B008
        for package in packages:
            if not token:
                raise InterruptedInstallationError(f'The installation of package {package!r} has been cancelled.')
            install_result = self.run(f'pip install {package}', token=token)
            if not install_result.success:
                raise CannotInstallDependencyError(f'The installation of package {package!r} failed.', install_result)
