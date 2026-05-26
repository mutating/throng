# mypy: disable-error-code=misc
# skelet's Storage metaclass exposes Any through class-definition metadata.
from functools import partial
from io import BytesIO
from os import X_OK, access, environ, pathsep
from os import name as os_name
from pathlib import Path, PurePosixPath
from shutil import Error as ShutilError
from shutil import copyfileobj, move, rmtree
from sys import executable
from tarfile import TarError, TarInfo
from tarfile import open as open_tar
from tempfile import TemporaryDirectory, mkdtemp
from threading import Lock as ThreadLock
from typing import (
    BinaryIO,
    ClassVar,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
    cast,
)
from weakref import finalize

from cantok import AbstractToken, CancellationError, DefaultToken
from dirstree import Crawler
from emptylog import EmptyLogger, LoggerProtocol, LoggersGroup
from locklib import ContextLockProtocol
from pathspec import PathSpec
from pathspec.patterns.gitwildmatch import GitWildMatchPatternError
from skelet import Field, Storage, for_tool
from suby import (
    EnvironmentVariablesConflict,
    RunningCommandError,
    WrongCommandError,
    WrongDirectoryError,
)
from suby import run as run_suby
from suby.subprocess_result import SubprocessResult

from throng.abstracts.isolate import AbstractIsolate
from throng.errors import (
    ArchiveUnpackError,
    CommandExecutionError,
    InstallError,
    InvalidVirtualEnvPathError,
    IsolateDeletedError,
    OperationCancelledError,
)
from throng.result import RunResult


def is_supported_compression_mode(value: str) -> bool:
    """Return whether ``value`` selects a supported archive compression value."""
    return value in ('gzip', 'bz2', 'lzma', 'none')


def are_valid_exclusion_patterns(patterns: List[str]) -> bool:
    """Return whether gitwildmatch exclusion patterns can be compiled."""
    try:
        PathSpec.from_lines('gitwildmatch', patterns)
    except GitWildMatchPatternError:
        return False

    return True


class DirectoryIsolationConfig(Storage, sources=for_tool('local')):
    """
    Configure filesystem-backed isolate behavior shared by built-in plugins.

    This base configuration reads the local plugin's skelet source.  The
    temporary plugin subclasses it to obtain the same fields from its own
    independent source.
    """

    compression: str = Field(
        'gzip',
        doc='compression mode used to write and read isolate snapshot archives',
        validation={'Unsupported compression mode.': is_supported_compression_mode},
    )
    use_venv: bool = Field(True, doc='whether dependency installation uses an isolate-local virtual environment')
    venv_path: str = Field('.venv', doc='relative path of the isolate-local virtual environment')
    dump_exclude: List[str] = Field(
        default_factory=list,
        doc='gitwildmatch patterns omitted from snapshots and preserved during load',
        validation={'Invalid dump exclude pattern.': are_valid_exclusion_patterns},
    )
    exclude_venv: bool = Field(True, doc='whether the effective virtual environment path is excluded from snapshots')
    log_run: bool = Field(False, doc='whether suby emits detailed command execution logs, which can reveal arguments')


