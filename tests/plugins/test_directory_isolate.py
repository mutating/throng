import tempfile
from io import BytesIO
from os import environ, link, pathsep
from os import name as os_name
from pathlib import Path
from shutil import rmtree as remove_tree
from stat import S_IMODE, S_IREAD, S_IRWXU, S_ISGID, S_ISUID, S_ISVTX, S_IWRITE, S_IXUSR
from subprocess import run as run_process
from sys import executable
from tarfile import CHRTYPE, DIRTYPE, LNKTYPE, PAX_FORMAT, SYMTYPE, TarInfo
from tarfile import open as open_tar
from time import monotonic
from typing import Dict, List, Mapping, Optional, Protocol, Tuple, cast

import pytest
from cantok import CounterToken, SimpleToken, TimeoutToken
from emptylog import EmptyLogger, MemoryLogger
from full_match import match
from suby import RunningCommandError, WrongCommandError, WrongDirectoryError
from suby.subprocess_result import SubprocessResult

from tests.helpers import (
    assert_any_message_contains,
    make_tar_bytes,
    read_tree,
)
from throng import (
    ArchiveUnpackError,
    CommandExecutionError,
    InstallError,
    InvalidVirtualEnvPathError,
    OperationCancelledError,
    throngs,
)
from throng.plugins.directory_isolate import DirectoryIsolate
from throng.plugins.directory_isolate import mkdtemp as directory_mkdtemp
from throng.plugins.directory_isolate import move as directory_move
from throng.plugins.directory_isolate import run_suby as directory_run_suby
from throng.plugins.temporary_directory_throng import (
    TemporaryDirectoryIsolationConfig,
    TemporaryDirectoryThrong,
)


class TemporaryIsolateFactory(Protocol):
    def __call__(
        self,
        *,
        compression: str = 'none',
        use_venv: bool = True,
        exclude_venv: bool = True,
        dump_exclude: Optional[List[str]] = None,
        venv_path: str = '.venv',
    ) -> DirectoryIsolate:
        ...


@pytest.fixture
def temporary_isolate(tmp_path: Path) -> TemporaryIsolateFactory:
    def create(
        *,
        compression: str = 'none',
        use_venv: bool = True,
        exclude_venv: bool = True,
        dump_exclude: Optional[List[str]] = None,
        venv_path: str = '.venv',
    ) -> DirectoryIsolate:
        config = TemporaryDirectoryIsolationConfig(
            base_directory=str(tmp_path),
            compression=compression,
            use_venv=use_venv,
            exclude_venv=exclude_venv,
            dump_exclude=dump_exclude or [],
            venv_path=venv_path,
        )

        return TemporaryDirectoryThrong(config=config).get_isolate()

    return create


def can_create_hardlinks_in_temporary_directory():
    """Return whether this test environment supports hardlinks in its temporary filesystem."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        original_file = Path(temporary_directory) / 'original'
        linked_file = Path(temporary_directory) / 'linked'
        original_file.touch()

        try:
            link(str(original_file), str(linked_file))
        except OSError:
            return False

        return True


def test_run_precancelled_token_stops_before_suby(tmp_path, monkeypatch):
    """Verify that a pre-cancelled run token stops before the subprocess runner is called."""
    monkeypatch.chdir(tmp_path)

    logger = MemoryLogger()
    token = SimpleToken().cancel()
    marker = tmp_path / 'created-by-cancelled-command'

    def forbidden_run(*_args, **_kwargs):
        raise AssertionError('suby.run must not be called')

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', forbidden_run)

    with pytest.raises(OperationCancelledError, match=match('The operation was cancelled.')):
        throngs()['local'].get_isolate().run(
            executable,
            '-c',
            f'from pathlib import Path; Path({str(marker)!r}).write_text("created")',
            token=token,
            logger=logger,
    )

    assert not marker.exists()
    assert_any_message_contains(logger.data.exception, 'run', 'cancelled')


def test_run_killed_by_token_result_raises_operation_cancelled(tmp_path, monkeypatch):
    """Verify that a subprocess result marked killed_by_token is normalized to OperationCancelledError."""
    monkeypatch.chdir(tmp_path)

    calls: List[Tuple[object, ...]] = []

    def fake_run(*args, **_kwargs):
        calls.append(args)
        return SubprocessResult(id='cancelled-run', killed_by_token=True)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)

    with pytest.raises(OperationCancelledError, match=match('The operation was cancelled.')):
        throngs()['local'].get_isolate().run('anything')

    assert calls == [('anything',)]


def test_run_killed_by_token_suby_error_raises_operation_cancelled(tmp_path, monkeypatch):
    """Verify that a suby error carrying a cancellation result is normalized to OperationCancelledError."""
    monkeypatch.chdir(tmp_path)
    logger = MemoryLogger()

    def fake_run(*_args, **_kwargs):
        raise RunningCommandError('cancelled', SubprocessResult(id='cancelled-error', killed_by_token=True))

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)

    with pytest.raises(OperationCancelledError, match=match('The operation was cancelled.')):
        throngs()['local'].get_isolate().run('anything', logger=logger)

    assert_any_message_contains(logger.data.exception, 'run', 'cancelled')


def test_run_mid_execution_cancelled(tmp_path, monkeypatch):
    """Verify that a timeout token firing during a long-running command raises OperationCancelledError."""
    monkeypatch.chdir(tmp_path)

    with pytest.raises(OperationCancelledError, match=match('The operation was cancelled.')):
        throngs()['local'].get_isolate().run(executable, '-c', 'import time; time.sleep(5)', token=TimeoutToken(0.1), split=False)


def test_install_precancelled_token_stops_before_pip(tmp_path, monkeypatch):
    """Verify that a pre-cancelled install token stops before any pip or venv subprocess is called."""
    monkeypatch.chdir(tmp_path)

    def fake_run(*_args, **_kwargs):
        raise AssertionError('pip must not be called')

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)

    with pytest.raises(OperationCancelledError, match=match('The operation was cancelled.')):
        throngs()['local'].get_isolate().install('example', token=SimpleToken().cancel())


def test_install_killed_by_token_result_raises_operation_cancelled(tmp_path, monkeypatch):
    """Verify that install normalizes and logs cancellation reported by its nested pip run."""
    calls: List[Tuple[object, ...]] = []
    logger = MemoryLogger()

    def fake_run(*args, **_kwargs):
        calls.append(args)
        return SubprocessResult(id='cancelled-install', killed_by_token=True)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=False)
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()

    with pytest.raises(OperationCancelledError, match=match('The operation was cancelled.')):
        isolate.install('example', logger=logger)

    assert calls == [(executable, '-m', 'pip', 'install', 'example')]
    assert_any_message_contains(logger.data.error, 'run', 'cancelled')
    assert_any_message_contains(logger.data.exception, 'install', 'cancelled')


def test_dump_precancelled_token_stops_before_walk(tmp_path, monkeypatch):
    """Verify that a pre-cancelled dump token raises before walking the isolate tree."""
    monkeypatch.chdir(tmp_path)

    with pytest.raises(OperationCancelledError, match=match('The operation was cancelled.')):
        throngs()['temporary_directory'].get_isolate().dump(token=SimpleToken().cancel())


def test_dump_mid_walk_cancelled(tmp_path):
    """Verify that cancellation during dump traversal raises OperationCancelledError instead of returning bytes."""
    config = TemporaryDirectoryIsolationConfig(compression='none', base_directory=str(tmp_path))
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()

    for index in range(25):
        (isolate.directory / f'{index}.txt').write_text(str(index))

    with pytest.raises(OperationCancelledError, match=match('The operation was cancelled.')):
        isolate.dump(token=CounterToken(5))


def test_load_precancelled_token_stops_before_staging(tmp_path):
    """Verify that pre-cancelled load logs cancellation and leaves the isolate tree untouched before staging."""
    config = TemporaryDirectoryIsolationConfig(compression='none', base_directory=str(tmp_path))
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()
    logger = MemoryLogger()
    (isolate.directory / 'old.txt').write_text('old')

    with pytest.raises(OperationCancelledError, match=match('The operation was cancelled.')):
        isolate.load(make_tar_bytes({'new.txt': b'new'}), logger=logger, token=SimpleToken().cancel())

    assert (isolate.directory / 'old.txt').read_text() == 'old'
    assert not (isolate.directory / 'new.txt').exists()
    assert_any_message_contains(logger.data.exception, 'load', 'cancelled')


def test_load_cancellation_during_staging(tmp_path, monkeypatch):
    """Verify that cancellation during load staging keeps the original tree and removes staging directories."""
    config = TemporaryDirectoryIsolationConfig(compression='none', base_directory=str(tmp_path))
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()
    (isolate.directory / 'old.txt').write_text('old')
    archive = make_tar_bytes({f'{index}.txt': str(index).encode() for index in range(25)})
    created_temp_dirs: List[Path] = []

    def tracking_mkdtemp(*args, **kwargs):
        path = Path(directory_mkdtemp(*args, **kwargs))
        created_temp_dirs.append(path)
        return str(path)

    monkeypatch.setattr('throng.plugins.directory_isolate.mkdtemp', tracking_mkdtemp)

    with pytest.raises(OperationCancelledError, match=match('The operation was cancelled.')):
        isolate.load(archive, token=CounterToken(5))

    assert (isolate.directory / 'old.txt').read_text() == 'old'
    assert created_temp_dirs
    assert all(not path.exists() for path in created_temp_dirs)


def test_load_cancellation_does_not_wait_for_all_archive_headers(tmp_path):
    """Verify that cancellation is observed while traversing a large tar member list, before all headers are read."""
    config = TemporaryDirectoryIsolationConfig(compression='none', base_directory=str(tmp_path))
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()
    archive = make_tar_bytes({f'{index}.txt': b'' for index in range(80_000)})

    started_at = monotonic()
    with pytest.raises(OperationCancelledError, match=match('The operation was cancelled.')):
        isolate.load(archive, token=TimeoutToken(0.005))
    elapsed = monotonic() - started_at

    assert elapsed < 0.5


def test_load_ignores_cancellation_after_commit_has_started(tmp_path, monkeypatch):
    """Verify the plan guarantee that token cancellation during load commit does not interrupt tree replacement."""
    config = TemporaryDirectoryIsolationConfig(compression='none', base_directory=str(tmp_path))
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()
    token = SimpleToken()
    (isolate.directory / 'old.txt').write_text('old')
    cancellation_triggered = False

    def move_then_cancel_during_commit(source, destination):
        nonlocal cancellation_triggered
        result = directory_move(source, destination)
        destination_path = Path(destination)

        if not cancellation_triggered and destination_path.parent.name.startswith('throng-backup-'):
            token.cancel()
            cancellation_triggered = True

        return result

    monkeypatch.setattr('throng.plugins.directory_isolate.move', move_then_cancel_during_commit)

    isolate.load(make_tar_bytes({'new.txt': b'new'}), token=token)

    assert cancellation_triggered is True
    assert read_tree(isolate.directory) == {'new.txt': b'new'}


def test_load_success_removes_staging_and_backup_directories(tmp_path, monkeypatch):
    """Verify that successful load removes both temporary directories created for staging and rollback."""
    config = TemporaryDirectoryIsolationConfig(compression='none', base_directory=str(tmp_path))
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()
    created_temp_dirs: List[Path] = []

    def tracking_mkdtemp(*args, **kwargs):
        path = Path(directory_mkdtemp(*args, **kwargs))
        created_temp_dirs.append(path)
        return str(path)

    monkeypatch.setattr('throng.plugins.directory_isolate.mkdtemp', tracking_mkdtemp)

    isolate.load(make_tar_bytes({'new.txt': b'new'}))

    assert (isolate.directory / 'new.txt').read_bytes() == b'new'
    assert len(created_temp_dirs) == 2
    assert all(not path.exists() for path in created_temp_dirs)


def test_load_validation_failure_removes_staging_and_backup_directories(tmp_path, monkeypatch):
    """Verify that validation failure leaves no staging or rollback directory behind."""
    config = TemporaryDirectoryIsolationConfig(compression='none', base_directory=str(tmp_path))
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()
    created_temp_dirs: List[Path] = []

    def tracking_mkdtemp(*args, **kwargs):
        path = Path(directory_mkdtemp(*args, **kwargs))
        created_temp_dirs.append(path)
        return str(path)

    monkeypatch.setattr('throng.plugins.directory_isolate.mkdtemp', tracking_mkdtemp)

    with pytest.raises(ArchiveUnpackError, match=match('Archive contains an absolute path: /outside.txt')):
        isolate.load(make_tar_bytes({'/outside.txt': b'unsafe'}))

    assert len(created_temp_dirs) == 2
    assert all(not path.exists() for path in created_temp_dirs)


def test_run_empty_arguments(tmp_path, monkeypatch):
    """Verify that calling run without command arguments raises CommandExecutionError instead of silently succeeding."""
    monkeypatch.chdir(tmp_path)

    with pytest.raises(CommandExecutionError, match=match('Cannot run an empty command.')):
        throngs()['local'].get_isolate().run()


def test_run_split_true_false(tmp_path, monkeypatch):
    """Verify that run forwards the requested split flag to the subprocess runner unchanged."""
    monkeypatch.chdir(tmp_path)

    observed: List[bool] = []

    def fake_run(*_args, **kwargs):
        observed.append(kwargs['split'])
        return SubprocessResult(id='split', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)
    isolate = throngs()['local'].get_isolate()

    isolate.run('one two', split=True)
    isolate.run('one two', split=False)

    assert observed == [True, False]


def test_run_default_split_parses_quoted_command_string(tmp_path, monkeypatch):
    """Verify that default split=True parses a quoted command string without an explicit split argument."""
    monkeypatch.chdir(tmp_path)

    result = throngs()['local'].get_isolate().run('printf "one two"', catch_output=True)

    assert result.stdout == 'one two'
    assert result.returncode == 0
    assert result.success is True


def test_run_double_backslash_forwarded_to_suby(tmp_path, monkeypatch):
    """Verify that run forwards the double_backslash flag to the subprocess runner unchanged."""
    monkeypatch.chdir(tmp_path)

    observed: List[bool] = []

    def fake_run(*_args, **kwargs):
        observed.append(kwargs['double_backslash'])
        return SubprocessResult(id='double-backslash', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)
    isolate = throngs()['local'].get_isolate()

    isolate.run('anything')
    isolate.run('anything', double_backslash=True)

    assert observed == [False, True]


def test_run_directory_forwarded_to_suby(tmp_path, monkeypatch):
    """Verify that run always forwards the isolate directory to the subprocess runner."""
    observed_directories: List[Path] = []
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=False)
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()

    def fake_run(*_args, **kwargs):
        observed_directories.append(kwargs['directory'])
        return SubprocessResult(id='directory', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)

    isolate.run('anything')

    assert observed_directories == [isolate.directory]


def test_run_env_override_visible_to_subprocess(tmp_path, monkeypatch):
    """Verify that an explicit env mapping replaces the child process environment and is visible to the command."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('THRONG_PARENT_ONLY_ENV', 'parent')

    result = throngs()['local'].get_isolate().run(
        executable,
        '-c',
        'import os; print(os.environ.get("THRONG_TEST_ENV", "")); print(os.environ.get("THRONG_PARENT_ONLY_ENV", ""))',
        catch_output=True,
        env={'THRONG_TEST_ENV': 'override'},
        split=False,
    )

    assert result.stdout is not None
    assert result.stdout.splitlines() == ['override', '']


