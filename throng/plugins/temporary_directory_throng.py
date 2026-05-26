# mypy: disable-error-code=misc
# skelet's Storage metaclass exposes Any through class-definition metadata.
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional
from uuid import uuid4

from emptylog import EmptyLogger, LoggerProtocol
from skelet import Field, for_tool

from throng.abstracts.throng import AbstractThrong
from throng.errors import InvalidBaseDirectoryError
from throng.plugins.directory_isolate import DirectoryIsolate, DirectoryIsolationConfig
from throng.plugins.empty_lock import EmptyLock


class TemporaryDirectoryIsolationConfig(DirectoryIsolationConfig, sources=for_tool('temporary_directory')):
    """Configure temporary isolates from their independent skelet source."""

    base_directory: Optional[str] = Field(None, doc='existing writable parent directory for created temporary isolate directories')


class TemporaryDirectoryThrong(AbstractThrong):
    """
    Create isolated disposable working directories without serialization.

    With no configured base, directories are managed by
    ``tempfile.TemporaryDirectory``.  With a base, each isolate owns a new
    UUID-hex child directory below that existing writable base.
    """

    def __init__(self, logger: LoggerProtocol = EmptyLogger(), config: Optional[TemporaryDirectoryIsolationConfig] = None) -> None:  # noqa: B008
        """Initialize the inherited logger and temporary-plugin settings."""
        self.logger = logger
        self.config = config if config is not None else TemporaryDirectoryIsolationConfig()

    def get_isolate(self) -> DirectoryIsolate:
        """Create a fresh owned directory and return an isolate with a no-op lock."""
        if self.config.base_directory is None:
            temporary_directory_manager = TemporaryDirectory()
            isolate_directory = Path(temporary_directory_manager.name)

            self.logger.info(f'Creating temporary isolate in stdlib temporary directory "{isolate_directory}".')

            return DirectoryIsolate(
                isolate_directory,
                self.config,
                self.logger,
                lock=EmptyLock(),
                temporary_directory_manager=temporary_directory_manager,
            )

        base_directory = Path(self.config.base_directory)
        self._validate_base_directory(base_directory)

        isolate_directory = base_directory / uuid4().hex

        try:
            isolate_directory.mkdir()
        except PermissionError as error:
            validation_error_message = f'Temporary base directory is not writable: {base_directory}'
            self.logger.exception(validation_error_message)
            raise InvalidBaseDirectoryError(validation_error_message) from error

        self.logger.info(f'Creating temporary isolate "{isolate_directory}" inside base directory "{base_directory}".')

        return DirectoryIsolate(isolate_directory, self.config, self.logger, lock=EmptyLock(), owns_directory=True)

    def _validate_base_directory(self, base_directory: Path) -> None:
        """Reject configured bases that do not exist as directories."""
        validation_error_message = None

        if not base_directory.exists():
            validation_error_message = f'Temporary base directory does not exist: {base_directory}'
        elif not base_directory.is_dir():
            validation_error_message = f'Temporary base path is not a directory: {base_directory}'

        if validation_error_message is not None:
            self.logger.error(validation_error_message)
            raise InvalidBaseDirectoryError(validation_error_message)
