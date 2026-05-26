from abc import ABC, abstractmethod

from throng.abstracts.isolate import AbstractIsolate


class AbstractThrong(ABC):
    """Define a provider of isolates without exposing its allocation policy."""

    @abstractmethod
    def get_isolate(self) -> AbstractIsolate:
        """Return an isolate suitable for one or more immediate operations."""