def test_run_add_env_visible_to_subprocess(tmp_path, monkeypatch):
    """Verify that add_env adds variables to the child process environment."""
    monkeypatch.chdir(tmp_path)

    result = throngs()['local'].get_isolate().run(
        executable,
        '-c',
        'import os; print(os.environ.get("THRONG_TEST_ADD_ENV", ""))',
        catch_output=True,
        add_env={'THRONG_TEST_ADD_ENV': 'added'},
        split=False,
    )

    assert result.stdout is not None
    assert result.stdout.strip() == 'added'


def test_run_delete_env_hidden_from_subprocess(tmp_path, monkeypatch):
    """Verify that delete_env removes selected parent environment variables only from the child process."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('THRONG_TEST_DELETE_ENV', 'delete-me')

    result = throngs()['local'].get_isolate().run(
        executable,
        '-c',
        'import os; print(os.environ.get("THRONG_TEST_DELETE_ENV", ""))',
        catch_output=True,
        delete_env=['THRONG_TEST_DELETE_ENV'],
        split=False,
    )

    assert result.stdout is not None
    assert result.stdout.strip() == ''
    assert environ['THRONG_TEST_DELETE_ENV'] == 'delete-me'


def test_run_combines_env_add_env_and_delete_env(tmp_path, monkeypatch):
    """Verify that env, add_env, and delete_env combine into the child process environment."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('THRONG_ENV_REMOVED', 'remove-me')

    result = throngs()['local'].get_isolate().run(
        executable,
        '-c',
        (
            'import os; '
            'print(os.environ.get("THRONG_ENV_BASE", "")); '
            'print(os.environ.get("THRONG_ENV_ADDED", "")); '
            'print(os.environ.get("THRONG_ENV_REMOVED", ""))'
        ),
        catch_output=True,
        env={'THRONG_ENV_BASE': 'base'},
        add_env={'THRONG_ENV_ADDED': 'added'},
        delete_env=['THRONG_ENV_REMOVED'],
        split=False,
    )

    assert result.stdout is not None
    assert result.stdout.splitlines() == ['base', 'added', '']


def test_run_env_conflict_raises_command_error_and_logs_failure(tmp_path, monkeypatch):
    """Verify that suby env/add/delete conflicts are naturally wrapped as CommandExecutionError and logged."""
    monkeypatch.chdir(tmp_path)
    logger = MemoryLogger()

    with pytest.raises(CommandExecutionError, match=match('Environment variables cannot be both set via env/add_env and deleted via delete_env: THRONG_ENV_CONFLICT.')):
        throngs()['local'].get_isolate().run(
            executable,
            '-c',
            'print("never")',
            env={'THRONG_ENV_CONFLICT': 'value'},
            delete_env=['THRONG_ENV_CONFLICT'],
            logger=logger,
            split=False,
        )

    assert_any_message_contains(logger.data.exception, 'run', 'failed')


def test_run_malformed_expression_normalizes_wrong_command_error(tmp_path):
    """
    Verify that the public run API does not expose suby's parsing error type.

    A naturally malformed quoted command makes suby raise
    ``WrongCommandError``; throng must expose ``CommandExecutionError`` and
    retain the backend failure only as its chained cause.
    """
    logger = MemoryLogger()
    isolate = TemporaryDirectoryThrong(
        config=TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=False),
    ).get_isolate()

    with pytest.raises(CommandExecutionError, match=match('The expression ""unterminated" cannot be parsed.')) as raised:
        isolate.run('"unterminated', logger=logger)

    assert isinstance(raised.value.__cause__, WrongCommandError)
    assert_any_message_contains(logger.data.exception, 'run', 'failed')


def test_run_missing_isolate_directory_normalizes_wrong_directory_error(tmp_path):
    """
    Verify that the public run API does not expose suby's directory error type.

    Removing a temporary isolate directory externally makes suby reject its
    forced working directory; throng must expose ``CommandExecutionError`` and
    retain ``WrongDirectoryError`` only as its chained cause.
    """
    logger = MemoryLogger()
    isolate = TemporaryDirectoryThrong(
        config=TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=False),
    ).get_isolate()
    remove_tree(isolate.directory)

    with pytest.raises(CommandExecutionError, match=match(f"The directory '{isolate.directory}' does not exist.")) as raised:
        isolate.run('printf never', logger=logger)

    assert isinstance(raised.value.__cause__, WrongDirectoryError)
    assert_any_message_contains(logger.data.exception, 'run', 'failed')


def test_run_nonzero_raises_by_default(tmp_path, monkeypatch):
    """Verify that a non-zero command raises throng CommandExecutionError by default and does not leak suby errors."""
    monkeypatch.chdir(tmp_path)

    expected_message = f'Command failed or output decoding failed: Error when executing the command "{executable} -c "import sys; sys.exit(9)"".'
    with pytest.raises(CommandExecutionError, match=match(expected_message)) as error:
        throngs()['local'].get_isolate().run(executable, '-c', 'import sys; sys.exit(9)', split=False)

    assert not isinstance(error.value, RunningCommandError)


def test_run_nonzero_catch_exceptions_returns_result(tmp_path, monkeypatch):
    """Verify that catch_exceptions=True returns an unsuccessful RunResult for a non-zero command."""
    monkeypatch.chdir(tmp_path)

    result = throngs()['local'].get_isolate().run(executable, '-c', 'import sys; sys.exit(9)', catch_exceptions=True, split=False)

    assert result.returncode == 9
    assert result.success is False


def test_run_nonzero_backend_result_raises_by_default(tmp_path, monkeypatch):
    """Verify that run raises the throng command error if a backend returns a non-zero result by default."""
    monkeypatch.chdir(tmp_path)

    def fake_run(*_args, **_kwargs):
        return SubprocessResult(id='nonzero-result', stderr='failed', returncode=2)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)

    with pytest.raises(CommandExecutionError, match=match('Command failed with a non-zero exit code.')) as raised:
        throngs()['local'].get_isolate().run('anything')

    assert raised.value.result is not None
    assert raised.value.result.stderr == 'failed'
    assert raised.value.result.returncode == 2
    assert raised.value.result.success is False


def test_run_startup_failure_maps_error(tmp_path, monkeypatch):
    """Verify that a missing executable is wrapped in CommandExecutionError with an unsuccessful mapped result."""
    monkeypatch.chdir(tmp_path)

    with pytest.raises(CommandExecutionError, match=match('Command failed or output decoding failed: The executable for the command "not-a-real-throng-command" was not found.')) as error:
        throngs()['local'].get_isolate().run('not-a-real-throng-command')

    assert error.value.result is not None
    assert error.value.result.success is False


def test_run_timeout_cancelled(tmp_path, monkeypatch):
    """Verify that a subprocess timeout is normalized to OperationCancelledError."""
    monkeypatch.chdir(tmp_path)

    with pytest.raises(OperationCancelledError, match=match('The operation was cancelled.')):
        throngs()['local'].get_isolate().run(executable, '-c', 'import time; time.sleep(5)', timeout=0.1, split=False)


def test_run_non_utf8_output_behavior(tmp_path, monkeypatch):
    """Verify that non-UTF-8 captured output is surfaced as a command execution failure."""
    monkeypatch.chdir(tmp_path)

    with pytest.raises(CommandExecutionError, match=match("'utf-8' codec can't decode byte 0xff in position 0: invalid start byte")):
        throngs()['local'].get_isolate().run(
            executable,
            '-c',
            'import sys; sys.stdout.buffer.write(b"\\xff")',
            catch_output=True,
            split=False,
        )


def test_install_use_venv_creates_missing_venv(tmp_path, monkeypatch):
    """Verify that install creates the configured virtual environment when it is missing."""
    calls: List[Tuple[object, ...]] = []

    def fake_run(*args, **_kwargs):
        calls.append(args)
        if '-m' in args and 'venv' in args:
            venv_path = Path(args[-1])
            (venv_path / 'bin').mkdir(parents=True)
            (venv_path / 'bin' / 'python').write_text('')
            (venv_path / 'bin' / 'python').chmod(S_IREAD | S_IWRITE | S_IXUSR)
        return SubprocessResult(id='install', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=True)
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()

    isolate.install('example')

    assert (isolate.directory / '.venv').exists()
    assert any('-m' in call and 'venv' in call for call in calls)


