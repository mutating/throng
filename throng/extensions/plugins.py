from pathlib import Path
from typing import List, Optional, Union

from throng import throng
from throng.extensions.directories.manager import DirectoriesManager
from throng.extensions.local.manager import LocalManager


@throng.plugin(unique=True)
def local(path: Union[str, Path] = '.', exclude: Optional[List[str]] = None, prepare: Optional[List[str]] = None, packages: Optional[List[str]] = None) -> LocalManager:
    return LocalManager(path, exclude, prepare, packages)


@throng.plugin(unique=True)
def directories(path: Union[str, Path] = '.', exclude: Optional[List[str]] = None, prepare: Optional[List[str]] = None, packages: Optional[List[str]] = None) -> DirectoriesManager:
    return DirectoriesManager(path, exclude, prepare, packages)
