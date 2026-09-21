from typing import Optional, Protocol


class RunResultProtocol(Protocol):
    success: bool
    returncode: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