def test_install_use_venv_uses_existing_venv(tmp_path, monkeypatch):
    """Verify that install reuses an existing virtual environment instead of recreating it."""
    calls: List[Tuple[object, ...]] = []

    def fake_run(*args, **_kwargs):
        calls.append(args)
        return SubprocessResult(id='install', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=True)
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()
    venv_python = isolate.directory / '.venv' / 'bin' / 'python'
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text('')
    venv_python.chmod(S_IREAD | S_IWRITE | S_IXUSR)

    isolate.install('example')

    assert not any('-m' in call and 'venv' in call for call in calls)
    assert any(call[0] == str(venv_python) for call in calls)


def test_install_custom_valid_venv_path(tmp_path, monkeypatch):
    """Verify that a custom relative venv_path is created and then activated by run."""
    calls: List[Tuple[Tuple[object, ...], Dict[str, object]]] = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        if '-m' in args and 'venv' in args:
            venv_path = Path(args[-1])
            (venv_path / 'bin').mkdir(parents=True)
            (venv_path / 'bin' / 'python').write_text('')
            (venv_path / 'bin' / 'python').chmod(S_IREAD | S_IWRITE | S_IXUSR)
        return SubprocessResult(id='install', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=True, venv_path='custom/env')
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()

    isolate.install('example')

    expected_venv_path = isolate.directory / 'custom' / 'env'

    assert expected_venv_path.exists()
    assert any(str(expected_venv_path) in [str(part) for part in args] for args, _kwargs in calls)

    isolate.run('anything')

    latest_run_call = calls[-1]

    assert latest_run_call[0] == ('anything',)
    assert isinstance(latest_run_call[1]['add_env'], dict)
    assert latest_run_call[1]['add_env']['VIRTUAL_ENV'] == str(expected_venv_path)
    assert str(expected_venv_path / 'bin') in str(latest_run_call[1]['add_env']['PATH'])


def test_install_venv_creation_failure_raises_install_error(tmp_path, monkeypatch):
    """Verify that virtual environment creation failure raises InstallError before pip is invoked."""
    calls: List[Tuple[object, ...]] = []

    def fake_run(*args, **_kwargs):
        calls.append(args)
        return SubprocessResult(id='venv-fail', stderr='venv failed', returncode=2)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=True)
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()

    with pytest.raises(InstallError, match=match('venv failed')):
        isolate.install('example')

    assert calls == [(executable, '-m', 'venv', str(isolate.directory / '.venv'))]


def test_install_rejects_venv_command_that_does_not_create_python(tmp_path, monkeypatch):
    """Verify that install rejects a reported-successful venv creation when its Python executable is absent."""
    logger = MemoryLogger()

    def fake_run(*_args, **_kwargs):
        return SubprocessResult(id='venv-incomplete', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=True)
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()
    python_path = isolate.directory / '.venv' / 'bin' / 'python'

    with pytest.raises(InvalidVirtualEnvPathError, match=match(f'Virtual environment python executable is missing: {python_path}')):
        isolate.install('example', logger=logger)

    assert_any_message_contains(logger.data.exception, 'install', 'virtual environment python executable is missing')


def test_install_absolute_venv_path_rejected(tmp_path):
    """Verify that an absolute venv_path is rejected as escaping the isolate directory."""
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), venv_path=str(tmp_path / 'outside'))
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()

    with pytest.raises(InvalidVirtualEnvPathError, match=match(f'Virtual environment path must be relative to the isolate directory, got absolute path: {tmp_path / "outside"}')):
        isolate.install('example')


def test_install_parent_escape_venv_path_rejected(tmp_path):
    """Verify that a parent-directory venv_path is rejected as escaping the isolate directory."""
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), venv_path='../venv')
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()

    with pytest.raises(InvalidVirtualEnvPathError, match=match('Virtual environment path escapes outside the isolate directory: ../venv')):
        isolate.install('example')


@pytest.mark.parametrize('operation_name', ['dump', 'load'])
def test_archive_operation_invalid_venv_path_is_logged(tmp_path, operation_name):
    """Verify that dump and load log configuration failures raised while computing venv exclusions."""
    logger = MemoryLogger()
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), venv_path='../outside', compression='none')
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()

    operations = {
        'dump': lambda: isolate.dump(logger=logger),
        'load': lambda: isolate.load(make_tar_bytes({'file.txt': b'content'}), logger=logger),
    }

    with pytest.raises(InvalidVirtualEnvPathError, match=match('Virtual environment path escapes outside the isolate directory: ../outside')):
        operations[operation_name]()

    assert_any_message_contains(logger.data.exception, operation_name, 'virtual environment', 'invalid')


def test_install_use_venv_false_global_pip(tmp_path, monkeypatch):
    """Verify that use_venv=False installs with the current interpreter instead of a virtual environment."""
    calls: List[Tuple[object, ...]] = []

    def fake_run(*args, **_kwargs):
        calls.append(args)
        return SubprocessResult(id='global-install', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=False)
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()

    isolate.install('example')

    assert calls == [(executable, '-m', 'pip', 'install', 'example')]


def test_install_multiple_packages(tmp_path, monkeypatch):
    """Verify that installing multiple packages forwards all package names to pip."""
    calls: List[Tuple[object, ...]] = []

    def fake_run(*args, **_kwargs):
        calls.append(args)
        return SubprocessResult(id='global-install', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=False)
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()

    isolate.install(('first-package', 'second-package'))

    assert calls == [(executable, '-m', 'pip', 'install', 'first-package', 'second-package')]


def test_install_empty_package_sequence_is_noop(tmp_path, monkeypatch):
    """Verify that an empty package sequence does not invoke pip or venv creation."""
    calls: List[Tuple[object, ...]] = []

    def fake_run(*args, **_kwargs):
        calls.append(args)
        return SubprocessResult(id='global-install', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=False)
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()

    isolate.install(())

    assert calls == []


@pytest.mark.parametrize('package_request', ['', ('',)])
def test_install_empty_package_name_is_rejected_before_pip(tmp_path, monkeypatch, package_request):
    """Verify that empty dependency names are rejected before any pip or venv subprocess is called."""
    calls: List[Tuple[object, ...]] = []

    def fake_run(*args, **_kwargs):
        calls.append(args)
        return SubprocessResult(id='global-install', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=False)
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()

    with pytest.raises(InstallError, match=match('Dependency name cannot be empty.')):
        isolate.install(package_request)

    assert calls == []


def test_install_failure(tmp_path, monkeypatch):
    """Verify that a pip failure is wrapped as InstallError with the pip diagnostic."""

    def fake_run(*_args, **_kwargs):
        return SubprocessResult(id='pip-fail', stderr='bad package', returncode=2)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=False)
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()

    with pytest.raises(InstallError, match=match('bad package')):
        isolate.install('bad-package')


def test_install_wraps_nested_command_execution_error(tmp_path, monkeypatch):
    """Verify that install surfaces a nested run startup failure as InstallError and logs it."""
    logger = MemoryLogger()
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=False)
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()

    def failing_run(_self, *_args, **_kwargs):
        raise CommandExecutionError('pip command could not be started.')

    monkeypatch.setattr(DirectoryIsolate, 'run', failing_run)

    with pytest.raises(InstallError, match=match('pip command could not be started.')):
        isolate.install('example', logger=logger)

    assert_any_message_contains(logger.data.exception, 'install', 'pip command could not be started')


def test_run_activates_existing_valid_venv(tmp_path, monkeypatch):
    """Verify that run activates an existing valid virtual environment through VIRTUAL_ENV and PATH."""
    observed_env: Optional[Mapping[str, str]] = None

    def fake_run(*_args, **kwargs):
        nonlocal observed_env
        observed_env = kwargs.get('add_env')
        return SubprocessResult(id='venv-run', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=True)
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()
    venv_python = isolate.directory / '.venv' / 'bin' / 'python'
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text('')
    venv_python.chmod(S_IREAD | S_IWRITE | S_IXUSR)

    isolate.run('anything')

    assert observed_env is not None
    assert observed_env['VIRTUAL_ENV'] == str(isolate.directory / '.venv')
    assert str((isolate.directory / '.venv' / 'bin')) in observed_env['PATH']


def test_run_does_not_activate_virtual_environment_before_it_exists(tmp_path, monkeypatch):
    """Verify the plan rule that use_venv alone does not inject activation variables before install creates the venv."""
    observed_add_env: Optional[Mapping[str, str]] = {'unexpected': 'value'}

    def fake_run(*_args, **kwargs):
        nonlocal observed_add_env
        observed_add_env = kwargs.get('add_env')
        return SubprocessResult(id='no-venv-yet', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=True)
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()

    isolate.run('anything')

    assert not (isolate.directory / '.venv').exists()
    assert observed_add_env is None


def test_run_venv_path_prepends_user_add_env_path(tmp_path, monkeypatch):
    """Verify that the venv bin directory is prepended before a user-provided add_env PATH."""
    observed_env: Optional[Mapping[str, str]] = None

    def fake_run(*_args, **kwargs):
        nonlocal observed_env
        observed_env = kwargs.get('add_env')
        return SubprocessResult(id='venv-run', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=True)
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()
    venv_python = isolate.directory / '.venv' / 'bin' / 'python'
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text('')
    venv_python.chmod(S_IREAD | S_IWRITE | S_IXUSR)

    isolate.run('anything', add_env={'PATH': 'custom-bin'})

    assert observed_env is not None
    assert observed_env['PATH'] == f'{venv_python.parent}{pathsep}custom-bin'


def test_run_venv_path_uses_user_env_path_when_add_env_path_is_absent(tmp_path, monkeypatch):
    """Verify that the venv bin directory is prepended before an env PATH when add_env lacks PATH."""
    observed_env: Optional[Mapping[str, str]] = None
    observed_add_env: Optional[Mapping[str, str]] = None

    def fake_run(*_args, **kwargs):
        nonlocal observed_env, observed_add_env
        observed_env = kwargs.get('env')
        observed_add_env = kwargs.get('add_env')
        return SubprocessResult(id='venv-run', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=True)
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()
    venv_python = isolate.directory / '.venv' / 'bin' / 'python'
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text('')
    venv_python.chmod(S_IREAD | S_IWRITE | S_IXUSR)

    isolate.run('anything', env={'PATH': 'env-bin', 'ONLY_ENV': 'visible'})

    assert observed_env == {'PATH': 'env-bin', 'ONLY_ENV': 'visible'}
    assert observed_add_env is not None
    assert observed_add_env['PATH'] == f'{venv_python.parent}{pathsep}env-bin'


def test_run_venv_path_respects_explicit_empty_environment(tmp_path):
    """Verify that venv activation does not restore the parent PATH when env explicitly replaces it with nothing."""
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=True)
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()
    venv_python = isolate.directory / '.venv' / 'bin' / 'python'
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text('')
    venv_python.chmod(S_IREAD | S_IWRITE | S_IXUSR)

    result = isolate.run(
        executable,
        '-c',
        'import os; print(os.environ.get("PATH", ""))',
        catch_output=True,
        env={},
        split=False,
    )

    assert result.stdout is not None
    assert result.stdout.strip() == str(venv_python.parent.resolve())


def test_run_broken_venv_error(tmp_path):
    """Verify that run rejects a broken virtual environment when its python executable is missing."""
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=True)
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()
    (isolate.directory / '.venv').mkdir()

    with pytest.raises(InvalidVirtualEnvPathError, match=match(f'Virtual environment python executable is missing: {isolate.directory / ".venv" / "bin" / "python"}')):
        isolate.run('anything')


