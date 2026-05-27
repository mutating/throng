from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from errno import EACCES
from gc import collect
from json import loads
from os import environ
from os import name as os_name
from pathlib import Path
from stat import S_IEXEC, S_IREAD, S_IWRITE
from subprocess import list2cmdline
from subprocess import run as run_process
from sys import executable, version_info
from tempfile import TemporaryDirectory, gettempdir
from textwrap import dedent
from threading import Barrier, Condition, Event, Lock
from typing import cast

import pytest
from emptylog import MemoryLogger
from full_match import match
from locklib import LockTraceWrapper
from suby.subprocess_result import SubprocessResult

from tests.helpers import hold_windows_path_open, make_tar_bytes
from throng import (
    InvalidBaseDirectoryError,
    IsolateDeletedError,
    throngs,
)
from throng.plugins.directory_isolate import DirectoryIsolate, DirectoryIsolationConfig
from throng.plugins.empty_lock import EmptyLock
from throng.plugins.temporary_directory_throng import (
    TemporaryDirectoryIsolationConfig,
    TemporaryDirectoryThrong,
)

WINDOWS_DIRECTORY_HANDLE_FLAGS = 0x02000000
WINDOWS_FILE_SHARE_READ = 0x00000001
WINDOWS_FILE_SHARE_WRITE = 0x00000002
WINDOWS_SHARING_VIOLATION_REASON = 'The process cannot access the file because it is being used by another process'


