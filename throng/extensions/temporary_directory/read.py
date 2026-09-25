import tarfile
from io import BytesIO
from pathlib import Path
from typing import List, Optional

from dirstree import Crawler


def read_directory(path: Path, exclude: Optional[List[str]]) -> bytes:
    buffer = BytesIO()
    crawler = Crawler(path, exclude=exclude)

    with tarfile.open(fileobj=buffer, mode='w') as tar:
        for file in crawler:
            tar.add(file)

    return buffer.getvalue()
