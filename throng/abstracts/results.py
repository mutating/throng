from typing import Optional, Protocol
from dataclasses import dataclass


class RunResultProtocol(Protocol):
    success: bool
    returncode: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None


@dataclass
class SimpleRunResult:
    success: bool
    returncode: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