def run_python_without_windows_privileges(*arguments: str) -> int:  # noqa: PLR0915 - Win32 process setup is deliberately localized here.
    """
    Start this Python interpreter under a restricted Windows access token.

    This helper is intentionally kept next to its single test because it is
    not application logic: it prepares a realistic Windows test environment.
    ``CreateRestrictedToken`` clones the token belonging to the pytest
    process and, with ``DISABLE_MAX_PRIVILEGE``, removes optional privileges
    from the child.  Microsoft documents ``CreateProcessAsUserW`` as the
    launcher for a process that uses this restricted token.  For a restricted
    version of the caller's own primary token, Windows does not require
    ``SeAssignPrimaryTokenPrivilege``; the API temporarily enables a required
    privilege that is already present but disabled on the caller's token.
    The child preserves the current environment and working directory.

    Preserving the environment is significant for CI coverage.  The workflow
    sets ``COVERAGE_PROCESS_START`` and installs a ``.pth`` startup hook in
    the active interpreter.  The child therefore starts coverage normally,
    writes its own parallel data file on exit, and the existing
    ``coverage combine`` command merges code executed in this child into the
    final report.

    The caller passes a script file path rather than a substantial inline
    ``python -c`` program.  Apart from keeping native process-launch details
    readable, this avoids coupling the test to a launcher's command-line
    length and quoting behavior.
    """
    if os_name != 'nt':
        raise RuntimeError('Restricted Windows Python processes can only be started on Windows.')

    from ctypes import (  # type: ignore[attr-defined]  # noqa: PLC0415 - these APIs exist only on Windows.
        POINTER,
        Structure,
        WinDLL,
        byref,
        c_void_p,
        create_unicode_buffer,
        get_last_error,
        sizeof,
    )
    from ctypes import (  # noqa: PLC0415 - Windows-only helper.
        cast as ctypes_cast,
    )
    from ctypes.wintypes import (  # noqa: PLC0415 - Windows-only helper.
        BOOL,
        BYTE,
        DWORD,
        HANDLE,
        LPVOID,
        LPWSTR,
        WORD,
    )

    class StartupInfo(Structure):
        """Declare the Win32 startup record required to create a process."""

        _fields_ = [  # noqa: RUF012 - ctypes describes native records through mutable class metadata.
            ('cb', DWORD),
            ('lpReserved', LPWSTR),
            ('lpDesktop', LPWSTR),
            ('lpTitle', LPWSTR),
            ('dwX', DWORD),
            ('dwY', DWORD),
            ('dwXSize', DWORD),
            ('dwYSize', DWORD),
            ('dwXCountChars', DWORD),
            ('dwYCountChars', DWORD),
            ('dwFillAttribute', DWORD),
            ('dwFlags', DWORD),
            ('wShowWindow', WORD),
            ('cbReserved2', WORD),
            ('lpReserved2', POINTER(BYTE)),
            ('hStdInput', HANDLE),
            ('hStdOutput', HANDLE),
            ('hStdError', HANDLE),
        ]

    class ProcessInformation(Structure):
        """Declare the Win32 handles returned for a newly created process."""

        _fields_ = [  # noqa: RUF012 - ctypes describes native records through mutable class metadata.
            ('hProcess', HANDLE),
            ('hThread', HANDLE),
            ('dwProcessId', DWORD),
            ('dwThreadId', DWORD),
        ]

    kernel32 = WinDLL('kernel32', use_last_error=True)
    advapi32 = WinDLL('advapi32', use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [HANDLE]
    close_handle.restype = BOOL
    get_current_process = kernel32.GetCurrentProcess
    get_current_process.argtypes = []
    get_current_process.restype = HANDLE
    wait_for_single_object = kernel32.WaitForSingleObject
    wait_for_single_object.argtypes = [HANDLE, DWORD]
    wait_for_single_object.restype = DWORD
    get_exit_code_process = kernel32.GetExitCodeProcess
    get_exit_code_process.argtypes = [HANDLE, POINTER(DWORD)]
    get_exit_code_process.restype = BOOL
    open_process_token = advapi32.OpenProcessToken
    open_process_token.argtypes = [HANDLE, DWORD, POINTER(HANDLE)]
    open_process_token.restype = BOOL
    create_restricted_token = advapi32.CreateRestrictedToken
    create_restricted_token.argtypes = [
        HANDLE,
        DWORD,
        DWORD,
        LPVOID,
        DWORD,
        LPVOID,
        DWORD,
        LPVOID,
        POINTER(HANDLE),
    ]
    create_restricted_token.restype = BOOL
    create_process_as_user = advapi32.CreateProcessAsUserW
    create_process_as_user.argtypes = [
        HANDLE,
        LPWSTR,
        LPWSTR,
        LPVOID,
        LPVOID,
        BOOL,
        DWORD,
        LPVOID,
        LPWSTR,
        POINTER(StartupInfo),
        POINTER(ProcessInformation),
    ]
    create_process_as_user.restype = BOOL

    token_access = 0x0001 | 0x0002 | 0x0008
    disable_max_privilege = 0x00000001
    create_unicode_environment = 0x00000400
    infinite_wait = 0xFFFFFFFF
    process_token = HANDLE()
    restricted_token = HANDLE()
    startup_information = StartupInfo()
    startup_information.cb = sizeof(StartupInfo)
    process_information = ProcessInformation()
    command_line = create_unicode_buffer(list2cmdline([executable, *arguments]))
    environment_block = create_unicode_buffer(''.join(f'{name}={value}\0' for name, value in environ.items()) + '\0')

    with ExitStack() as handles:
        if not open_process_token(get_current_process(), token_access, byref(process_token)):
            raise OSError(get_last_error(), 'Could not open the current Windows process token.')

        handles.callback(close_handle, process_token)

        if not create_restricted_token(
            process_token,
            disable_max_privilege,
            0,
            None,
            0,
            None,
            0,
            None,
            byref(restricted_token),
        ):
            raise OSError(get_last_error(), 'Could not create a restricted Windows process token.')

        handles.callback(close_handle, restricted_token)

        if not create_process_as_user(
            restricted_token,
            str(executable),
            command_line,
            None,
            None,
            False,
            create_unicode_environment,
            ctypes_cast(environment_block, c_void_p),
            str(Path.cwd()),
            byref(startup_information),
            byref(process_information),
        ):
            raise OSError(get_last_error(), 'Could not start Python through a restricted Windows process token.')

        handles.callback(close_handle, process_information.hThread)
        handles.callback(close_handle, process_information.hProcess)
        wait_for_single_object(process_information.hProcess, infinite_wait)
        exit_code = DWORD()

        if not get_exit_code_process(process_information.hProcess, byref(exit_code)):
            raise OSError(get_last_error(), 'Could not read the restricted Python process exit code.')

        return int(exit_code.value)


def assert_isolate_deleted(operation):
    with pytest.raises(IsolateDeletedError, match=match('Isolate has been deleted.')):
        operation()


def test_temp_base_none_uses_stdlib_temp(tmp_path, monkeypatch):
    """Verify that default temporary isolates use distinct stdlib temp roots and log both creations."""
    monkeypatch.chdir(tmp_path)

    logger = MemoryLogger()
    throng = TemporaryDirectoryThrong(logger=logger)
    first = throng.get_isolate()
    second = throng.get_isolate()

    assert first.directory.exists()
    assert second.directory.exists()
    assert first.directory != second.directory
    assert Path(gettempdir()).resolve() in first.directory.resolve().parents
    assert Path(gettempdir()).resolve() in second.directory.resolve().parents
    assert tmp_path not in first.directory.parents
    assert tmp_path not in second.directory.parents
    assert [str(call.message) for call in logger.data.info] == [
        f'Creating temporary isolate in stdlib temporary directory "{first.directory}".',
        f'Creating temporary isolate in stdlib temporary directory "{second.directory}".',
    ]


def test_temp_base_directory_uuid_subdir(tmp_path):
    """Verify that a configured temporary base receives a UUID-hex child and logs its creation."""
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path))
    logger = MemoryLogger()

    isolate = TemporaryDirectoryThrong(logger=logger, config=config).get_isolate()

    assert isolate.directory.parent == tmp_path
    assert len(isolate.directory.name) == 32
    assert all(character in '0123456789abcdef' for character in isolate.directory.name)
    assert isolate.directory.is_dir()
    assert [str(call.message) for call in logger.data.info] == [
        f'Creating temporary isolate "{isolate.directory}" inside base directory "{tmp_path}".',
    ]


