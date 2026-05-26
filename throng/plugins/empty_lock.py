from types import TracebackType
from typing import Optional, Type


class EmptyLock:
    """
    Provide the context-lock interface while deliberately doing no locking.

    Temporary isolates receive this lock so they can reuse ``DirectoryIsolate``
    without acquiring the serialization policy of the local plugin.
    """

    def __enter__(self) -> None:
        """Enter a no-op critical section."""
        self.acquire()

    def __exit__(self, exc_type: Optional[Type[BaseException]], exc_val: Optional[BaseException], exc_tb: Optional[TracebackType]) -> None:
        """Leave a no-op critical section without suppressing exceptions."""
        self.release()

    def acquire(self) -> None:
        """Accept a lock acquisition request without blocking."""

    def release(self) -> None:
        """Accept a matching lock release request without side effects."""