@pytest.mark.parametrize('operation_name', ['run', 'install'])
def test_operations_reject_directory_as_venv_python_executable(tmp_path, monkeypatch, operation_name):
    """Verify that run and install reject a venv whose Python executable path is a directory."""
    calls: List[Tuple[object, ...]] = []

    def fake_run(*args, **_kwargs):
        calls.append(args)
        return SubprocessResult(id='unexpected', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=True)
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()
    python_path = isolate.directory / '.venv' / 'bin' / 'python'
    python_path.mkdir(parents=True)
    operations = {
        'run': lambda: isolate.run('anything'),
        'install': lambda: isolate.install('example'),
    }

    with pytest.raises(InvalidVirtualEnvPathError, match=match(f'Virtual environment python executable is not a regular file: {python_path}')):
        operations[operation_name]()

    assert calls == []


@pytest.mark.skipif(os_name == 'nt', reason='POSIX executable permission checks do not apply on Windows')
@pytest.mark.parametrize('operation_name', ['run', 'install'])
def test_operations_reject_non_executable_venv_python(tmp_path, monkeypatch, operation_name):
    """Verify that run and install reject a POSIX venv Python file without execute permission."""
    calls: List[Tuple[object, ...]] = []

    def fake_run(*args, **_kwargs):
        calls.append(args)
        return SubprocessResult(id='unexpected', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=True)
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()
    python_path = isolate.directory / '.venv' / 'bin' / 'python'
    python_path.parent.mkdir(parents=True)
    python_path.write_text('')
    python_path.chmod(S_IREAD | S_IWRITE)
    operations = {
        'run': lambda: isolate.run('anything'),
        'install': lambda: isolate.install('example'),
    }

    with pytest.raises(InvalidVirtualEnvPathError, match=match(f'Virtual environment python executable is not executable: {python_path}')):
        operations[operation_name]()

    assert calls == []


def test_run_activates_windows_virtual_environment_python_layout(tmp_path, monkeypatch):
    """The plan requires platform-specific venv activation; this checks the Windows Scripts layout."""
    observed_add_env: Dict[str, str] = {}

    def fake_run(*_args, **kwargs):
        observed_add_env.update(kwargs['add_env'])
        return SubprocessResult(id='windows-venv', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.os_name', 'nt')
    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=True)
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()
    python_path = isolate.directory / '.venv' / 'Scripts' / 'python.exe'
    python_path.parent.mkdir(parents=True)
    python_path.write_text('')

    isolate.run('anything')

    assert observed_add_env['VIRTUAL_ENV'] == str(isolate.directory / '.venv')
    assert observed_add_env['PATH'].split(pathsep)[0] == str(python_path.parent)


def test_throng_logger_inherited_by_isolate(tmp_path, monkeypatch):
    """Verify that a logger passed to throngs is inherited by created isolates and receives operation logs."""
    monkeypatch.chdir(tmp_path)

    logger = MemoryLogger()
    isolate = throngs(logger=logger)['local'].get_isolate()
    isolate.run('printf inherited', catch_output=True)

    assert_any_message_contains(logger.data.info, 'isolate')
    assert_any_message_contains(logger.data.info, 'run')


def test_operation_logger_grouped_with_inherited_logger(tmp_path, monkeypatch):
    """Verify that an operation logger is grouped with the inherited logger and both receive logs."""
    monkeypatch.chdir(tmp_path)

    inherited_logger = MemoryLogger()
    operation_logger = MemoryLogger()
    isolate = throngs(logger=inherited_logger)['local'].get_isolate()

    isolate.run('printf grouped', catch_output=True, logger=operation_logger)

    assert_any_message_contains(inherited_logger.data.info, 'run')
    assert_any_message_contains(operation_logger.data.info, 'run')


def test_install_operation_logger_records_nested_run_once_in_each_logger(tmp_path, monkeypatch):
    """Verify that install and its nested run emit concise records once to both configured loggers."""

    def fake_run(*_args, **_kwargs):
        return SubprocessResult(id='install', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)
    inherited_logger = MemoryLogger()
    operation_logger = MemoryLogger()
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=False)
    isolate = TemporaryDirectoryThrong(logger=inherited_logger, config=config).get_isolate()
    info_before_install = len(inherited_logger.data.info)

    isolate.install('example', logger=operation_logger)

    expected_info = [
        'Installing 1 dependency.',
        f'Starting run in "{isolate.directory}".',
        'Run completed successfully.',
        'Install completed successfully.',
    ]
    expected_debug = [
        'Installing dependencies with the current interpreter.',
        'Run environment additions: [].',
    ]
    assert [str(call.message) for call in inherited_logger.data.info[info_before_install:]] == expected_info
    assert [str(call.message) for call in operation_logger.data.info] == expected_info
    assert [str(call.message) for call in inherited_logger.data.debug] == expected_debug
    assert [str(call.message) for call in operation_logger.data.debug] == expected_debug


def test_same_inherited_and_operation_logger_does_not_duplicate_records(tmp_path, monkeypatch):
    """Verify that passing the inherited logger again does not duplicate log records."""
    monkeypatch.chdir(tmp_path)

    logger = MemoryLogger()
    isolate = throngs(logger=logger)['local'].get_isolate()
    before = len(logger.data.info)

    isolate.run('printf dedupe', catch_output=True, logger=logger)

    messages = [str(call.message) for call in logger.data.info[before:]]
    assert messages
    assert len(messages) == len(set(messages))


def test_default_operation_logger_does_not_duplicate_empty_logger(tmp_path, monkeypatch):
    """Verify that the default EmptyLogger path emits the same inherited logs as an explicit EmptyLogger."""
    monkeypatch.chdir(tmp_path)

    default_logger = MemoryLogger()
    explicit_empty_logger = MemoryLogger()
    default_isolate = throngs(logger=default_logger)['local'].get_isolate()
    explicit_empty_isolate = throngs(logger=explicit_empty_logger)['local'].get_isolate()

    default_isolate.run('printf inherited-only', catch_output=True)
    explicit_empty_isolate.run('printf inherited-only', catch_output=True, logger=EmptyLogger())

    default_messages = [str(call.message) for call in default_logger.data.info]
    explicit_empty_messages = [str(call.message) for call in explicit_empty_logger.data.info]
    assert explicit_empty_messages == default_messages


def test_logging_run_success_levels(tmp_path, monkeypatch):
    """Verify that a successful run records one throng lifecycle without suby duplication."""
    monkeypatch.chdir(tmp_path)

    logger = MemoryLogger()
    isolate = throngs(logger=logger)['local'].get_isolate()

    isolate.run('printf ok', catch_output=True)

    assert [str(call.message) for call in logger.data.info] == [
        f'Creating local isolate in current working directory "{tmp_path}".',
        f'Starting run in "{tmp_path}".',
        'Run completed successfully.',
    ]
    assert_any_message_contains(logger.data.debug, 'environment')


@pytest.mark.parametrize('explicit_log_run', [None, False])
def test_log_run_disabled_omits_suby_logger_and_backend_messages(tmp_path, monkeypatch, explicit_log_run):
    """Verify that omitted or false log_run keeps suby internal command messages out of the operation logger."""
    logger = MemoryLogger()
    observed_keyword_arguments: Dict[str, object] = {}
    configuration_arguments = {'log_run': explicit_log_run} if explicit_log_run is not None else {}
    config = TemporaryDirectoryIsolationConfig(
        base_directory=str(tmp_path),
        use_venv=False,
        **configuration_arguments,
    )
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()

    def observing_run(*args, **kwargs):
        observed_keyword_arguments.update(kwargs)
        return directory_run_suby(*args, **kwargs)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', observing_run)

    isolate.run('printf hidden-backend', logger=logger, catch_output=True)

    messages = [str(call.message) for call in logger.data.info]
    assert config.log_run is False
    assert 'logger' not in observed_keyword_arguments
    assert messages == [
        f'Starting run in "{isolate.directory}".',
        'Run completed successfully.',
    ]


def test_log_run_enabled_forwards_operation_logger_and_backend_messages(tmp_path, monkeypatch):
    """Verify that log_run=True deliberately forwards the operation logger so suby records are visible."""
    logger = MemoryLogger()
    observed_backend_logger: Optional[object] = None
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=False, log_run=True)
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()

    def observing_run(*args, **kwargs):
        nonlocal observed_backend_logger
        observed_backend_logger = kwargs.get('logger')
        return directory_run_suby(*args, **kwargs)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', observing_run)

    isolate.run('printf visible-backend', logger=logger, catch_output=True)

    messages = [str(call.message) for call in logger.data.info]
    assert observed_backend_logger is logger
    assert messages == [
        f'Starting run in "{isolate.directory}".',
        'The beginning of the execution of the command "printf visible-backend".',
        'The command "printf visible-backend" has been successfully executed.',
        'Run completed successfully.',
    ]


def test_logging_run_failure_levels(tmp_path, monkeypatch):
    """Verify that a failing run writes a useful non-zero diagnostic without suby lifecycle records."""
    monkeypatch.chdir(tmp_path)

    logger = MemoryLogger()
    isolate = throngs(logger=logger)['local'].get_isolate()

    expected_message = f'Command failed or output decoding failed: Error when executing the command "{executable} -c "import sys; sys.exit(5)"".'
    with pytest.raises(CommandExecutionError, match=match(expected_message)):
        isolate.run(executable, '-c', 'import sys; sys.exit(5)', split=False)

    assert_any_message_contains(logger.data.error, 'run', 'non-zero', '5')
    assert [str(call.message) for call in logger.data.info] == [
        f'Creating local isolate in current working directory "{tmp_path}".',
        f'Starting run in "{tmp_path}".',
    ]


def test_logging_captured_nonzero_run_does_not_report_success(tmp_path, monkeypatch):
    """Verify that a captured non-zero subprocess result is reported as a failed command, not as success."""
    monkeypatch.chdir(tmp_path)

    logger = MemoryLogger()
    isolate = throngs(logger=logger)['local'].get_isolate()

    result = isolate.run(executable, '-c', 'import sys; sys.exit(7)', catch_exceptions=True, split=False)

    assert result.returncode == 7
    assert_any_message_contains(logger.data.error, 'run', 'non-zero', '7')
    assert all(str(call.message) != 'Run completed successfully.' for call in logger.data.info)


def test_logging_run_does_not_expose_command_arguments(tmp_path, monkeypatch):
    """Verify that lifecycle logging does not expose potentially sensitive command arguments."""
    monkeypatch.chdir(tmp_path)

    logger = MemoryLogger()
    secret_argument = 'token=private-run-secret'
    isolate = throngs(logger=logger)['local'].get_isolate()

    isolate.run(executable, '-c', 'pass', secret_argument, split=False)

    messages = [
        str(call.message)
        for level in (logger.data.debug, logger.data.info, logger.data.error, logger.data.exception)
        for call in level
    ]

    assert all(secret_argument not in message for message in messages)