def test_temp_base_missing(tmp_path):
    """Verify that a missing temporary base directory raises and logs InvalidBaseDirectoryError."""
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path / 'missing'))
    logger = MemoryLogger()

    with pytest.raises(InvalidBaseDirectoryError, match=match(f'Temporary base directory does not exist: {tmp_path / "missing"}')):
        TemporaryDirectoryThrong(logger=logger, config=config).get_isolate()

    assert [str(call.message) for call in logger.data.error] == [
        f'Temporary base directory does not exist: {tmp_path / "missing"}',
    ]


def test_temp_base_file(tmp_path):
    """Verify that a file temporary base path raises and logs InvalidBaseDirectoryError."""
    file_path = tmp_path / 'file'
    file_path.write_text('content')
    config = TemporaryDirectoryIsolationConfig(base_directory=str(file_path))
    logger = MemoryLogger()

    with pytest.raises(InvalidBaseDirectoryError, match=match(f'Temporary base path is not a directory: {file_path}')):
        TemporaryDirectoryThrong(logger=logger, config=config).get_isolate()

    assert [str(call.message) for call in logger.data.error] == [
        f'Temporary base path is not a directory: {file_path}',
    ]


@pytest.mark.skipif(os_name == 'nt', reason='symlink creation often requires elevated privileges on Windows')
def test_temp_base_symlink_to_file(tmp_path):
    """Verify that a file symlink temporary base path raises and logs InvalidBaseDirectoryError."""
    target_file = tmp_path / 'file'
    target_file.write_text('content')
    symlink_path = tmp_path / 'link'
    symlink_path.symlink_to(target_file)
    config = TemporaryDirectoryIsolationConfig(base_directory=str(symlink_path))
    logger = MemoryLogger()

    with pytest.raises(InvalidBaseDirectoryError, match=match(f'Temporary base path is not a directory: {symlink_path}')):
        TemporaryDirectoryThrong(logger=logger, config=config).get_isolate()

    assert [str(call.message) for call in logger.data.error] == [
        f'Temporary base path is not a directory: {symlink_path}',
    ]


