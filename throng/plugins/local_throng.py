from pathlib import Path
from threading import RLock
from typing import Optional

from emptylog import EmptyLogger, LoggerProtocol

from throng.abstracts.throng import AbstractThrong
from throng.plugins.directory_isolate import DirectoryIsolate, DirectoryIsolationConfig


class LocalDirectoryThrong(AbstractThrong):
    """
    Create isolates that operate in the caller's current working directory.

    Every isolate returned by one throng shares its reentrant lock.  The lock
    is reentrant because dependency installation performs nested ``run`` calls
    while the outer install operation already owns the lock.
    """

    def __init__(self, logger: LoggerProtocol = EmptyLogger(), config: Optional[DirectoryIsolationConfig] = None) -> None:  # noqa: B008
        """Initialize local settings, inherited logging, and operation lock."""
        self.logger = logger
        self.config = config if config is not None else DirectoryIsolationConfig()
        self.lock = RLock()

    def get_isolate(self) -> DirectoryIsolate:
        """Return an isolate bound to the current working directory at creation."""
        working_directory = Path.cwd()

        self.logger.info(f'Creating local isolate in current working directory "{working_directory}".')

        return DirectoryIsolate(working_directory, self.config, self.logger, lock=self.lock)
