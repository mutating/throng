from abc import ABC, abstractmethod
from typing import List, Type, Union

from cantok import AbstractToken, DefaultToken

from throng.abstracts.results import RunResultProtocol, SimpleRunResult
from throng.errors import InterruptedChainError, NotSuccessfulRunError


class AbstractIsolate(ABC):
    def __del__(self) -> None:
        self.kill()

    def run(self, command: str, token: AbstractToken = DefaultToken(), exception: Union[bool, BaseException, Type[BaseException]] = False) -> RunResultProtocol:  # noqa: B008
        result = self._run(command, token=token)

        if not result.success:
            exception_message = f'Command {command!r} terminated prematurely, return code {result.returncode}.'

            if exception is True:
                raise NotSuccessfulRunError(exception_message, result)

            if isinstance(exception, BaseException):
                raise exception

            if exception is not False:
                raise exception(exception_message)

        return result

    @abstractmethod
    def _run(self, command: str, token: AbstractToken = DefaultToken()) -> RunResultProtocol:  # noqa: B008
        ...  # pragma: no cover

    @abstractmethod
    def read(self) -> bytes:
        ...  # pragma: no cover

    @abstractmethod
    def kill(self) -> None:
        ...  # pragma: no cover

    @abstractmethod
    def install(self, *packages: str) -> None:
        ...  # pragma: no cover

    def chain(self, *commands: str, token: AbstractToken = DefaultToken(), exception: Union[bool, BaseException, Type[BaseException]] = False) -> List[RunResultProtocol]:  # noqa: B008
        results = []

        for command in commands:
            if token:
                result = self.run(command, token=token, exception=exception)
                results.append(result)
            else:
                exception_message = f'The series of commands was interrupted before executing command {command!r} due to receiving a signal from the cancellation token.'

                if exception is True:
                    raise InterruptedChainError(exception_message)

                if isinstance(exception, BaseException):
                    raise exception

                if exception is not False:
                    raise exception(exception_message)

                results.append(
                    SimpleRunResult(
                        success=False,
                    ),
                )

        return results
