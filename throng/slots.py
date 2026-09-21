from pathlib import Path
from typing import Dict, Optional, Union

from pristan import slot

from throng import AbstractManager


@slot(entrypoint_group='throng')
def throng(path: Optional[Union[str, Path]]) -> Dict[str, AbstractManager]:
    ...