@pytest.mark.skipif(os_name == 'nt', reason='permission mode semantics differ on Windows')
def test_temp_base_not_writable(tmp_path, request):
    """Verify that a native POSIX creation denial is normalized, chained, and logged."""
    base_directory = tmp_path / 'base'
    base_directory.mkdir()
    request.addfinalizer(lambda: base_directory.chmod(S_IREAD | S_IWRITE | S_IEXEC))
    base_directory.chmod(S_IREAD | S_IEXEC)
    config = TemporaryDirectoryIsolationConfig(base_directory=str(base_directory))
    logger = MemoryLogger()

    with pytest.raises(InvalidBaseDirectoryError, match=match(f'Temporary base directory is not writable: {base_directory}')) as raised:
        TemporaryDirectoryThrong(logger=logger, config=config).get_isolate()

    assert isinstance(raised.value.__cause__, PermissionError)
    assert list(base_directory.iterdir()) == []
    assert [str(call.message) for call in logger.data.exception] == [
        f'Temporary base directory is not writable: {base_directory}',
    ]


@pytest.mark.skipif(os_name != 'nt', reason='Windows access control lists are not available on POSIX')
def test_temp_base_denied_subdirectory_creation_on_windows_is_rejected_and_logged(tmp_path, request):
    """
    Verify that Windows rejects and logs a configured base that cannot hold a child isolate.

    On Windows, a directory does not become unwritable merely because its
    read-only attribute is set.  Access is primarily determined by the
    directory's discretionary access control list (DACL): its access control
    entries may explicitly allow or deny rights to a user.  ``icacls`` adds a
    deny entry for ``AD`` ("add subdirectory") here, which is the exact right
    needed when the plugin creates the isolate's UUID-named child directory.

    There is one additional Windows rule that matters in GitHub Actions.
    Every process has an access token that identifies both the user and extra
    privileges held by that process.  The hosted Windows runner enables
    ``SeBackupPrivilege`` and ``SeRestorePrivilege`` on pytest's token.
    Restore privilege can allow a process to create filesystem objects even
    when an ordinary DACL-based write attempt would fail.  Consequently the
    parent pytest process is intentionally unsuitable for checking this
    permission-denied branch: it can bypass the denial that an ordinary user
    process would observe.

    The test therefore keeps pytest as the supervising parent but executes the
    library call in a second Python process created through the Win32 APIs
    ``CreateRestrictedToken(DISABLE_MAX_PRIVILEGE)`` and
    ``CreateProcessAsUserW``.  This is the documented API pair for running a
    process under a restricted copy of the caller's own primary token: the
    child still has the same user identity,
    Python installation, imports and normal filesystem access outside this
    denied directory, but it no longer has optional token privileges capable
    of bypassing the DACL.  The child records its result and ``MemoryLogger``
    messages as JSON because Python exception and logger objects cannot be
    directly asserted across process boundaries.

    This subprocess does not hide executed code from coverage.  CI starts
    coverage in Python subprocesses with a site ``.pth`` hook controlled by
    the inherited ``COVERAGE_PROCESS_START`` variable.  This child inherits
    that environment, runs the same interpreter without ``-S``, and keeps the
    repository as its working directory.  Coverage therefore writes a normal
    parallel child data file, which the workflow's existing
    ``coverage combine`` step includes in the 100-percent report.

    The child program is saved to a temporary ``.py`` file instead of being
    supplied through ``python -c`` so that native process-launch mechanics do
    not depend on the length or quoting of this explanatory test program.
    """
    base_directory = tmp_path / 'base'
    base_directory.mkdir()
    user_name = run_process(['whoami'], check=True, capture_output=True, text=True).stdout.strip()
    request.addfinalizer(lambda: run_process(['icacls', str(base_directory), '/remove:d', user_name], check=True, capture_output=True))
    run_process(['icacls', str(base_directory), '/deny', f'{user_name}:(AD)'], check=True, capture_output=True)
    result_path = tmp_path / 'restricted-child-result.json'
    child_script_path = tmp_path / 'restricted-child.py'
    child_script = dedent(
        """
        from json import dumps
        from pathlib import Path
        from subprocess import run
        from sys import argv

        from emptylog import MemoryLogger

        from throng.plugins.temporary_directory_throng import (
            TemporaryDirectoryIsolationConfig,
            TemporaryDirectoryThrong,
        )

        base_directory = Path(argv[1])
        result_path = Path(argv[2])
        logger = MemoryLogger()
        privilege_listing = run(['whoami', '/priv'], check=True, capture_output=True, text=True).stdout

        try:
            TemporaryDirectoryThrong(
                logger=logger,
                config=TemporaryDirectoryIsolationConfig(base_directory=str(base_directory)),
            ).get_isolate()
        except Exception as error:
            result = {
                'exception_type': type(error).__name__,
                'message': str(error),
                'cause_type': type(error.__cause__).__name__ if error.__cause__ is not None else None,
                'exception_logs': [str(call.message) for call in logger.data.exception],
                'privilege_listing': privilege_listing,
            }
        else:
            result = {
                'exception_type': None,
                'message': None,
                'cause_type': None,
                'exception_logs': [str(call.message) for call in logger.data.exception],
                'privilege_listing': privilege_listing,
            }

        result_path.write_text(dumps(result))
        """,
    )
    child_script_path.write_text(child_script)

    child_exit_code = run_python_without_windows_privileges(str(child_script_path), str(base_directory), str(result_path))
    result = loads(result_path.read_text())

    assert child_exit_code == 0
    assert not any('SeBackupPrivilege' in line and 'Enabled' in line for line in result['privilege_listing'].splitlines())
    assert not any('SeRestorePrivilege' in line and 'Enabled' in line for line in result['privilege_listing'].splitlines())
    assert result['exception_type'] == 'InvalidBaseDirectoryError'
    assert result['message'] == f'Temporary base directory is not writable: {base_directory}'
    assert result['cause_type'] == 'PermissionError'
    assert list(base_directory.iterdir()) == []
    assert result['exception_logs'] == [
        f'Temporary base directory is not writable: {base_directory}',
    ]


