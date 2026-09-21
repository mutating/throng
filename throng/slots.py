from pathlib import Path
from typing import Dict
from typing import Union, Optional

from pristan import slot

from throng import AbstractManager, LocalManager


@slot
def throng(path: Optional[Union[str, Path]]) -> Dict[str, AbstractManager]:
    ...