def test_logging_install_success_and_failure(tmp_path, monkeypatch):
    """Verify that install logs concise success and diagnostic failure events."""
    monkeypatch.chdir(tmp_path)

    logger = MemoryLogger()
    calls: List[Tuple[object, ...]] = []

    def successful_run(*args, **_kwargs):
        calls.append(args)
        if '-m' in args and 'venv' in args:
            venv_path = Path(args[-1])
            (venv_path / 'bin').mkdir(parents=True)
            (venv_path / 'bin' / 'python').write_text('')
            (venv_path / 'bin' / 'python').chmod(S_IREAD | S_IWRITE | S_IXUSR)
        return SubprocessResult(id='install-ok', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', successful_run)
    isolate = throngs(logger=logger)['local'].get_isolate()

    isolate.install('example-package')

    assert calls
    assert_any_message_contains(logger.data.info, 'install')
    assert_any_message_contains(logger.data.debug, 'creating', 'virtual environment')

    def failing_run(*_args, **_kwargs):
        return SubprocessResult(id='install-fail', stderr='pip failed', returncode=1)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', failing_run)
    with pytest.raises(InstallError, match=match('pip failed')):
        isolate.install('broken-package')

    assert_any_message_contains(logger.data.exception, 'install', 'failed', 'pip failed')


def test_logging_install_does_not_expose_dependency_specification(tmp_path, monkeypatch):
    """Verify that install logging does not expose potentially sensitive dependency specifications."""
    secret_dependency = 'private-package @ https://user:secret-token@example.invalid/archive.whl'

    def fake_run(*_args, **_kwargs):
        return SubprocessResult(id='install', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)
    logger = MemoryLogger()
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=False)
    isolate = TemporaryDirectoryThrong(logger=logger, config=config).get_isolate()

    isolate.install(secret_dependency)

    messages = [
        str(call.message)
        for level in (logger.data.debug, logger.data.info, logger.data.error, logger.data.exception)
        for call in level
    ]

    assert all(secret_dependency not in message for message in messages)
    assert all('secret-token' not in message for message in messages)


def test_logging_failed_install_does_not_expose_dependency_specification(tmp_path, monkeypatch):
    """Verify that install failure logs preserve diagnostics without exposing a secret dependency URL."""
    secret_dependency = 'private-package @ https://user:secret-token@example.invalid/archive.whl'
    logger = MemoryLogger()
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=False)
    isolate = TemporaryDirectoryThrong(logger=logger, config=config).get_isolate()

    def fake_run(*_args, **_kwargs):
        return SubprocessResult(id='failure', stderr=f'pip could not install {secret_dependency}', returncode=1)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)

    with pytest.raises(InstallError, match=match(f'pip could not install {secret_dependency}')):
        isolate.install(secret_dependency)

    messages = [
        str(call.message)
        for level in (logger.data.debug, logger.data.info, logger.data.error, logger.data.exception)
        for call in level
    ]
    assert any('pip could not install' in message for message in messages)
    assert all('secret-token' not in message for message in messages)


def test_logging_failed_install_redacts_overlapping_dependency_specifications(tmp_path, monkeypatch):
    """Verify that a longer dependency specification is fully redacted even when another name is its prefix."""
    logger = MemoryLogger()
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=False)
    isolate = TemporaryDirectoryThrong(logger=logger, config=config).get_isolate()

    def fake_run(*_args, **_kwargs):
        return SubprocessResult(id='failure', stderr='pip could not install foobar or foo', returncode=1)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)

    with pytest.raises(InstallError, match=match('pip could not install foobar or foo')):
        isolate.install(('foo', 'foobar'))

    assert [str(call.message) for call in logger.data.exception] == [
        'Install failed: pip could not install <dependency specification> or <dependency specification>.',
    ]


def test_logging_dump_load_success_and_failure(tmp_path, monkeypatch):
    """Verify that dump and load success and failure paths write the expected logs."""
    monkeypatch.chdir(tmp_path)

    logger = MemoryLogger()
    isolate = cast(DirectoryIsolate, throngs(logger=logger)['temporary_directory'].get_isolate())
    (isolate.directory / 'file.txt').write_text('content')

    dumped = isolate.dump()
    isolate.load(dumped)

    assert_any_message_contains(logger.data.info, 'dump')
    assert_any_message_contains(logger.data.info, 'load')
    assert_any_message_contains(logger.data.debug, 'compression')

    with pytest.raises(ArchiveUnpackError, match=match('archive unpack failed: not a gzip file')):
        isolate.load(b'not an archive')

    assert [str(call.message) for call in logger.data.exception] == ['Archive unpack failed: not a gzip file.']


def test_operation_memory_logger_isolation(temporary_isolate):
    """Verify that independent operation loggers receive only their own isolate operation records."""
    first = MemoryLogger()
    second = MemoryLogger()
    first_isolate = temporary_isolate()
    second_isolate = temporary_isolate()

    first_isolate.dump(logger=first)
    second_isolate.dump(logger=second)

    first_messages = [str(call.message) for call in first.data.info]
    second_messages = [str(call.message) for call in second.data.info]

    assert any(str(first_isolate.directory) in message for message in first_messages)
    assert any(str(second_isolate.directory) in message for message in second_messages)
    assert all(str(second_isolate.directory) not in message for message in first_messages)
    assert all(str(first_isolate.directory) not in message for message in second_messages)


@pytest.mark.parametrize('compression', ['gzip', 'bz2', 'lzma', 'none'])
def test_dump_load_roundtrip_gzip_bz2_lzma_none(compression: str, temporary_isolate):
    """Verify that dump/load round-trips file bytes for every supported compression mode."""
    source = temporary_isolate(compression=compression)
    target = temporary_isolate(compression=compression)
    (source.directory / 'nested').mkdir()
    (source.directory / 'nested' / 'file.txt').write_text('content')
    (source.directory / 'root.bin').write_bytes(b'\x00\xff')

    dumped = source.dump()
    target.load(dumped)

    assert read_tree(target.directory) == {
        'nested/file.txt': b'content',
        'root.bin': b'\x00\xff',
    }


def test_none_compression_plain_tar(temporary_isolate):
    """Verify that none compression produces a plain uncompressed tar archive."""
    isolate = temporary_isolate(compression='none')
    (isolate.directory / 'file.txt').write_text('content')

    dumped = isolate.dump()

    with open_tar(fileobj=BytesIO(dumped), mode='r:') as archive:
        assert archive.getnames() == ['file.txt']
    assert not dumped.startswith(b'\x1f\x8b')
    assert not dumped.startswith(b'BZh')
    assert not dumped.startswith(b'\xfd7zXZ\x00')


def test_dump_binary_file(temporary_isolate):
    """Verify that binary files round-trip byte-for-byte through dump and load."""
    source = temporary_isolate()
    target = temporary_isolate()
    payload = bytes(range(256))
    (source.directory / 'payload.bin').write_bytes(payload)

    target.load(source.dump())

    assert (target.directory / 'payload.bin').read_bytes() == payload


@pytest.mark.skipif(os_name == 'nt', reason='symlink creation often requires elevated privileges on Windows')
def test_dump_omits_symbolic_links_from_serialized_contents(temporary_isolate):
    """Verify that dump serializes regular file contents but does not emit symbolic-link archive members."""
    source = temporary_isolate()
    target = temporary_isolate()
    regular_file = source.directory / 'regular.txt'
    regular_file.write_text('content')
    (source.directory / 'symbolic.txt').symlink_to(regular_file)

    dumped = source.dump()
    target.load(dumped)

    assert read_tree(target.directory) == {'regular.txt': b'content'}
    assert not (target.directory / 'symbolic.txt').exists()


@pytest.mark.skipif(not can_create_hardlinks_in_temporary_directory(), reason='hardlinks are not supported in this temporary filesystem')
def test_dump_serializes_hardlink_paths_as_regular_files(temporary_isolate):
    """Verify that each hardlink path is dumped as regular file data so that load can restore the archive."""
    source = temporary_isolate()
    target = temporary_isolate()
    first_file = source.directory / 'first.txt'
    second_file = source.directory / 'second.txt'
    first_file.write_text('content')

    link(str(first_file), str(second_file))

    dumped = source.dump()

    with open_tar(fileobj=BytesIO(dumped), mode='r:') as archive:
        archived_files = {member.name: member.isfile() for member in archive.getmembers()}

    target.load(dumped)

    assert archived_files == {'first.txt': True, 'second.txt': True}
    assert read_tree(target.directory) == {'first.txt': b'content', 'second.txt': b'content'}


@pytest.mark.skipif(os_name == 'nt', reason='POSIX named pipes are not available on Windows')
def test_dump_omits_named_pipes_from_serialized_contents(temporary_isolate):
    """Verify that dump omits a POSIX named pipe because snapshots contain regular file data only."""
    source = temporary_isolate()
    target = temporary_isolate()
    kept_file = source.directory / 'regular.txt'
    omitted_pipe = source.directory / 'ignored.pipe'
    kept_file.write_text('content')
    run_process(['mkfifo', str(omitted_pipe)], check=True)

    target.load(source.dump())

    assert read_tree(target.directory) == {'regular.txt': b'content'}
    assert not (target.directory / 'ignored.pipe').exists()


@pytest.mark.skipif(os_name == 'nt', reason='POSIX mode expectations do not apply on Windows')
def test_dump_preserves_ordinary_permission_mode_where_practical(temporary_isolate):
    """Verify that dump/load preserves complete ordinary POSIX permission bits while retaining executable status."""
    source = temporary_isolate()
    target = temporary_isolate()
    script = source.directory / 'script.sh'
    script.write_text('#!/bin/sh\nexit 0\n')
    expected_mode = 0o754
    script.chmod(expected_mode)

    target.load(source.dump())

    assert S_IMODE((target.directory / 'script.sh').stat().st_mode) == expected_mode


@pytest.mark.skipif(os_name == 'nt', reason='POSIX mode expectations do not apply on Windows')
def test_load_masks_special_permission_bits(temporary_isolate):
    """Verify that load strips special permission bits while preserving ordinary permissions."""
    isolate = temporary_isolate()
    special_bits = S_ISUID | S_ISGID | S_ISVTX
    mode = special_bits | S_IRWXU

    isolate.load(make_tar_bytes({'script.sh': b'#!/bin/sh\n'}, modes={'script.sh': mode}))

    loaded_mode = (isolate.directory / 'script.sh').stat().st_mode
    assert loaded_mode & special_bits == 0
    assert loaded_mode & S_IXUSR


def test_dump_excludes_configured_paths(temporary_isolate):
    """Verify that configured exclude patterns omit matching paths from dumps."""
    source = temporary_isolate(dump_exclude=['ignored/**'])
    (source.directory / 'kept.txt').write_text('kept')
    (source.directory / 'ignored').mkdir()
    (source.directory / 'ignored' / 'file.txt').write_text('ignored')

    dumped = source.dump()

    with open_tar(fileobj=BytesIO(dumped), mode='r:') as archive:
        assert archive.getnames() == ['kept.txt']


def test_dump_excludes_venv_by_default(temporary_isolate):
    """Verify that the effective virtual environment path is excluded from dumps by default."""
    isolate = temporary_isolate()
    (isolate.directory / '.venv').mkdir()
    (isolate.directory / '.venv' / 'installed.txt').write_text('dependency')

    dumped = isolate.dump()

    with open_tar(fileobj=BytesIO(dumped), mode='r:') as archive:
        assert '.venv/installed.txt' not in archive.getnames()


def test_dump_excludes_custom_venv_path_by_default(temporary_isolate):
    """Verify that the configured virtual environment path is excluded from dumps by default."""
    isolate = temporary_isolate(venv_path='custom/env')
    custom_venv = isolate.directory / 'custom' / 'env'
    custom_venv.mkdir(parents=True)
    (custom_venv / 'installed.txt').write_text('dependency')

    dumped = isolate.dump()

    with open_tar(fileobj=BytesIO(dumped), mode='r:') as archive:
        assert 'custom/env/installed.txt' not in archive.getnames()


