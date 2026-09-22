from pathlib import Path
from io import BytesIO
import tarfile

from dirstree import Crawler




def read_directory(path: Path) -> bytes:
    buffer = BytesIO()
    crawler = Crawler(path)

    with tarfile.open(fileobj=buffer, mode='w') as tar:
        for file in crawler:
            tar.add(file)

    return buffer.getvalue()