def test_temp_isolates_are_distinct(tmp_path):
    """Verify that temporary isolates get separate directories and do not share files."""
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path))
    throng = TemporaryDirectoryThrong(config=config)
    first = throng.get_isolate()
    second = throng.get_isolate()

    (first.directory / 'only-first').write_text('content')

    assert first.directory != second.directory
    assert (first.directory / 'only-first').read_text() == 'content'
    assert not (second.directory / 'only-first').exists()


def test_temp_cross_isolate_file_contamination(tmp_path):
    """Verify that commands in one temporary isolate cannot see files created in another isolate."""
    throng = TemporaryDirectoryThrong(config=TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path)))
    first = throng.get_isolate()
    second = throng.get_isolate()

    first.run(executable, '-c', 'from pathlib import Path; Path("created.txt").write_text("first")', split=False)

    assert (first.directory / 'created.txt').read_text() == 'first'
    assert not (second.directory / 'created.txt').exists()


def test_temp_no_shared_command_lock(tmp_path, monkeypatch):
    """Verify that run calls on different temporary isolates can overlap because they do not share a command lock."""
    barrier = Barrier(2)

    def fake_run(*_args, **_kwargs):
        barrier.wait(timeout=5)
        return SubprocessResult(id='temp-distinct', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)
    throng = TemporaryDirectoryThrong(config=TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path)))
    first = throng.get_isolate()
    second = throng.get_isolate()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(first.run, 'anything'),
            executor.submit(second.run, 'anything'),
        ]
        for future in futures:
            future.result()

    assert barrier.n_waiting == 0