@pytest.mark.parametrize('venv_path', ['[env]', 'venv*', '!'])
def test_dump_excludes_custom_venv_path_as_a_literal_directory(venv_path: str, temporary_isolate):
    """Verify that a custom venv path containing glob syntax excludes only that literal directory during dump."""
    isolate = temporary_isolate(venv_path=venv_path)
    virtual_environment = isolate.directory / venv_path
    virtual_environment.mkdir()
    (virtual_environment / 'installed.txt').write_text('dependency')
    unrelated_directory = isolate.directory / 'venv-neighbor'
    unrelated_directory.mkdir()
    (unrelated_directory / 'ordinary.txt').write_text('ordinary')

    dumped = isolate.dump()

    with open_tar(fileobj=BytesIO(dumped), mode='r:') as archive:
        archived_names = archive.getnames()

    assert f'{venv_path}/installed.txt' not in archived_names
    assert 'venv-neighbor/ordinary.txt' in archived_names


def test_dump_can_include_venv_when_exclude_disabled(temporary_isolate):
    """Verify that disabling venv exclusion allows the venv path to appear in dumps."""
    isolate = temporary_isolate(exclude_venv=False)
    (isolate.directory / '.venv').mkdir()
    (isolate.directory / '.venv' / 'installed.txt').write_text('dependency')

    dumped = isolate.dump()

    with open_tar(fileobj=BytesIO(dumped), mode='r:') as archive:
        assert '.venv/installed.txt' in archive.getnames()


def test_dump_includes_default_venv_path_when_virtual_environments_are_disabled(temporary_isolate):
    """Verify that `.venv` is ordinary serializable content when installation does not use virtual environments."""
    isolate = temporary_isolate(use_venv=False)
    (isolate.directory / '.venv').mkdir()
    (isolate.directory / '.venv' / 'ordinary.txt').write_text('ordinary')

    dumped = isolate.dump()

    with open_tar(fileobj=BytesIO(dumped), mode='r:') as archive:
        assert '.venv/ordinary.txt' in archive.getnames()


def test_load_preserves_excluded_venv(temporary_isolate):
    """Verify that load preserves an existing excluded venv directory."""
    isolate = temporary_isolate()
    (isolate.directory / '.venv').mkdir()
    (isolate.directory / '.venv' / 'installed.txt').write_text('dependency')

    isolate.load(make_tar_bytes({'fresh.txt': b'fresh'}))

    assert (isolate.directory / '.venv' / 'installed.txt').read_text() == 'dependency'
    assert (isolate.directory / 'fresh.txt').read_text() == 'fresh'


@pytest.mark.parametrize('venv_path', ['[env]', 'venv*', '!'])
def test_load_preserves_custom_venv_path_as_a_literal_directory(venv_path: str, temporary_isolate):
    """Verify that load preserves only the literal custom venv directory even when its name resembles a glob."""
    isolate = temporary_isolate(venv_path=venv_path)
    virtual_environment = isolate.directory / venv_path
    virtual_environment.mkdir()
    (virtual_environment / 'installed.txt').write_text('dependency')
    unrelated_directory = isolate.directory / 'venv-neighbor'
    unrelated_directory.mkdir()
    (unrelated_directory / 'ordinary.txt').write_text('ordinary')

    isolate.load(make_tar_bytes({'fresh.txt': b'fresh'}))

    assert (virtual_environment / 'installed.txt').read_text() == 'dependency'
    assert not unrelated_directory.exists()
    assert (isolate.directory / 'fresh.txt').read_text() == 'fresh'


def test_load_replaces_default_venv_path_when_virtual_environments_are_disabled(temporary_isolate):
    """Verify that load replaces `.venv` as ordinary content when virtual environments are disabled."""
    isolate = temporary_isolate(use_venv=False)
    (isolate.directory / '.venv').mkdir()
    (isolate.directory / '.venv' / 'old.txt').write_text('old')

    isolate.load(make_tar_bytes({'fresh.txt': b'fresh'}))

    assert not (isolate.directory / '.venv').exists()
    assert (isolate.directory / 'fresh.txt').read_text() == 'fresh'


def test_load_preserves_empty_excluded_venv(temporary_isolate):
    """Verify that load preserves an empty excluded venv directory."""
    isolate = temporary_isolate()
    (isolate.directory / '.venv').mkdir()

    isolate.load(make_tar_bytes({'fresh.txt': b'fresh'}))

    assert (isolate.directory / '.venv').is_dir()
    assert (isolate.directory / 'fresh.txt').read_text() == 'fresh'


@pytest.mark.skipif(os_name == 'nt', reason='POSIX named pipes are not available on Windows')
def test_load_preserves_excluded_named_pipe(temporary_isolate):
    """Verify that load leaves a pre-existing excluded POSIX named pipe unchanged."""
    isolate = temporary_isolate(dump_exclude=['kept.pipe'])
    kept_pipe = isolate.directory / 'kept.pipe'
    run_process(['mkfifo', str(kept_pipe)], check=True)

    isolate.load(make_tar_bytes({'fresh.txt': b'fresh'}))

    assert kept_pipe.is_fifo()
    assert (isolate.directory / 'fresh.txt').read_text() == 'fresh'


@pytest.mark.skipif(os_name == 'nt', reason='symlink creation often requires elevated privileges on Windows')
def test_load_preserves_excluded_symbolic_link(tmp_path, temporary_isolate):
    """Verify that load retains an excluded symbolic link and its existing external target."""
    external_target = tmp_path / 'external.txt'
    external_target.write_text('outside')
    isolate = temporary_isolate(dump_exclude=['kept-link'])
    kept_link = isolate.directory / 'kept-link'
    kept_link.symlink_to(external_target)

    isolate.load(make_tar_bytes({'fresh.txt': b'fresh'}))

    assert kept_link.is_symlink()
    assert kept_link.read_text() == 'outside'
    assert (isolate.directory / 'fresh.txt').read_text() == 'fresh'


def test_load_preserves_excluded_file_over_conflicting_archive_directory(temporary_isolate):
    """Verify that an excluded existing file wins over an archive directory at the same path."""
    isolate = temporary_isolate(dump_exclude=['kept'])
    kept_file = isolate.directory / 'kept'
    kept_file.write_text('old')

    isolate.load(make_tar_bytes({'kept/new.txt': b'new', 'fresh.txt': b'fresh'}))

    assert kept_file.read_text() == 'old'
    assert (isolate.directory / 'fresh.txt').read_text() == 'fresh'


def test_load_preserves_excluded_directory_over_conflicting_archive_file(temporary_isolate):
    """Verify that an excluded existing directory wins over an archive file at the same path."""
    isolate = temporary_isolate(dump_exclude=['kept', 'kept/**'])
    kept_directory = isolate.directory / 'kept'
    kept_directory.mkdir()
    (kept_directory / 'old.txt').write_text('old')

    isolate.load(make_tar_bytes({'kept': b'new', 'fresh.txt': b'fresh'}))

    assert (kept_directory / 'old.txt').read_text() == 'old'
    assert (isolate.directory / 'fresh.txt').read_text() == 'fresh'


def test_dump_empty_sandbox(temporary_isolate):
    """Verify that dumping an empty isolate produces a valid archive that can be loaded."""
    source = temporary_isolate()
    target = temporary_isolate()

    dumped = source.dump()
    target.load(dumped)

    assert read_tree(target.directory) == {}


def test_load_empty_archive_replaces_non_excluded_contents(temporary_isolate):
    """Verify that loading an empty archive removes old non-excluded files."""
    source = temporary_isolate()
    target = temporary_isolate()
    (target.directory / 'old.txt').write_text('old')

    target.load(source.dump())

    assert read_tree(target.directory) == {}


def test_load_corrupted_bytes(temporary_isolate):
    """Verify that corrupted archive bytes raise ArchiveUnpackError with the unpack reason."""
    isolate = temporary_isolate()

    with pytest.raises(ArchiveUnpackError, match=match('archive unpack failed: truncated header')):
        isolate.load(b'garbage')


def test_load_empty_bytes(temporary_isolate):
    """Verify that empty archive bytes raise ArchiveUnpackError with the empty-file reason."""
    isolate = temporary_isolate()

    with pytest.raises(ArchiveUnpackError, match=match('archive unpack failed: empty file')):
        isolate.load(b'')


def test_load_compression_mismatch(temporary_isolate):
    """Verify that loading bytes with the wrong configured compression raises ArchiveUnpackError."""
    isolate = temporary_isolate(compression='bz2')

    with pytest.raises(ArchiveUnpackError, match=match('archive unpack failed: not a bzip2 file')):
        isolate.load(make_tar_bytes({'file.txt': b'content'}, compression='gzip'))


def test_load_ignores_archived_entry_matching_excluded_path(temporary_isolate):
    """Verify that incoming data for an excluded path cannot overwrite its existing contents."""
    isolate = temporary_isolate(dump_exclude=['kept.txt'])
    kept_file = isolate.directory / 'kept.txt'
    kept_file.write_text('old')

    isolate.load(make_tar_bytes({'kept.txt': b'new', 'fresh.txt': b'fresh'}))

    assert kept_file.read_text() == 'old'
    assert (isolate.directory / 'fresh.txt').read_text() == 'fresh'


def test_load_creates_explicit_archive_directories(temporary_isolate):
    """Verify that load accepts a safe explicit directory member and places nested file data inside it."""
    isolate = temporary_isolate()
    archive_buffer = BytesIO()

    with open_tar(fileobj=archive_buffer, mode='w') as archive:
        directory_info = TarInfo('nested')
        directory_info.type = DIRTYPE
        archive.addfile(directory_info)

        file_info = TarInfo('nested/file.txt')
        payload = b'content'
        file_info.size = len(payload)
        archive.addfile(file_info, BytesIO(payload))

    isolate.load(archive_buffer.getvalue())

    assert (isolate.directory / 'nested').is_dir()
    assert (isolate.directory / 'nested' / 'file.txt').read_bytes() == b'content'


def test_load_rejects_absolute_path(temporary_isolate):
    """Verify that load rejects archive entries with absolute paths."""
    isolate = temporary_isolate()

    with pytest.raises(ArchiveUnpackError, match=match('Archive contains an absolute path: /x')):
        isolate.load(make_tar_bytes({'/x': b'x'}))


def test_load_rejects_traversal(temporary_isolate):
    """Verify that load rejects archive entries that traverse outside the isolate directory."""
    isolate = temporary_isolate()

    with pytest.raises(ArchiveUnpackError, match=match('Archive contains path traversal outside the isolate: a/../../x')):
        isolate.load(make_tar_bytes({'a/../../x': b'x'}))


@pytest.mark.parametrize('path', ['C:/outside.txt', 'C:\\outside.txt', '..\\outside.txt'])
def test_load_rejects_windows_style_unsafe_paths(path: str, temporary_isolate):
    """Verify that load rejects Windows-style absolute and traversal paths."""
    isolate = temporary_isolate()

    with pytest.raises(ArchiveUnpackError, match=match(expected_unsafe_path_message(path))):
        isolate.load(make_tar_bytes({path: b'x'}))


def expected_unsafe_path_message(path: str) -> str:
    if path == 'C:/outside.txt':
        return 'Archive contains an absolute Windows drive path: C:/outside.txt'
    if path == 'C:\\outside.txt':
        return 'Archive contains an unsafe backslash path: C:\\outside.txt'
    return 'Archive contains an unsafe backslash path: ..\\outside.txt'


@pytest.mark.parametrize('path', ['bad\x00name.txt', 'bad\x01name.txt', 'bad\x1fname.txt', 'bad\x7fname.txt'])
def test_load_rejects_null_and_control_character_paths(path: str, temporary_isolate):
    """Verify that load rejects archive entry names containing null, control, or DEL characters."""
    isolate = temporary_isolate()

    with pytest.raises(ArchiveUnpackError, match=match(f'Archive contains an unsafe control character in path: {path!r}')):
        isolate.load(make_pax_tar_bytes(path, b'x'))


