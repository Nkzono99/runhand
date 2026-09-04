"""Machine and human output shared by every command."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Result:
    command: str
    data: dict[str, Any] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    def envelope(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "ok": True,
            "command": self.command,
            "data": self.data,
            "warnings": self.warnings,
            "errors": [],
        }


def warning(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    path: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "code": code,
        "message": message,
        "retryable": retryable,
    }
    if path is not None:
        value["path"] = path
    if details:
        value["details"] = details
    return value