def test_temp_same_isolate_operations_are_not_serialized(tmp_path, monkeypatch):
    """Verify that run and install on one temporary isolate can overlap because the isolate uses EmptyLock."""
    entered = 0
    barrier = Barrier(2)
    guard = Lock()

    def fake_run(*_args, **_kwargs):
        nonlocal entered
        with guard:
            entered += 1
        barrier.wait(timeout=5)
        return SubprocessResult(id='temp-serialized', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=False)
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(isolate.run, 'anything'),
            executor.submit(isolate.install, 'example'),
        ]
        for future in futures:
            future.result()

    assert entered == 2
    assert barrier.n_waiting == 0


def test_temporary_throng_isolate_empty_lock_does_not_serialize_operations(tmp_path, monkeypatch):
    """Verify that an isolate returned by TemporaryDirectoryThrong enters a non-serializing lock for every operation."""
    entered = 0
    barrier = Barrier(5)
    guard = Lock()
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=False)
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()
    lock_trace = LockTraceWrapper(isolate.lock)
    isolate.lock = lock_trace

    def traced_operation(name, operation_result):
        def wrapper(*_args, **_kwargs):
            nonlocal entered
            lock_trace.notify(name)
            with guard:
                entered += 1
            barrier.wait(timeout=5)
            return operation_result

        return wrapper

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', traced_operation('run', SubprocessResult(id='temp-run', returncode=0)))
    monkeypatch.setattr(DirectoryIsolate, '_install_unlocked', traced_operation('install', None))
    monkeypatch.setattr(DirectoryIsolate, '_dump_unlocked', traced_operation('dump', b''))
    monkeypatch.setattr(DirectoryIsolate, '_load_unlocked', traced_operation('load', None))
    monkeypatch.setattr(DirectoryIsolate, '_delete_unlocked', traced_operation('delete', None))

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [
            executor.submit(isolate.run, 'anything'),
            executor.submit(isolate.install, 'example'),
            executor.submit(isolate.dump),
            executor.submit(isolate.load, b''),
            executor.submit(isolate.delete),
        ]
        for future in futures:
            future.result()

    assert lock_trace.was_event_locked('run')
    assert lock_trace.was_event_locked('install')
    assert lock_trace.was_event_locked('dump')
    assert lock_trace.was_event_locked('load')
    assert lock_trace.was_event_locked('delete')
    assert entered == 5
    assert barrier.n_waiting == 0


def test_temporary_throng_isolate_empty_lock_allows_concurrent_runs(tmp_path, monkeypatch):
    """Verify that an isolate returned by TemporaryDirectoryThrong permits concurrent runs."""
    entered = 0
    barrier = Barrier(2)
    guard = Lock()
    config = TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path), use_venv=False)
    isolate = TemporaryDirectoryThrong(config=config).get_isolate()
    lock_trace = LockTraceWrapper(isolate.lock)
    isolate.lock = lock_trace

    def traced_run(*_args, **_kwargs):
        nonlocal entered
        lock_trace.notify('run')
        with guard:
            entered += 1
        barrier.wait(timeout=5)
        return SubprocessResult(id='temp-run', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', traced_run)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(isolate.run, 'anything'),
            executor.submit(isolate.run, 'anything'),
        ]
        for future in futures:
            future.result()

    assert lock_trace.was_event_locked('run')
    assert entered == 2
    assert barrier.n_waiting == 0


def test_temp_delete_removes_owned_directory(tmp_path):
    """Verify that delete removes an owned temporary-directory isolate when removal is deterministic."""
    throng = TemporaryDirectoryThrong(config=TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path)))
    isolate = throng.get_isolate()
    isolate_directory = isolate.directory

    isolate.delete()

    assert not isolate_directory.exists()


def test_temp_delete_operation_logger_is_added_to_inherited_logger(tmp_path):
    """Verify that delete lifecycle records are emitted to inherited and operation loggers."""
    inherited_logger = MemoryLogger()
    operation_logger = MemoryLogger()
    throng = TemporaryDirectoryThrong(
        logger=inherited_logger,
        config=TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path)),
    )
    isolate = throng.get_isolate()
    inherited_info_before_delete = len(inherited_logger.data.info)

    isolate.delete(logger=operation_logger)

    expected_delete_messages = [
        f'Deleting isolate "{isolate.directory}".',
        'Delete completed successfully.',
    ]
    assert [str(call.message) for call in inherited_logger.data.info[inherited_info_before_delete:]] == expected_delete_messages
    assert [str(call.message) for call in operation_logger.data.info] == expected_delete_messages


