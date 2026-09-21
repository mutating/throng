from typing import Protocol, Optional


class RunResultProtocol(Protocol):
    success: bool
    returncode: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