class DirectoryIsolate(AbstractIsolate):
    """
    Run isolate operations against a designated filesystem directory.

    A concrete throng supplies the directory, inherited logger, and locking
    policy.  Public operations acquire that lock; a local throng provides a
    reentrant serialization lock while a temporary throng intentionally
    provides :class:`EmptyLock`.  Successful deletion makes this object
    unusable even when the underlying local directory remains present.
    """

    COMPRESSION_TO_TAR_MODE: ClassVar[Dict[str, str]] = {
        'none': 'r:',
        'gzip': 'r:gz',
        'bz2': 'r:bz2',
        'lzma': 'r:xz',
    }
    COMPRESSION_TO_TAR_WRITE_MODE: ClassVar[Dict[str, str]] = {
        'none': 'w',
        'gzip': 'w:gz',
        'bz2': 'w:bz2',
        'lzma': 'w:xz',
    }

    def __init__(  # noqa: PLR0913
        self,
        directory: Path,
        config: DirectoryIsolationConfig,
        logger: LoggerProtocol = EmptyLogger(),  # noqa: B008
        *,
        lock: ContextLockProtocol,
        temporary_directory_manager: Optional['TemporaryDirectory[str]'] = None,
        owns_directory: bool = False,
    ) -> None:
        """
        Bind filesystem state, lifecycle ownership, logging, and locking.

        ``lock`` must support nested entry if its caller will use
        ``install``: installation invokes public ``run`` while still inside
        the operation lock.  ``temporary_directory_manager`` and
        ``owns_directory`` select the cleanup strategy used by ``delete`` and
        best-effort finalization.
        """
        self.directory = directory
        self.config = config
        self.logger = logger
        self.lock = lock
        self.temporary_directory_manager = temporary_directory_manager
        self.owns_directory = owns_directory
        self.directory_finalizer = finalize(self, rmtree, self.directory, ignore_errors=True) if owns_directory else None
        self.lock_only_for_delete = ThreadLock()
        self.deleted = False

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
        Execute a command with this isolate as its forced working directory.

        The passed logger is added to the inherited logger.  Environment
        controls are forwarded to suby after optional venv activation, and
        backend cancellation or execution errors are normalized to throng
        exceptions.
        """
        operation_logger = self._combine_loggers(logger)

        with self.lock:
            self._raise_if_deleted(operation_logger, 'run')

            return self._run_unlocked(
                arguments,
                operation_logger,
                split,
                token,
                timeout,
                catch_output,
                catch_exceptions,
                double_backslash,
                env,
                add_env,
                delete_env,
            )

    def install(self, what: Union[str, Sequence[str]], logger: LoggerProtocol = EmptyLogger(), token: AbstractToken = DefaultToken()) -> None:  # noqa: B008
        """
        Install dependency specifications through an isolate ``run`` call.

        Depending on configuration, the command uses a validated local venv
        or the current Python interpreter.  Dependency strings are not placed
        in lifecycle logs and are redacted from failure diagnostics.
        """
        combined_logger = self._combine_loggers(logger)

        with self.lock:
            self._raise_if_deleted(combined_logger, 'install')

            return self._install_unlocked(what, combined_logger, logger, token)

    def dump(self, logger: LoggerProtocol = EmptyLogger(), token: AbstractToken = DefaultToken()) -> bytes:  # noqa: B008
        """
        Create archive bytes for non-excluded regular isolate files.

        Symbolic links are never serialized.  The configured compression and
        exclusion policy are used, and the effective venv is excluded by
        default.  Cancellation is checked between traversed files rather than
        during one file's tar copy.
        """
        operation_logger = self._combine_loggers(logger)

        with self.lock:
            self._raise_if_deleted(operation_logger, 'dump')

            return self._dump_unlocked(operation_logger, token)

    def load(self, dump: bytes, logger: LoggerProtocol = EmptyLogger(), token: AbstractToken = DefaultToken()) -> None:  # noqa: B008
        """
        Replace non-excluded contents with validated archive bytes.

        Bytes are first extracted into staging and validated for safe archive
        members.  After staging succeeds, commit deliberately runs without
        cancellation checks so token cancellation cannot intentionally leave a
        half-applied tree; a commit failure instead triggers best-effort
        rollback.
        """
        operation_logger = self._combine_loggers(logger)

        with self.lock:
            self._raise_if_deleted(operation_logger, 'load')

            return self._load_unlocked(dump, operation_logger, token)

    def delete(self, logger: LoggerProtocol = EmptyLogger()) -> None:  # noqa: B008
        """
        Dispose of this isolate under its supplied operation-lock policy.

        Temporary isolates remove their owned directory; local isolates only
        transition to the deleted state.  A cleanup failure leaves deletion
        retryable.
        """
        operation_logger = self._combine_loggers(logger)

        with self.lock:
            return self._delete_unlocked(operation_logger)

    def _run_unlocked(  # noqa: PLR0913
        self,
        arguments: Tuple[Union[str, Path], ...],
        logger: LoggerProtocol,
        split: bool,
        token: AbstractToken,
        timeout: Optional[Union[int, float]],
        catch_output: bool,
        catch_exceptions: bool,
        double_backslash: bool,
        env: Optional[Mapping[str, str]],
        add_env: Optional[Mapping[str, str]],
        delete_env: Optional[Union[List[str], Tuple[str, ...]]],
    ) -> RunResult:
        """
        Implement ``run`` after the public method has acquired its lock.

        By default the subprocess backend receives no logger argument and
        therefore uses its empty default logger: ``DirectoryIsolate`` emits
        concise public lifecycle records itself.  Enabling ``log_run``
        forwards this operation's logger to suby, deliberately exposing its
        detailed records, which may contain command arguments.
        """
        try:
            token.check()

            if not arguments:
                logger.error('Run failed because the command is empty.')
                raise CommandExecutionError('Cannot run an empty command.')

            effective_add_env = self._build_run_add_env(add_env, env)

            logger.info(f'Starting run in "{self.directory}".')
            logger.debug(f'Run environment additions: {sorted((effective_add_env or {}).keys())}.')

            backend_run = partial(run_suby, logger=logger) if self.config.log_run else run_suby

            subprocess_result = backend_run(
                *arguments,
                catch_output=catch_output,
                catch_exceptions=catch_exceptions,
                timeout=timeout,
                directory=self.directory,
                split=split,
                double_backslash=double_backslash,
                env=env,
                add_env=effective_add_env,
                delete_env=delete_env,
                token=token,
            )

            if subprocess_result.killed_by_token:
                logger.error('Run operation was cancelled.')
                raise OperationCancelledError('The operation was cancelled.')

            run_result = self._map_subprocess_result(subprocess_result)
            if run_result.returncode != 0:
                logger.error(f'Run completed with non-zero exit code {run_result.returncode}.')
                if not catch_exceptions:
                    raise CommandExecutionError('Command failed with a non-zero exit code.', run_result)
                return run_result

            logger.info('Run completed successfully.')
            return run_result

        except CancellationError as error:
            logger.exception('Run operation was cancelled.')
            raise OperationCancelledError('The operation was cancelled.') from error
        except InvalidVirtualEnvPathError:
            logger.exception('Run failed because the virtual environment is invalid.')
            raise
        except RunningCommandError as error:
            if error.result.killed_by_token:
                logger.exception('Run operation was cancelled.')
                raise OperationCancelledError('The operation was cancelled.') from error

            run_result = self._map_subprocess_result(error.result)
            logger.error(f'Run failed with non-zero exit code {run_result.returncode}.')  # noqa: TRY400 - suby exception may expose command arguments.
            raise CommandExecutionError(f'Command failed or output decoding failed: {error}', run_result) from error
        except (EnvironmentVariablesConflict, UnicodeDecodeError, WrongCommandError, WrongDirectoryError) as error:
            logger.exception('Run failed.')
            raise CommandExecutionError(str(error)) from error

    def _install_unlocked(
        self,
        what: Union[str, Sequence[str]],
        operation_logger: LoggerProtocol,
        nested_run_logger: LoggerProtocol,
        token: AbstractToken,
    ) -> None:
        """
        Implement installation while avoiding duplicate nested run loggers.

        ``nested_run_logger`` is the operation-specific logger received from
        the caller, not the already combined logger; public ``run`` combines
        it with the inherited logger exactly once when installation invokes
        nested commands.
        """
        try:
            self._check_token(token)
            packages = (what,) if isinstance(what, str) else tuple(what)

            if not packages:
                operation_logger.info('No dependencies requested; install completed without changes.')
                return

            if any(package == '' for package in packages):
                raise InstallError('Dependency name cannot be empty.')

            dependency_word = 'dependency' if len(packages) == 1 else 'dependencies'
            operation_logger.info(f'Installing {len(packages)} {dependency_word}.')

            if self.config.use_venv:
                venv_path = self._resolve_venv_path()
                python_path = self._get_venv_python_path(venv_path)

                if not python_path.exists():
                    operation_logger.debug(f'Creating virtual environment at "{venv_path}".')
                    venv_creation_result = self.run(executable, '-m', 'venv', str(venv_path), logger=nested_run_logger, token=token, catch_output=True, catch_exceptions=True, split=False)

                    if not venv_creation_result.success:
                        raise InstallError(venv_creation_result.stderr or venv_creation_result.stdout or 'Virtual environment creation failed.')

                self._validate_venv_python_path(python_path)

                command = (str(python_path), '-m', 'pip', 'install', *packages)
                operation_logger.debug(f'Installing dependencies with virtual environment at "{venv_path}".')
            else:
                command = (executable, '-m', 'pip', 'install', *packages)
                operation_logger.debug('Installing dependencies with the current interpreter.')

            installation_result = self.run(*command, logger=nested_run_logger, token=token, catch_output=True, catch_exceptions=True, split=False)

            if not installation_result.success:
                raise InstallError(installation_result.stderr or installation_result.stdout or 'Dependency installation failed.')

            operation_logger.info('Install completed successfully.')
        except OperationCancelledError:
            operation_logger.exception('Install operation was cancelled.')
            raise
        except (CommandExecutionError, InstallError, InvalidVirtualEnvPathError) as error:
            diagnostic = str(error)
            non_empty_packages = (package for package in packages if package)
            for package in sorted(non_empty_packages, key=len, reverse=True):
                diagnostic = diagnostic.replace(package, '<dependency specification>')

            operation_logger.exception(f'Install failed: {diagnostic}.')

            if isinstance(error, CommandExecutionError):
                raise InstallError(str(error)) from error

            raise

    def _dump_unlocked(self, logger: LoggerProtocol, token: AbstractToken) -> bytes:
        """Build a tar archive from non-excluded regular files after lock checks."""
        try:
            self._check_token(token)

            archive_mode = self.COMPRESSION_TO_TAR_WRITE_MODE[self.config.compression]
            try:
                exclusion_spec = self._create_exclusion_spec()
            except ValueError as error:
                logger.exception(f'Dump failed because the exclude configuration is invalid: {error}.')  # noqa: TRY401 - plain loggers need the diagnostic text.
                raise

            excluded_venv_path = self._resolve_excluded_venv_path()

            logger.info(f'Dumping isolate "{self.directory}".')
            logger.debug(
                f'Dump options: compression={self.config.compression}, '
                f'excludes={self.config.dump_exclude}, excluded_venv_path={excluded_venv_path}.',
            )

            archive_buffer = BytesIO()
            with open_tar(fileobj=archive_buffer, mode=archive_mode) as archive:
                archive.dereference = True
                crawler = Crawler(
                    self.directory,
                    filter=lambda source_path: (
                        not source_path.is_symlink()
                        and not self._is_path_excluded(
                            source_path.relative_to(self.directory).as_posix(),
                            exclusion_spec,
                            excluded_venv_path,
                        )
                    ),
                )

                for source_path in crawler.go(token=token):
                    self._check_token(token)
                    relative_path = source_path.relative_to(self.directory).as_posix()
                    archive.add(source_path, arcname=relative_path, recursive=False)

                self._check_token(token)

            logger.info('Dump completed successfully.')
            return archive_buffer.getvalue()
        except OperationCancelledError:
            logger.exception('Dump operation was cancelled.')
            raise
        except InvalidVirtualEnvPathError as error:
            logger.exception(f'Dump failed because the virtual environment is invalid: {error}.')  # noqa: TRY401 - plain loggers need the diagnostic text.
            raise
        except (OSError, TarError) as error:
            logger.exception(f'Dump failed: {error}.')  # noqa: TRY401 - plain loggers need the diagnostic text.
            raise

    def _load_unlocked(self, dump: bytes, logger: LoggerProtocol, token: AbstractToken) -> None:
        """Stage, validate, and commit loaded contents after lock acquisition."""
        staging_directory: Optional[Path] = None
        backup_directory: Optional[Path] = None

        try:
            self._check_token(token)

            try:
                exclusion_spec = self._create_exclusion_spec()
            except ValueError as error:
                logger.exception(f'Load failed because the exclude configuration is invalid: {error}.')  # noqa: TRY401 - plain loggers need the diagnostic text.
                raise

            excluded_venv_path = self._resolve_excluded_venv_path()
            staging_directory = Path(mkdtemp(prefix='throng-load-'))
            backup_directory = Path(mkdtemp(prefix='throng-backup-'))

            logger.info(f'Loading isolate "{self.directory}".')
            logger.debug(f'Load compression mode: {self.config.compression}.')

            self._extract_to_staging(dump, staging_directory, token, exclusion_spec, excluded_venv_path)
            self._check_token(token)
            self._commit_staged_tree(staging_directory, backup_directory, exclusion_spec, excluded_venv_path)

            logger.info('Load completed successfully.')
        except InvalidVirtualEnvPathError as error:
            logger.exception(f'Load failed because the virtual environment is invalid: {error}.')  # noqa: TRY401 - plain loggers need the diagnostic text.
            raise
        except (OperationCancelledError, ArchiveUnpackError) as error:
            if isinstance(error, OperationCancelledError):
                message = 'Load operation was cancelled.'
            else:
                archive_reason = str(error)
                archive_prefix = 'archive unpack failed: '

                if archive_reason.startswith(archive_prefix):
                    archive_reason = archive_reason[len(archive_prefix):]

                message = f'Archive unpack failed: {archive_reason}.'
            logger.exception(message)
            raise
        except OSError as error:
            reason = error.strerror or str(error)
            unpack_error = ArchiveUnpackError(f'archive unpack failed: cannot create temporary load directories: {reason}')
            logger.exception(f'Archive unpack failed: cannot create temporary load directories: {reason}.')
            raise unpack_error from error
        finally:
            for temporary_directory in (path for path in (staging_directory, backup_directory) if path is not None):
                rmtree(temporary_directory, ignore_errors=True)

    def _delete_unlocked(self, logger: LoggerProtocol) -> None:
        """Mark deletion and apply this isolate's configured cleanup strategy."""
        self._mark_deleted(logger)

        logger.info(f'Deleting isolate "{self.directory}".')

        try:
            if self.temporary_directory_manager is not None:
                self.temporary_directory_manager.cleanup()
            elif self.owns_directory:
                rmtree(self.directory)

            if self.directory_finalizer is not None:
                self.directory_finalizer.detach()
        except OSError as error:
            with self.lock_only_for_delete:
                self.deleted = False

            logger.exception(f'Delete failed: {error}.')  # noqa: TRY401 - plain loggers need the diagnostic text.
            raise

        logger.info('Delete completed successfully.')

    def _extract_to_staging(
        self,
        dump: bytes,
        staging_directory: Path,
        token: AbstractToken,
        exclusion_spec: PathSpec,
        excluded_venv_path: Optional[PurePosixPath],
    ) -> None:
        """
        Validate archive members and copy accepted contents into staging.

        Excluded paths are ignored in incoming bytes so existing excluded
        paths can later be restored.  Cancellation is checked per archive
        member; copying the bytes of one regular file is intentionally a
        bounded but currently non-interruptible unit.
        """
        member_names: Set[str] = set()
        member_kinds: Dict[str, str] = {}

        try:
            archive_mode = self.COMPRESSION_TO_TAR_MODE[self.config.compression]

            with open_tar(fileobj=BytesIO(dump), mode=archive_mode) as archive:
                for member in archive:
                    self._check_token(token)
                    normalized_name = self._validate_member(member, member_names, member_kinds)

                    if not member.isdir() and not member.isfile():
                        raise ArchiveUnpackError(f'Archive contains unsafe entry: {member.name}')

                    if normalized_name is None:
                        continue

                    member_names.add(normalized_name)
                    member_kinds[normalized_name] = 'dir' if member.isdir() else 'file'

                    if self._is_path_excluded(normalized_name, exclusion_spec, excluded_venv_path):
                        continue

                    target_path = staging_directory / normalized_name
                    if member.isdir():
                        target_path.mkdir(parents=True, exist_ok=True)
                    else:
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        source_file = cast(BinaryIO, archive.extractfile(member))

                        with source_file, target_path.open('wb') as target_file:
                            copyfileobj(source_file, target_file)

                        target_path.chmod(member.mode & 0o777)
        except (OSError, TarError) as error:
            raise ArchiveUnpackError(f'archive unpack failed: {error}') from error

    def _validate_member(self, member: TarInfo, member_names: Set[str], member_kinds: Dict[str, str]) -> Optional[str]:
        """
        Return a safe normalized member name or reject an unsafe archive.

        Only empty/current-directory entries yield ``None``.  The method
        rejects dangerous names, duplicate entries, and file/directory
        conflicts before extraction; entry types are checked by the caller.
        """
        raw_name = member.name

        if any(ord(character) < 32 or ord(character) == 127 for character in raw_name):
            raise ArchiveUnpackError(f'Archive contains an unsafe control character in path: {raw_name!r}')
        if '\\' in raw_name:
            raise ArchiveUnpackError(f'Archive contains an unsafe backslash path: {raw_name}')

        member_path = PurePosixPath(raw_name)

        if member_path.is_absolute():
            raise ArchiveUnpackError(f'Archive contains an absolute path: {raw_name}')
        if member_path.parts and len(member_path.parts[0]) == 2 and member_path.parts[0][1] == ':':
            raise ArchiveUnpackError(f'Archive contains an absolute Windows drive path: {raw_name}')
        if '..' in member_path.parts:
            raise ArchiveUnpackError(f'Archive contains path traversal outside the isolate: {raw_name}')

        normalized_name = member_path.as_posix()

        if normalized_name == '.':
            return None
        if normalized_name in member_names:
            raise ArchiveUnpackError(f'Archive contains a duplicate entry: {normalized_name}')

        for known_name, member_kind in member_kinds.items():
            if (member_kind == 'file' and normalized_name.startswith(f'{known_name}/')) or (member.isfile() and known_name.startswith(f'{normalized_name}/')):
                raise ArchiveUnpackError(f'Archive contains a file/directory conflict: {normalized_name}')

        return normalized_name

    def _commit_staged_tree(
        self,
        staging_directory: Path,
        backup_directory: Path,
        exclusion_spec: PathSpec,
        excluded_venv_path: Optional[PurePosixPath],
    ) -> None:
        """
        Replace live non-excluded contents and roll back commit failures.

        Existing contents are first moved to backup.  Staged contents become
        the new source of truth, after which excluded old paths are restored.
        Any failure attempts to reconstruct the prior tree and reports paths
        that could not be restored.
        """
        backup_complete = False
        backed_up_paths: Set[str] = set()
        restored_excluded_paths: List[Path] = []

        try:
            for child_path in self.directory.iterdir():
                move(str(child_path), str(backup_directory / child_path.name))
                backed_up_paths.add(child_path.name)

            backup_complete = True

            for child_path in staging_directory.iterdir():
                move(str(child_path), str(self.directory / child_path.name))

            self._restore_excluded_paths(backup_directory, exclusion_spec, excluded_venv_path, restored_excluded_paths)
        except (ArchiveUnpackError, OSError, ShutilError) as error:
            unrestored_paths: List[str] = []

            if backup_complete:
                for restored_path in reversed(restored_excluded_paths):
                    try:
                        source_path = self.directory / restored_path
                        backup_path = backup_directory / restored_path
                        backup_path.parent.mkdir(parents=True, exist_ok=True)
                        move(str(source_path), str(backup_path))
                    except (OSError, ShutilError):
                        unrestored_paths.append(restored_path.as_posix())

                for child_path in self.directory.iterdir():
                    try:
                        self._remove_path(child_path)
                    except OSError:
                        if child_path.name not in backed_up_paths:
                            unrestored_paths.append(child_path.name)

            for child_path in backup_directory.iterdir():
                if child_path.name not in backed_up_paths:
                    continue

                try:
                    destination_path = self.directory / child_path.name

                    if destination_path.exists() or destination_path.is_symlink():
                        self._remove_path(destination_path)

                    move(str(child_path), str(destination_path))
                except (OSError, ShutilError):
                    unrestored_paths.append(child_path.name)

            unrestored_suffix = f' Unrestored paths: {", ".join(unrestored_paths)}.' if unrestored_paths else ''
            reason = error.strerror if isinstance(error, OSError) and error.strerror else str(error)

            raise ArchiveUnpackError(f'Archive commit failed; rollback attempted. Cause: {reason}.{unrestored_suffix}') from error

    def _restore_excluded_paths(
        self,
        backup_directory: Path,
        exclusion_spec: PathSpec,
        excluded_venv_path: Optional[PurePosixPath],
        restored_paths: List[Path],
    ) -> None:
        """Move excluded backup paths back over newly staged contents."""
        def get_restore_order(source_path: Path) -> Tuple[int, str]:
            """Sort parent paths before descendants during excluded restoration."""
            relative_path = source_path.relative_to(backup_directory)
            return len(relative_path.parts), relative_path.as_posix()

        restored_directory_names: List[str] = []
        source_paths: List[Path] = list(backup_directory.rglob('*'))
        source_paths.sort(key=get_restore_order)

        for source_path in source_paths:
            relative_path = source_path.relative_to(backup_directory)
            relative_name = relative_path.as_posix()

            if any(relative_name == restored_name or relative_name.startswith(f'{restored_name}/') for restored_name in restored_directory_names):
                continue
            if not self._is_path_excluded(relative_name, exclusion_spec, excluded_venv_path):
                continue

            destination_path = self.directory / relative_path
            if destination_path.parent.exists() and not destination_path.parent.is_dir():
                raise ArchiveUnpackError(f'Archive commit failed; excluded path parent is a file: {relative_path.parent.as_posix()}')

            destination_path.parent.mkdir(parents=True, exist_ok=True)

            if destination_path.exists() or destination_path.is_symlink():
                self._remove_path(destination_path)

            move(str(source_path), str(destination_path))
            restored_paths.append(relative_path)

            if destination_path.is_dir() and not destination_path.is_symlink():
                restored_directory_names.append(relative_name)

    def _remove_path(self, path: Path) -> None:
        """Remove one filesystem node without following symbolic links."""
        if path.is_dir() and not path.is_symlink():
            rmtree(path)
        else:
            path.unlink(missing_ok=True)

    def _raise_if_deleted(self, logger: LoggerProtocol, operation_name: str) -> None:
        """Reject an operation after this object has entered its deleted state."""
        with self.lock_only_for_delete:
            deleted = self.deleted

        if deleted:
            logger.error(f'{operation_name.capitalize()} rejected because the isolate has been deleted.')
            raise IsolateDeletedError('Isolate has been deleted.')

    def _mark_deleted(self, logger: LoggerProtocol) -> None:
        """Atomically begin deletion or reject an already deleted isolate."""
        with self.lock_only_for_delete:
            if not self.deleted:
                self.deleted = True
                return

        logger.error('Delete rejected because the isolate has been deleted.')
        raise IsolateDeletedError('Isolate has been deleted.')

    def _map_subprocess_result(self, subprocess_result: SubprocessResult) -> RunResult:
        """Detach public command results from the suby result type."""
        return RunResult(
            id=str(subprocess_result.id),
            stdout=subprocess_result.stdout,
            stderr=subprocess_result.stderr,
            returncode=subprocess_result.returncode,
        )

    def _combine_loggers(self, operation_logger: LoggerProtocol) -> LoggerProtocol:
        """Add an operation logger to inherited logging without duplication."""
        if self.logger is operation_logger or isinstance(operation_logger, EmptyLogger):
            return self.logger
        if isinstance(self.logger, EmptyLogger):
            return operation_logger
        return LoggersGroup(self.logger, operation_logger)

    def _check_token(self, token: AbstractToken) -> None:
        """Raise the library cancellation exception for a cancelled token."""
        try:
            token.check()
        except CancellationError as error:
            raise OperationCancelledError('The operation was cancelled.') from error

    def _resolve_venv_path(self) -> Path:
        """Resolve a configured venv path while keeping it inside the isolate."""
        configured_path = Path(self.config.venv_path)

        if configured_path.is_absolute():
            raise InvalidVirtualEnvPathError(f'Virtual environment path must be relative to the isolate directory, got absolute path: {configured_path}')

        resolved_path = (self.directory / configured_path).resolve()
        isolate_path = self.directory.resolve()

        if resolved_path != isolate_path and isolate_path not in resolved_path.parents:
            raise InvalidVirtualEnvPathError(f'Virtual environment path escapes outside the isolate directory: {configured_path}')

        return resolved_path

    def _get_venv_python_path(self, venv_path: Path) -> Path:
        """Return the platform-specific Python executable path in a venv."""
        if os_name == 'nt':
            return venv_path / 'Scripts' / 'python.exe'

        return venv_path / 'bin' / 'python'

    def _validate_venv_python_path(self, python_path: Path) -> None:
        """Require a runnable Python executable at the configured venv path."""
        if not python_path.exists():
            raise InvalidVirtualEnvPathError(f'Virtual environment python executable is missing: {python_path}')
        if not python_path.is_file():
            raise InvalidVirtualEnvPathError(f'Virtual environment python executable is not a regular file: {python_path}')
        if os_name != 'nt' and not access(python_path, X_OK):
            raise InvalidVirtualEnvPathError(f'Virtual environment python executable is not executable: {python_path}')

    def _build_run_add_env(self, add_env: Optional[Mapping[str, str]], env: Optional[Mapping[str, str]]) -> Optional[Mapping[str, str]]:
        """
        Merge additions with venv activation only when that venv exists.

        Setting ``use_venv`` does not create a virtual environment during
        ``run``; activation variables are injected only after installation has
        created the configured venv directory.
        """
        environment_additions: Dict[str, str] = dict(add_env or {})

        if self.config.use_venv:
            venv_path = self._resolve_venv_path()

            if venv_path.exists():
                python_path = self._get_venv_python_path(venv_path)
                self._validate_venv_python_path(python_path)

                bin_path = python_path.parent
                if 'PATH' in environment_additions:
                    current_path = environment_additions['PATH']
                elif env is not None:
                    current_path = env.get('PATH', '')
                else:
                    current_path = environ.get('PATH', '')

                environment_additions['VIRTUAL_ENV'] = str(venv_path)
                environment_additions['PATH'] = f'{bin_path}{pathsep}{current_path}' if current_path else str(bin_path)

        return environment_additions or None

    def _create_exclusion_spec(self) -> PathSpec:
        """Compile current mutable exclusion settings before each archive action."""
        try:
            return PathSpec.from_lines('gitwildmatch', self.config.dump_exclude)
        except GitWildMatchPatternError as error:
            raise ValueError('Invalid dump exclude pattern.') from error

    def _resolve_excluded_venv_path(self) -> Optional[PurePosixPath]:
        """Return the literal effective venv subtree excluded by default."""
        if not self.config.use_venv or not self.config.exclude_venv:
            return None

        venv_path = self._resolve_venv_path()
        relative_venv_path = venv_path.relative_to(self.directory.resolve()).as_posix()

        return PurePosixPath(relative_venv_path)

    def _is_path_excluded(
        self,
        relative_name: str,
        exclusion_spec: PathSpec,
        excluded_venv_path: Optional[PurePosixPath],
    ) -> bool:
        """Return whether one relative archive path must remain untouched."""
        relative_path = PurePosixPath(relative_name)

        if excluded_venv_path is not None and (relative_path == excluded_venv_path or excluded_venv_path in relative_path.parents):
            return True

        return exclusion_spec.match_file(relative_path.as_posix())
