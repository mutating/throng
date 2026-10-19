from abc import ABC, abstractmethod
from typing import List, Type, Union

from cantok import AbstractToken, DefaultToken

from throng.abstracts.meta import (
    Benchmarks,
    IsolateMeta,
    IsolateResourcesDescription,
    Resources,
)
from throng.abstracts.results import RunResultProtocol, SimpleRunResult
from throng.errors import InterruptedChainError, NotSuccessfulRunError


class AbstractIsolate(ABC):
    @property
    def meta(self) -> IsolateMeta:
        description = self.get_resources_description()
        benchmarks = self.get_benchmarks()

        resources = Resources(
            benchmarks=benchmarks,
            shared=description.shared,
            shared_with=description.shared_with,
        )

        return IsolateMeta(
            id=self.get_id(),
            resources=resources,
        )

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
    def install(self, *packages: str, token: AbstractToken = DefaultToken()) -> None:  # noqa: B008
        ...  # pragma: no cover

    @abstractmethod
    def get_benchmarks(self) -> Benchmarks:
        ...  # pragma: no cover

    @abstractmethod
    def get_resources_description(self) -> IsolateResourcesDescription:
        ...  # pragma: no cover

    @abstractmethod
    def get_id(self) -> str:
        """Return an ID unique across plugins and stable for this isolate's lifetime."""
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