def test_temp_configured_base_is_cleaned_when_isolate_is_collected(tmp_path):
    """Verify that a temporary isolate inside a configured base is cleaned up if its owner is collected."""
    throng = TemporaryDirectoryThrong(config=TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path)))
    isolate = throng.get_isolate()
    isolate_directory = isolate.directory

    del isolate
    collect()

    assert not isolate_directory.exists()


@pytest.mark.skipif(os_name == 'nt', reason='permission mode semantics differ on Windows')
def test_temp_delete_failure_raises_and_does_not_log_success(tmp_path, request):
    """
    Verify that a natural delete permission failure is logged and retryable.

    CPython 3.12 preserves the ``Path`` argument in ``shutil.rmtree`` error
    text, while earlier supported versions stringify it; the operation
    contract is the same in both forms.
    """
    base_directory = tmp_path / 'base'
    base_directory.mkdir()
    logger = MemoryLogger()
    throng = TemporaryDirectoryThrong(logger=logger, config=TemporaryDirectoryIsolationConfig(base_directory=str(base_directory)))
    isolate = throng.get_isolate()
    isolate_directory = isolate.directory
    request.addfinalizer(lambda: base_directory.chmod(S_IREAD | S_IWRITE | S_IEXEC))
    base_directory.chmod(S_IREAD | S_IEXEC)
    denied_path = isolate_directory if version_info >= (3, 12) else str(isolate_directory)
    permission_error_message = str(PermissionError(EACCES, 'Permission denied', denied_path))

    with pytest.raises(PermissionError, match=match(permission_error_message)):
        isolate.delete()

    assert isolate_directory.exists()
    assert any('Delete failed' in str(call.message) for call in logger.data.exception)
    assert all(str(call.message) != 'Delete completed successfully.' for call in logger.data.info)

    base_directory.chmod(S_IREAD | S_IWRITE | S_IEXEC)
    isolate.delete()

    assert not isolate_directory.exists()
    assert [str(call.message) for call in logger.data.info].count('Delete completed successfully.') == 1


@pytest.mark.skipif(os_name != 'nt', reason='Windows sharing violations are not available on POSIX')
def test_temp_delete_locked_windows_directory_raises_and_can_be_retried(tmp_path):
    """
    Verify that a native Windows delete failure is logged and leaves deletion retryable.

    An open directory handle without delete sharing blocks the first cleanup;
    after the handle closes, the same isolate must be deletable successfully.
    """
    logger = MemoryLogger()
    throng = TemporaryDirectoryThrong(logger=logger, config=TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path)))
    isolate = throng.get_isolate()
    isolate_directory = isolate.directory
    permission_error_message = str(PermissionError(EACCES, WINDOWS_SHARING_VIOLATION_REASON, str(isolate_directory), 32))

    with hold_windows_path_open(
        isolate_directory,
        share_mode=WINDOWS_FILE_SHARE_READ | WINDOWS_FILE_SHARE_WRITE,
        flags=WINDOWS_DIRECTORY_HANDLE_FLAGS,
    ), pytest.raises(PermissionError, match=match(permission_error_message)):
        isolate.delete()

    assert isolate_directory.exists()
    assert any('Delete failed' in str(call.message) for call in logger.data.exception)
    assert all(str(call.message) != 'Delete completed successfully.' for call in logger.data.info)

    isolate.delete()

    assert not isolate_directory.exists()
    assert [str(call.message) for call in logger.data.info].count('Delete completed successfully.') == 1


