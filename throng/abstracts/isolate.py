from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Mapping, Optional, Sequence, Tuple, Union

from cantok import AbstractToken, DefaultToken
from emptylog import EmptyLogger, LoggerProtocol

from throng.result import RunResult


class AbstractIsolate(ABC):
    """
    Define operations available through one execution-environment handle.

    A throng chooses how isolates are created or reused.  Users operate on an
    isolate for concrete actions without depending on backend lifetime or
    reuse policy, and may supply an additional logger or a cancellation token
    to each cancellable action.  Once ``delete`` has succeeded,
    implementations must reject all further actions.
    """

    @abstractmethod
    def run(  # noqa: PLR0913
        self,
        *arguments: Union[str, Path],
        logger: LoggerProtocol = EmptyLogger(),  # noqa: B008
        split: bool = True,
        token: AbstractToken = DefaultToken(),  # noqa: B008
        timeout: Optional[Union[int, float]] = None,
        catch_output: bool = False,
        catch_exceptions: bool = False,
        double_backslash: bool = False,
        env: Optional[Mapping[str, str]] = None,
        add_env: Optional[Mapping[str, str]] = None,
        delete_env: Optional[Union[List[str], Tuple[str, ...]]] = None,
    ) -> RunResult:
        """
        Execute a command inside the isolate and return a library result.

        Implementations choose the isolate working directory and must not let
        callers override it.  A cancelled execution raises the library
        cancellation exception instead of exposing backend-specific state.
        """

    @abstractmethod
    def install(self, what: Union[str, Sequence[str]], logger: LoggerProtocol = EmptyLogger(), token: AbstractToken = DefaultToken()) -> None:  # noqa: B008
        """Install one or more Python dependency specifications for later runs."""

    @abstractmethod
    def dump(self, logger: LoggerProtocol = EmptyLogger(), token: AbstractToken = DefaultToken()) -> bytes:  # noqa: B008
        """Serialize the isolate filesystem into portable archive bytes."""

    @abstractmethod
    def load(self, dump: bytes, logger: LoggerProtocol = EmptyLogger(), token: AbstractToken = DefaultToken()) -> None:  # noqa: B008
        """Replace non-excluded filesystem contents from archive bytes."""

    @abstractmethod
    def delete(self, logger: LoggerProtocol = EmptyLogger()) -> None:  # noqa: B008
        """
        Dispose of the isolate and prevent later operations on this object.

        Deletion deliberately has no cancellation token: cleanup is either
        completed or raises a cleanup error that the caller can handle.
        """
