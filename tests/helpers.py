from contextlib import ExitStack, contextmanager
from io import BytesIO
from pathlib import Path
from sys import platform
from tarfile import TarInfo
from tarfile import open as open_tar
from typing import Dict, Iterable, Optional

from dirstree import Crawler
from emptylog.call_data import LoggerCallData

if platform == 'win32':
    from ctypes import (  # type: ignore[attr-defined]  # POSIX typeshed omits the Windows-only API.
        WinDLL,
        c_uint32,
        c_void_p,
        c_wchar_p,
        get_last_error,
    )


@contextmanager
def hold_windows_path_open(path: Path, *, share_mode: int, flags: int):
    """
    Keep a Windows filesystem object open with explicit sharing permissions.

    This helper is called only by Windows-only tests.  It uses the native API
    because Python's regular ``open`` does not let tests choose delete sharing.
    """
    if platform != 'win32':
        raise RuntimeError('Windows handles can only be held open on Windows.')

    kernel32 = WinDLL('kernel32', use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [c_wchar_p, c_uint32, c_uint32, c_void_p, c_uint32, c_uint32, c_void_p]
    create_file.restype = c_void_p
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [c_void_p]
    close_handle.restype = c_uint32

    generic_read = 0x80000000
    open_existing = 3
    invalid_handle = c_void_p(-1).value
    handle = create_file(str(path), generic_read, share_mode, None, open_existing, flags, None)

    if handle == invalid_handle:
        raise OSError(get_last_error(), f'Could not open Windows lock target: {path}')

    with ExitStack() as resources:
        resources.callback(close_handle, handle)

        yield


def assert_any_message_contains(calls: Iterable[LoggerCallData], *chunks: str) -> None:
    """Assert that at least one logged message contains all chunks, case-insensitively."""
    messages = [str(call.message) for call in calls]
    lowered_chunks = [chunk.lower() for chunk in chunks]

    for message in messages:
        lowered_message = message.lower()

        if all(chunk in lowered_message for chunk in lowered_chunks):
            return

    raise AssertionError(f'No log message contains {chunks!r}. Messages: {messages!r}')


def make_tar_bytes(
    entries: Dict[str, bytes],
    *,
    compression: str = 'none',
    modes: Optional[Dict[str, int]] = None,
) -> bytes:
    archive_buffer = BytesIO()
    archive_mode = {
        'none': 'w',
        'gzip': 'w:gz',
        'bz2': 'w:bz2',
        'lzma': 'w:xz',
    }[compression]

    with open_tar(fileobj=archive_buffer, mode=archive_mode) as archive:
        for name, content in entries.items():
            member_info = TarInfo(name)
            member_info.size = len(content)

            if modes is not None and name in modes:
                member_info.mode = modes[name]

            archive.addfile(member_info, BytesIO(content))

    return archive_buffer.getvalue()


def read_tree(root: Path) -> Dict[str, bytes]:
    file_contents: Dict[str, bytes] = {}

    for file_path in Crawler(root).go():
        file_contents[file_path.relative_to(root).as_posix()] = file_path.read_bytes()

    return file_contents