def test_temp_delete_after_delete_raises(tmp_path):
    """Verify that a second delete call is rejected and logged as a delete operation."""
    logger = MemoryLogger()
    throng = TemporaryDirectoryThrong(logger=logger, config=TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path)))
    isolate = throng.get_isolate()

    isolate.delete()

    assert_isolate_deleted(isolate.delete)
    assert any(str(call.message) == 'Delete rejected because the isolate has been deleted.' for call in logger.data.error)


def test_temp_delete_stdlib_temp_manager(tmp_path, monkeypatch):
    """Verify that delete removes stdlib-managed temporary isolate directories."""
    monkeypatch.chdir(tmp_path)

    isolate = cast(DirectoryIsolate, throngs()['temporary_directory'].get_isolate())
    isolate_directory = isolate.directory

    isolate.delete()

    assert not isolate_directory.exists()


def test_temp_delete_does_not_wait_for_running_operation(tmp_path, monkeypatch):
    """Verify that temporary isolate delete is not serialized behind an in-flight run and does not hide the run result."""
    started = Event()
    release = Event()
    delete_finished = Event()

    def blocking_run(*_args, **_kwargs):
        started.set()
        release.wait(timeout=5)
        return SubprocessResult(id='blocking-run', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', blocking_run)
    throng = TemporaryDirectoryThrong(config=TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path)))
    isolate = throng.get_isolate()
    isolate_directory = isolate.directory

    with ThreadPoolExecutor(max_workers=2) as executor:
        run_future = executor.submit(isolate.run, 'anything')
        assert started.wait(timeout=5)
        delete_future = executor.submit(isolate.delete)
        delete_future.add_done_callback(lambda _future: delete_finished.set())

        assert delete_finished.wait(timeout=5)
        assert not isolate_directory.exists()

        release.set()
        delete_future.result()
        assert run_future.result().id == 'blocking-run'


def test_temp_concurrent_delete_only_succeeds_once(tmp_path):
    """Verify that concurrent temporary isolate deletion has one winner and then rejects the already-deleted isolate."""
    release_cleanup = Event()
    first_cleanup_entered = Event()
    second_future_done = False
    cleanup_entries = 0
    condition = Condition()

    class BlockingCleanupManager:
        def cleanup(self):
            nonlocal cleanup_entries
            with condition:
                cleanup_entries += 1
                if cleanup_entries == 1:
                    first_cleanup_entered.set()
                condition.notify_all()
            release_cleanup.wait(timeout=5)

    isolate = DirectoryIsolate(
        tmp_path,
        DirectoryIsolationConfig(),
        lock=EmptyLock(),
        temporary_directory_manager=cast('TemporaryDirectory[str]', BlockingCleanupManager()),
    )

    def delete_once():
        try:
            isolate.delete()
        except IsolateDeletedError:
            return 'already-deleted'
        return 'deleted'

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(delete_once)
        assert first_cleanup_entered.wait(timeout=5)
        second_future = executor.submit(delete_once)

        def mark_second_done(_future):
            nonlocal second_future_done
            with condition:
                second_future_done = True
                condition.notify_all()

        second_future.add_done_callback(mark_second_done)
        with condition:
            assert condition.wait_for(lambda: cleanup_entries == 2 or second_future_done, timeout=5)
        release_cleanup.set()
        results = [first_future.result(), second_future.result()]

    assert sorted(results) == ['already-deleted', 'deleted']


@pytest.mark.parametrize('operation', ['run', 'install', 'dump', 'load', 'delete'])
def test_temp_operations_after_delete_raise(tmp_path, operation):
    """Verify that every temporary isolate operation raises IsolateDeletedError after delete."""
    isolate = TemporaryDirectoryThrong(config=TemporaryDirectoryIsolationConfig(base_directory=str(tmp_path))).get_isolate()
    isolate.delete()

    operations = {
        'run': lambda: isolate.run('printf never'),
        'install': lambda: isolate.install('example'),
        'dump': isolate.dump,
        'load': lambda: isolate.load(make_tar_bytes({'file.txt': b'content'})),
        'delete': isolate.delete,
    }

    assert_isolate_deleted(operations[operation])
