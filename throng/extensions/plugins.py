from pathlib import Path
from typing import Optional, Union

from throng import throng
from throng.extensions.local.manager import LocalManager
from throng.extensions.temporary_directory.manager import TemporaryDirectoryManager


@throng.plugin(unique=True)
def local(path: Optional[Union[str, Path]]) -> LocalManager:
    return LocalManager(path)


@throng.plugin(unique=True)
def temporary_directory(path: Optional[Union[str, Path]]) -> TemporaryDirectoryManager:
    return TemporaryDirectoryManager(path)
