"""Read-only installation and workspace diagnostics."""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .config import Config
from .result import Result, warning
from .storage import OWNER_FILE, read_json


def _path_status(path: Path, kind: str) -> dict[str, Any]:
    existing = path
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    writable = existing.exists() and os.access(existing, os.W_OK | os.X_OK)
    marker = path / OWNER_FILE
    owned: bool | None = None
    if marker.is_file():
        try:
            value = read_json(marker)
            owned = value.get("owner") == "runhand" and value.get("kind") == kind
        except Exception:
            owned = False
    return {
        "path": str(path),
        "exists": path.exists(),
        "nearest_existing_parent": str(existing),
        "writable": writable,
        "owned": owned,
    }


def run_doctor(config: Config) -> Result:
    warnings: list[dict[str, Any]] = []
    scratch = _path_status(config.scratch_root, "scratch")
    state = _path_status(config.state_root, "state")
    for label, status in (("scratch", scratch), ("state", state)):
        if not status["writable"]:
            warnings.append(
                warning(
                    f"{label}_not_writable",
                    f"{label} root cannot be created or written from this host",
                    path=status["path"],
                )
            )
        if status["owned"] is False:
            warnings.append(
                warning(
                    f"{label}_owner_invalid",
                    f"{label} root has an incompatible RunHand owner marker",
                    path=status["path"],
                )
            )

    repository_root = Path(__file__).resolve().parents[2]
    manifest = repository_root / ".codex-plugin" / "plugin.json"
    skill = repository_root / "skills" / "runhand" / "SKILL.md"
    return Result(
        "doctor",
        {
            "version": __version__,
            "python": platform.python_version(),
            "python_supported": sys.version_info >= (3, 11),
            "platform": platform.platform(),
            "workspace": str(config.workspace),
            "config": config.public_dict(),
            "scratch": scratch,
            "state": state,
            "plugin_cues": {
                "manifest": str(manifest) if manifest.is_file() else None,
                "skill": str(skill) if skill.is_file() else None,
            },
        },
        warnings,
    )
