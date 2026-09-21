from pathlib import Path
from typing import Union, Optional

from throng import throng, LocalManager


@throng.plugin(unique=True)
def local(path: Optional[Union[str, Path]]) -> LocalManager:
    return LocalManager(path)
