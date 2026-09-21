from typing import Protocol, Optional


class RunResultProtocol(Protocol):
    success: bool
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    returncode: Optional[int] = None
