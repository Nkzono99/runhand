"""Stable CLI errors and exit codes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class RunHandError(Exception):
    code: str
    message: str
    exit_code: int
    retryable: bool = False
    path: Path | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.path is not None:
            item["path"] = str(self.path)
        if self.details:
            item["details"] = self.details
        return item


class UsageError(RunHandError):
    def __init__(self, code: str, message: str, **kwargs: Any) -> None:
        super().__init__(code, message, 2, **kwargs)


class UnsafeError(RunHandError):
    def __init__(self, code: str, message: str, **kwargs: Any) -> None:
        super().__init__(code, message, 3, **kwargs)


class PlanError(RunHandError):
    def __init__(self, code: str, message: str, **kwargs: Any) -> None:
        super().__init__(code, message, 4, **kwargs)


class LocalIOError(RunHandError):
    def __init__(self, code: str, message: str, **kwargs: Any) -> None:
        super().__init__(code, message, 5, **kwargs)
