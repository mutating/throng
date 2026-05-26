from emptylog import EmptyLogger, LoggerProtocol

from throng.abstracts.throng import AbstractThrong
from throng.plugins.local_throng import LocalDirectoryThrong
from throng.plugins.temporary_directory_throng import TemporaryDirectoryThrong
from throng.slots import throngs


@throngs.plugin('local', unique=True)
def create_local_throng(logger: LoggerProtocol = EmptyLogger()) -> AbstractThrong:  # noqa: B008
    """Construct the unique built-in provider for current-directory execution."""
    return LocalDirectoryThrong(logger=logger)


@throngs.plugin('temporary_directory', unique=True)
def create_temporary_directory_throng(logger: LoggerProtocol = EmptyLogger()) -> AbstractThrong:  # noqa: B008
    """Construct the unique built-in provider for temporary-directory execution."""
    return TemporaryDirectoryThrong(logger=logger)
