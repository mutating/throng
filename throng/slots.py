from pathlib import Path
from typing import Dict, List, Optional, Union

from pristan import slot

from throng import AbstractManager


@slot(entrypoint_group='throng')
def throng(path: Union[str, Path] = '.', exclude: Optional[List[str]] = None) -> Dict[str, AbstractManager]:  # type: ignore[empty-body]
    ...
