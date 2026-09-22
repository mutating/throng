from pathlib import Path
from typing import Optional, Union

from throng import LocalManager, throng


@throng.plugin(unique=True)
def local(path: Optional[Union[str, Path]]) -> LocalManager:
    return LocalManager(path)


@throng.plugin(unique=True)
def temporary_directory(path: Optional[Union[str, Path]]) -> LocalManager:
    return LocalManager(path)
