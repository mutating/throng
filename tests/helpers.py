from io import BytesIO
from pathlib import Path
from tarfile import TarInfo
from tarfile import open as open_tar
from typing import Dict, Iterable, Optional

from dirstree import Crawler
from emptylog.call_data import LoggerCallData


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
