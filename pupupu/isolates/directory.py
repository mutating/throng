from pathlib import Path
from typing import Any, List, Optional, Union

from cantok import AbstractToken, DefaultToken
from emptylog import EmptyLogger, LoggerProtocol
from skelet import Field, Storage, for_tool

from pupupu import AbstractIsolate


class DirectoryIsolationConfig(Storage, sources=for_tool('pupupu')):
    venv_folder_name: str = Field('.venv')
    change_directories: bool = Field(False)


class DirectoryIsolate(AbstractIsolate):
    def run(self, *arguments: Union[str, Path], logger: LoggerProtocol = EmptyLogger(), split: bool = True, token: AbstractToken = DefaultToken(), timeout: Optional[Union[int, float]] = None) -> Any:   # noqa: B008
        ...

    def load(self, dump: bytes) -> None:
        ...

    def dump(self) -> bytes:
        ...

    def install(self, what: Union[str, List[str]]):
        ...
