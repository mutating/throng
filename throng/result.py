from dataclasses import dataclass
from typing import Optional


@dataclass
class RunResult:
    """Store backend-independent command output and completion status."""

    id: str
    stdout: Optional[str]
    stderr: Optional[str]
    returncode: Optional[int]

    @property
    def success(self) -> bool:
        """Return ``True`` exactly when ``returncode`` is zero."""
        return self.returncode == 0