def make_pax_tar_bytes(path: str, content: bytes) -> bytes:
    archive_buffer = BytesIO()

    with open_tar(fileobj=archive_buffer, mode='w', format=PAX_FORMAT) as archive:
        member_info = TarInfo('placeholder')
        member_info.pax_headers = {'path': path}
        member_info.size = len(content)
        archive.addfile(member_info, BytesIO(content))

    return archive_buffer.getvalue()


def make_unsafe_tar_bytes(member: TarInfo) -> bytes:
    archive_buffer = BytesIO()

    with open_tar(fileobj=archive_buffer, mode='w') as archive:
        if member.isfile():
            content = b'content'
            member.size = len(content)
            archive.addfile(member, BytesIO(content))
        else:
            archive.addfile(member)

    return archive_buffer.getvalue()


@pytest.mark.parametrize('path', ['', '.'])
def test_load_skips_empty_and_current_directory_entries(path: str, temporary_isolate):
    """Verify that empty and current-directory tar entries are ignored safely."""
    isolate = temporary_isolate()
    (isolate.directory / 'old.txt').write_text('old')

    isolate.load(make_tar_bytes({path: b''}))

    assert read_tree(isolate.directory) == {}


def test_load_rejects_duplicate_entries(temporary_isolate):
    """Verify that load rejects archives containing duplicate normalized entry paths."""
    isolate = temporary_isolate()
    archive_buffer = BytesIO()

    with open_tar(fileobj=archive_buffer, mode='w') as archive:
        for _ in range(2):
            member_info = TarInfo('same.txt')
            payload = b'x'
            member_info.size = len(payload)
            archive.addfile(member_info, BytesIO(payload))

    with pytest.raises(ArchiveUnpackError, match=match('Archive contains a duplicate entry: same.txt')):
        isolate.load(archive_buffer.getvalue())


@pytest.mark.parametrize(
    'member',
    [
        TarInfo('link'),
        TarInfo('hardlink'),
        TarInfo('device'),
    ],
)
def test_load_rejects_symlink_hardlink_device(member: TarInfo, temporary_isolate):
    """Verify that load rejects symlinks, hardlinks, and device-like tar entries as unsupported archive entries."""
    if member.name == 'link':
        member.type = SYMTYPE
        member.linkname = 'target'
    elif member.name == 'hardlink':
        member.type = LNKTYPE
        member.linkname = 'target'
    else:
        member.type = CHRTYPE

    isolate = temporary_isolate()

    with pytest.raises(ArchiveUnpackError, match=match(f'Archive contains unsafe entry: {member.name}')):
        isolate.load(make_unsafe_tar_bytes(member))


@pytest.mark.parametrize(
    ('member_name', 'member_type'),
    [
        ('link', SYMTYPE),
        ('hardlink', LNKTYPE),
        ('device', CHRTYPE),
    ],
)
def test_load_rejects_excluded_symlink_hardlink_device(member_name: str, member_type: bytes, temporary_isolate):
    """Verify that exclusion rules cannot conceal unsupported archive entry types during load validation."""
    member = TarInfo(member_name)
    member.type = member_type
    if member_type in (SYMTYPE, LNKTYPE):
        member.linkname = 'target'

    isolate = temporary_isolate(dump_exclude=[member_name])

    with pytest.raises(ArchiveUnpackError, match=match(f'Archive contains unsafe entry: {member_name}')):
        isolate.load(make_unsafe_tar_bytes(member))


@pytest.mark.parametrize('member_type', [SYMTYPE, LNKTYPE, CHRTYPE])
def test_load_rejects_unsafe_current_directory_entry(member_type: bytes, temporary_isolate):
    """Verify that an ignored current-directory archive name cannot conceal an unsafe member type."""
    member = TarInfo('.')
    member.type = member_type
    if member_type in (SYMTYPE, LNKTYPE):
        member.linkname = 'target'

    isolate = temporary_isolate()

    with pytest.raises(ArchiveUnpackError, match=match('Archive contains unsafe entry: .')):
        isolate.load(make_unsafe_tar_bytes(member))


def test_load_rejects_file_directory_conflict(temporary_isolate):
    """Verify that load rejects archives where a file blocks a child directory entry."""
    isolate = temporary_isolate()
    archive_buffer = BytesIO()

    with open_tar(fileobj=archive_buffer, mode='w') as archive:
        file_info = TarInfo('conflict')
        payload = b'x'
        file_info.size = len(payload)
        archive.addfile(file_info, BytesIO(payload))

        nested_info = TarInfo('conflict/nested.txt')
        nested_info.size = len(payload)
        archive.addfile(nested_info, BytesIO(payload))

    with pytest.raises(ArchiveUnpackError, match=match('Archive contains a file/directory conflict: conflict/nested.txt')):
        isolate.load(archive_buffer.getvalue())


def test_load_rejects_directory_file_conflict(temporary_isolate):
    """Verify that load rejects archives where a directory blocks a file at the same path."""
    isolate = temporary_isolate()
    archive_buffer = BytesIO()

    with open_tar(fileobj=archive_buffer, mode='w') as archive:
        nested_info = TarInfo('conflict/nested.txt')
        payload = b'x'
        nested_info.size = len(payload)
        archive.addfile(nested_info, BytesIO(payload))

        file_info = TarInfo('conflict')
        file_info.size = len(payload)
        archive.addfile(file_info, BytesIO(payload))

    with pytest.raises(ArchiveUnpackError, match=match('Archive contains a file/directory conflict: conflict')):
        isolate.load(archive_buffer.getvalue())


def test_load_replaces_non_excluded_contents(temporary_isolate):
    """Verify that load replaces old non-excluded contents with the archive contents."""
    isolate = temporary_isolate()
    (isolate.directory / 'old.txt').write_text('old')

    isolate.load(make_tar_bytes({'new.txt': b'new'}))

    assert not (isolate.directory / 'old.txt').exists()
    assert (isolate.directory / 'new.txt').read_text() == 'new'


def test_load_keeps_excluded_contents(temporary_isolate):
    """Verify that load keeps excluded files that are absent from the archive."""
    isolate = temporary_isolate(dump_exclude=['keep/**'])
    (isolate.directory / 'keep').mkdir()
    (isolate.directory / 'keep' / 'old.txt').write_text('old')

    isolate.load(make_tar_bytes({'new.txt': b'new'}))

    assert (isolate.directory / 'keep' / 'old.txt').read_text() == 'old'
    assert (isolate.directory / 'new.txt').read_text() == 'new'


def test_root_anchored_exclude_pattern_has_the_same_dump_and_load_semantics(temporary_isolate):
    """Verify that a root-anchored user pattern omits a dumped path and preserves the matching loaded path."""
    source = temporary_isolate(dump_exclude=['/keep.txt'])
    target = temporary_isolate(dump_exclude=['/keep.txt'])
    (source.directory / 'keep.txt').write_text('source exclusion')
    (source.directory / 'fresh.txt').write_text('fresh')
    (target.directory / 'keep.txt').write_text('preserved')
    (target.directory / 'stale.txt').write_text('remove')

    dumped = source.dump()

    with open_tar(fileobj=BytesIO(dumped), mode='r:') as archive:
        assert archive.getnames() == ['fresh.txt']

    target.load(dumped)

    assert (target.directory / 'keep.txt').read_text() == 'preserved'
    assert (target.directory / 'fresh.txt').read_text() == 'fresh'
    assert not (target.directory / 'stale.txt').exists()


def test_negated_exclude_pattern_has_the_same_dump_and_load_semantics(temporary_isolate):
    """Verify that a negated user pattern re-includes the same nested path in both snapshot directions."""
    patterns = ['keep/**', '!keep/included.txt']
    source = temporary_isolate(dump_exclude=patterns)
    target = temporary_isolate(dump_exclude=patterns)
    (source.directory / 'keep').mkdir()
    (source.directory / 'keep' / 'excluded.txt').write_text('excluded from dump')
    (source.directory / 'keep' / 'included.txt').write_text('new included value')
    (target.directory / 'keep').mkdir()
    (target.directory / 'keep' / 'excluded.txt').write_text('preserved')
    (target.directory / 'keep' / 'included.txt').write_text('stale')

    dumped = source.dump()

    with open_tar(fileobj=BytesIO(dumped), mode='r:') as archive:
        assert archive.getnames() == ['keep/included.txt']

    target.load(dumped)

    assert (target.directory / 'keep' / 'excluded.txt').read_text() == 'preserved'
    assert (target.directory / 'keep' / 'included.txt').read_text() == 'new included value'


def test_load_preserves_nested_excluded_path_without_preserving_siblings(temporary_isolate):
    """Verify that preserving a nested excluded path does not preserve its non-excluded siblings."""
    isolate = temporary_isolate(dump_exclude=['keep/nested/**'])
    (isolate.directory / 'keep' / 'nested').mkdir(parents=True)
    (isolate.directory / 'keep' / 'nested' / 'old.txt').write_text('old')
    (isolate.directory / 'keep' / 'sibling.txt').write_text('remove me')

    isolate.load(make_tar_bytes({'new.txt': b'new'}))

    assert (isolate.directory / 'keep' / 'nested' / 'old.txt').read_text() == 'old'
    assert not (isolate.directory / 'keep' / 'sibling.txt').exists()
    assert (isolate.directory / 'new.txt').read_text() == 'new'


def test_load_replaces_concurrent_excluded_destination_with_preserved_value(monkeypatch, temporary_isolate):
    """The plan requires excluded paths to survive load; this verifies restoration over a new destination."""
    isolate = temporary_isolate(dump_exclude=['keep.txt'])
    preserved_path = isolate.directory / 'keep.txt'
    preserved_path.write_text('preserved')

    def move_and_create_destination(source, destination):
        source_path = Path(source)
        destination_path = Path(destination)
        result = directory_move(source, destination)

        if source_path.parent.name.startswith('throng-load-') and destination_path == isolate.directory / 'fresh.txt':
            preserved_path.write_text('external replacement')

        return result

    monkeypatch.setattr('throng.plugins.directory_isolate.move', move_and_create_destination)

    isolate.load(make_tar_bytes({'fresh.txt': b'fresh'}))

    assert preserved_path.read_text() == 'preserved'
    assert (isolate.directory / 'fresh.txt').read_text() == 'fresh'


def test_load_commit_failure_rolls_back_preexisting_contents(temporary_isolate):
    """Verify that a natural commit conflict restores the original tree and reports no unrestored paths."""
    isolate = temporary_isolate(dump_exclude=['keep/old.txt'])
    logger = MemoryLogger()
    (isolate.directory / 'keep').mkdir()
    (isolate.directory / 'keep' / 'old.txt').write_text('old')

    with pytest.raises(
        ArchiveUnpackError,
        match=match('Archive commit failed; rollback attempted. Cause: Archive commit failed; excluded path parent is a file: keep.'),
    ) as raised:
        isolate.load(make_tar_bytes({'keep': b'new', 'fresh.txt': b'fresh'}), logger=logger)

    assert 'Unrestored paths:' not in str(raised.value)
    assert read_tree(isolate.directory) == {'keep/old.txt': b'old'}
    assert_any_message_contains(logger.data.exception, 'archive', 'commit failed')


def test_load_commit_failure_removes_new_directory_during_rollback(temporary_isolate):
    """Verify that rollback removes a newly staged directory before restoring pre-existing contents."""
    isolate = temporary_isolate(dump_exclude=['keep/old.txt'])
    (isolate.directory / 'keep').mkdir()
    (isolate.directory / 'keep' / 'old.txt').write_text('old')

    with pytest.raises(
        ArchiveUnpackError,
        match=match('Archive commit failed; rollback attempted. Cause: Archive commit failed; excluded path parent is a file: keep.'),
    ):
        isolate.load(make_tar_bytes({'created/new.txt': b'new', 'keep': b'conflict'}))

    assert read_tree(isolate.directory) == {'keep/old.txt': b'old'}
    assert not (isolate.directory / 'created').exists()


