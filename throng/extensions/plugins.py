from pathlib import Path
from typing import List, Optional, Union

from throng import throng
from throng.extensions.local.manager import LocalManager
from throng.extensions.temporary_directory.manager import TemporaryDirectoryManager


@throng.plugin(unique=True)
def local(path: Union[str, Path] = '.', exclude: Optional[List[str]] = None) -> LocalManager:
    return LocalManager(path, exclude)


@throng.plugin(unique=True)
def temporary_directory(path: Union[str, Path] = '.', exclude: Optional[List[str]] = None) -> TemporaryDirectoryManager:
    return TemporaryDirectoryManager(path, exclude)
