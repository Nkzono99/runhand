"""Bounded inspection of optional RunHand submission history."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .result import warning
from .storage import is_within, read_json, root_is_owned

HISTORY_KIND = "submission"
HISTORY_RESULTS = {"accepted", "rejected", "unknown", "unknown_unresolved"}


def is_history_record(value: dict[str, Any]) -> bool:
    """Return whether *value* is an owned, compatible history record."""
    return (
        value.get("schema") == 1
        and value.get("owner") == "runhand"
        and value.get("kind") == HISTORY_KIND
        and value.get("result") in HISTORY_RESULTS
    )


def history_summary(state_root: Path, *, recent_limit: int = 20) -> dict[str, Any]:
    """Return safe recent/unresolved history without trusting foreign files."""
    root = state_root / "history"
    summary: dict[str, Any] = {
        "root": str(root),
        "status": "absent",
        "scanned": 0,
        "recent": [],
        "unresolved": [],
        "invalid": [],
        "invalid_count": 0,
    }
    if not root.exists() and not root.is_symlink():
        return summary
    if not root_is_owned(state_root, "state"):
        summary["status"] = "unowned"
        return summary
    if root.is_symlink() or (root.exists() and not is_within(root, state_root)):
        summary["status"] = "unowned"
        return summary
    if not root.is_dir():
        summary["status"] = "unreadable"
        return summary

    summary["status"] = "available"
    records: list[tuple[int, dict[str, Any]]] = []
    invalid: list[str] = []
    try:
        paths = sorted(root.iterdir(), key=lambda path: os.fsencode(path.name))
    except OSError:
        summary["status"] = "unreadable"
        return summary
    for path in paths:
        if path.name.startswith(".") or path.suffix != ".json":
            continue
        summary["scanned"] += 1
        try:
            if path.is_symlink() or not path.is_file():
                raise ValueError("history entry is not a regular file")
            value = read_json(path)
            if not is_history_record(value):
                raise ValueError("history entry is not an owned compatible record")
            modified_ns = path.stat().st_mtime_ns
        except Exception:
            invalid.append(str(path))
            continue
        entry: dict[str, Any] = {
            "path": str(path),
            "result": value["result"],
            "reconciled": value.get("reconciled"),
        }
        for key in ("target", "recorded_at", "submit_time", "attempt_state"):
            if isinstance(value.get(key), str):
                entry[key] = value[key]
        records.append((modified_ns, entry))

    records.sort(key=lambda item: (-item[0], os.fsencode(item[1]["path"])))
    recent = [entry for _, entry in records[:recent_limit]]
    unresolved = [
        entry["path"]
        for _, entry in records
        if entry["result"] in {"unknown", "unknown_unresolved"}
    ]
    summary["recent"] = recent
    summary["unresolved"] = unresolved
    summary["invalid"] = invalid[:recent_limit]
    summary["invalid_count"] = len(invalid)
    return summary


def history_warnings(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Translate history health into stable CLI warnings."""
    warnings: list[dict[str, Any]] = []
    if summary["status"] in {"unowned", "unreadable"}:
        warnings.append(
            warning(
                "history_unavailable",
                "optional submission history is not safely readable",
                path=summary["root"],
                details={"status": summary["status"]},
            )
        )
    if summary["invalid_count"]:
        warnings.append(
            warning(
                "history_records_invalid",
                "some optional submission history records are incompatible or unreadable",
                path=summary["root"],
                details={"count": summary["invalid_count"]},
            )
        )
    if summary["unresolved"]:
        warnings.append(
            warning(
                "unresolved_submission_evidence",
                "local history contains unresolved submission evidence; reconcile through the Site capability",
                details={"count": len(summary["unresolved"])},
            )
        )
    return warnings