def test_load_commit_failure_rolls_back_excluded_path_restored_before_later_conflict(temporary_isolate):
    """Verify that rollback retains an excluded file restored before a later excluded-path conflict aborts commit."""
    isolate = temporary_isolate(dump_exclude=['a.txt', 'z/kept.txt'])
    (isolate.directory / 'a.txt').write_text('preserved first')
    (isolate.directory / 'z').mkdir()
    (isolate.directory / 'z' / 'kept.txt').write_text('preserved later')

    with pytest.raises(
        ArchiveUnpackError,
        match=match('Archive commit failed; rollback attempted. Cause: Archive commit failed; excluded path parent is a file: z.'),
    ):
        isolate.load(make_tar_bytes({'z': b'conflict', 'fresh.txt': b'fresh'}))

    assert read_tree(isolate.directory) == {
        'a.txt': b'preserved first',
        'z/kept.txt': b'preserved later',
    }


def test_load_rollback_reports_excluded_path_that_cannot_be_moved_back(monkeypatch, temporary_isolate):
    """The plan requires best-effort rollback diagnostics; this reports an excluded path lost while undoing restoration."""
    isolate = temporary_isolate(dump_exclude=['a.txt', 'z/kept.txt'])
    (isolate.directory / 'a.txt').write_text('preserved first')
    (isolate.directory / 'z').mkdir()
    (isolate.directory / 'z' / 'kept.txt').write_text('preserved later')
    moves_to_backup = 0

    def fail_second_move_of_restored_excluded_path(source, destination):
        nonlocal moves_to_backup
        source_path = Path(source)
        destination_path = Path(destination)

        if source_path == isolate.directory / 'a.txt' and destination_path.parent.name.startswith('throng-backup-'):
            moves_to_backup += 1
            if moves_to_backup == 2:
                raise PermissionError('cannot move restored excluded path back to backup')

        return directory_move(source, destination)

    monkeypatch.setattr('throng.plugins.directory_isolate.move', fail_second_move_of_restored_excluded_path)

    with pytest.raises(
        ArchiveUnpackError,
        match=match('Archive commit failed; rollback attempted. Cause: Archive commit failed; excluded path parent is a file: z. Unrestored paths: a.txt.'),
    ):
        isolate.load(make_tar_bytes({'z': b'conflict', 'fresh.txt': b'fresh'}))

    assert not (isolate.directory / 'a.txt').exists()
    assert (isolate.directory / 'z' / 'kept.txt').read_text() == 'preserved later'


def test_load_rollback_reports_new_content_that_cannot_be_removed(monkeypatch, temporary_isolate):
    """The plan requires best-effort rollback diagnostics; this reports staged content that cannot be removed."""
    isolate = temporary_isolate(dump_exclude=['keep/old.txt'])
    (isolate.directory / 'keep').mkdir()
    (isolate.directory / 'keep' / 'old.txt').write_text('old')
    original_remove_path = DirectoryIsolate._remove_path

    def fail_removing_created_directory(self, path):
        if path == isolate.directory / 'created':
            raise PermissionError('cannot remove newly staged directory')
        original_remove_path(self, path)

    monkeypatch.setattr(DirectoryIsolate, '_remove_path', fail_removing_created_directory)

    with pytest.raises(
        ArchiveUnpackError,
        match=match('Archive commit failed; rollback attempted. Cause: Archive commit failed; excluded path parent is a file: keep. Unrestored paths: created.'),
    ):
        isolate.load(make_tar_bytes({'created/new.txt': b'new', 'keep': b'conflict'}))

    assert (isolate.directory / 'created' / 'new.txt').read_text() == 'new'
    assert (isolate.directory / 'keep' / 'old.txt').read_text() == 'old'


def test_load_rollback_does_not_report_backed_up_path_restored_after_transient_removal_failure(monkeypatch, temporary_isolate):
    """The plan reports only unrestored rollback paths; this checks recovery after a transient replacement failure."""
    isolate = temporary_isolate(dump_exclude=['keep/old.txt'])
    (isolate.directory / 'keep').mkdir()
    (isolate.directory / 'keep' / 'old.txt').write_text('old')
    original_remove_path = DirectoryIsolate._remove_path
    failed_once = False

    def fail_first_removal_of_backed_up_destination(self, path):
        nonlocal failed_once

        if path == isolate.directory / 'keep' and not failed_once:
            failed_once = True
            raise PermissionError('temporary refusal while removing replacement')

        original_remove_path(self, path)

    monkeypatch.setattr(DirectoryIsolate, '_remove_path', fail_first_removal_of_backed_up_destination)

    with pytest.raises(
        ArchiveUnpackError,
        match=match('Archive commit failed; rollback attempted. Cause: Archive commit failed; excluded path parent is a file: keep.'),
    ) as raised:
        isolate.load(make_tar_bytes({'keep': b'conflict'}))

    assert failed_once is True
    assert 'Unrestored paths:' not in str(raised.value)
    assert read_tree(isolate.directory) == {'keep/old.txt': b'old'}


def test_load_rollback_removes_external_destination_before_restoring_backup(monkeypatch, temporary_isolate):
    """The plan requires rollback to restore original paths; this checks replacement of an intervening path."""
    isolate = temporary_isolate(dump_exclude=['keep/old.txt'])
    (isolate.directory / 'old.txt').write_text('original')
    (isolate.directory / 'keep').mkdir()
    (isolate.directory / 'keep' / 'old.txt').write_text('preserved')
    original_remove_path = DirectoryIsolate._remove_path

    def remove_staged_path_then_create_external_destination(self, path):
        original_remove_path(self, path)

        if path == isolate.directory / 'fresh.txt':
            (isolate.directory / 'old.txt').write_text('external replacement')

    monkeypatch.setattr(DirectoryIsolate, '_remove_path', remove_staged_path_then_create_external_destination)

    with pytest.raises(
        ArchiveUnpackError,
        match=match('Archive commit failed; rollback attempted. Cause: Archive commit failed; excluded path parent is a file: keep.'),
    ):
        isolate.load(make_tar_bytes({'fresh.txt': b'fresh', 'keep': b'conflict'}))

    assert (isolate.directory / 'old.txt').read_text() == 'original'
    assert (isolate.directory / 'keep' / 'old.txt').read_text() == 'preserved'


def test_load_rollback_reports_original_path_that_cannot_be_restored(monkeypatch, temporary_isolate):
    """The plan requires best-effort rollback diagnostics; this reports an original path that cannot be restored."""
    isolate = temporary_isolate(dump_exclude=['keep/old.txt'])
    (isolate.directory / 'old.txt').write_text('original')
    (isolate.directory / 'keep').mkdir()
    (isolate.directory / 'keep' / 'old.txt').write_text('preserved')

    def fail_restoring_original_file(source, destination):
        source_path = Path(source)
        destination_path = Path(destination)

        if source_path.parent.name.startswith('throng-backup-') and source_path.name == 'old.txt' and destination_path == isolate.directory / 'old.txt':
            raise PermissionError('cannot restore original path')

        return directory_move(source, destination)

    monkeypatch.setattr('throng.plugins.directory_isolate.move', fail_restoring_original_file)

    with pytest.raises(
        ArchiveUnpackError,
        match=match('Archive commit failed; rollback attempted. Cause: Archive commit failed; excluded path parent is a file: keep. Unrestored paths: old.txt.'),
    ):
        isolate.load(make_tar_bytes({'keep': b'conflict', 'fresh.txt': b'fresh'}))

    assert not (isolate.directory / 'old.txt').exists()
    assert (isolate.directory / 'keep' / 'old.txt').read_text() == 'preserved'


def test_config_rejects_unsupported_compression_mode(tmp_path):
    """Verify that unsupported compression modes are rejected by skelet config validation."""
    with pytest.raises(ValueError, match=match('Unsupported compression mode.')):
        TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), compression='zstd')


def test_config_rejects_malformed_dump_exclude_pattern(tmp_path):
    """Verify that malformed archive exclude patterns are rejected before any dump or load operation can start."""
    with pytest.raises(ValueError, match=match('Invalid dump exclude pattern.')):
        TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), dump_exclude=['!'])


@pytest.mark.parametrize('operation_name', ['dump', 'load'])
def test_operation_rejects_malformed_dump_exclude_added_after_configuration(operation_name: str, temporary_isolate):
    """Verify that mutating configured excludes cannot expose a raw pathspec error during dump or load."""
    isolate = temporary_isolate()
    isolate.config.dump_exclude.append('!')
    logger = MemoryLogger()
    operation = {
        'dump': lambda: isolate.dump(logger=logger),
        'load': lambda: isolate.load(make_tar_bytes({'file.txt': b'content'}), logger=logger),
    }[operation_name]

    with pytest.raises(ValueError, match=match('Invalid dump exclude pattern.')):
        operation()

    assert_any_message_contains(logger.data.exception, operation_name, 'invalid', 'exclude')


def test_load_tempdir_creation_failure_is_wrapped_and_logged(tmp_path, monkeypatch, temporary_isolate):
    """Verify that a real tempfile creation failure is wrapped as ArchiveUnpackError and logged."""
    tempdir_file = tmp_path / 'not-a-directory'
    tempdir_file.write_text('content')
    monkeypatch.setattr(tempfile, 'tempdir', str(tempdir_file))
    isolate = temporary_isolate()
    logger = MemoryLogger()

    with pytest.raises(ArchiveUnpackError, match=match('archive unpack failed: cannot create temporary load directories: Not a directory')):
        isolate.load(make_tar_bytes({'new.txt': b'new'}), logger=logger)

    assert [str(call.message) for call in logger.data.exception] == ['Archive unpack failed: cannot create temporary load directories: Not a directory.']


@pytest.mark.skipif(os_name == 'nt', reason='permission mode semantics differ on Windows')
def test_load_permission_denied_write(request, temporary_isolate):
    """Verify that a write failure reports only genuinely unrestored paths and retains untouched old data."""
    isolate = temporary_isolate()
    (isolate.directory / 'old.txt').write_text('old')
    request.addfinalizer(lambda: isolate.directory.chmod(S_IREAD | S_IWRITE | S_IXUSR))
    isolate.directory.chmod(S_IREAD | S_IXUSR)
    logger = MemoryLogger()

    with pytest.raises(ArchiveUnpackError, match=match('Archive commit failed; rollback attempted. Cause: Permission denied.')) as raised:
        isolate.load(make_tar_bytes({'new.txt': b'new'}), logger=logger)

    assert 'Unrestored paths:' not in str(raised.value)
    assert (isolate.directory / 'old.txt').read_text() == 'old'
    assert not (isolate.directory / 'new.txt').exists()
    assert_any_message_contains(logger.data.exception, 'archive', 'failed')


@pytest.mark.skipif(os_name == 'nt', reason='permission mode semantics differ on Windows')
def test_dump_permission_denied_read(request, temporary_isolate):
    """Verify that a read failure during dump propagates the read error and logs the failure."""
    isolate = temporary_isolate()
    unreadable = isolate.directory / 'unreadable.txt'
    unreadable.write_text('secret')
    request.addfinalizer(lambda: unreadable.chmod(S_IREAD | S_IWRITE) if unreadable.exists() else None)
    unreadable.chmod(0)

    logger = MemoryLogger()

    with pytest.raises(PermissionError, match=match(f"[Errno 13] Permission denied: '{unreadable}'")):
        isolate.dump(logger=logger)

    assert_any_message_contains(logger.data.exception, 'dump', 'failed')
