from cantok import AbstractToken, DefaultToken
from locklib import ContextLockProtocol
from suby import SubprocessResult, run

from throng.abstracts.abstract_isolate import AbstractIsolate


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
