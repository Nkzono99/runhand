"""Disposable scratch task allocation and pinning."""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from .config import Config
from .errors import PlanError, UnsafeError
from .result import Result, warning
from .storage import (
    atomic_write_json,
    ensure_owned_root,
    is_within,
    read_json,
    require_owned_root,
    root_is_owned,
    utc_now,
)

TASK_META = ".runhand-task.json"
TASK_KINDS = {"smoke", "pilot", "debug", "analysis", "preview"}


def _key_digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _task_candidates(root: Path, kind: str, key_digest: str) -> list[dict[str, Any]]:
    if not root_is_owned(root, "scratch"):
        return []
    parent = root / "tasks" / kind
    if not parent.is_dir():
        return []
    candidates: list[dict[str, Any]] = []
    for child in sorted(parent.iterdir(), key=lambda path: os.fsencode(path.name)):
        meta_path = child / TASK_META
        if not child.is_dir() or not meta_path.is_file():
            continue
        try:
            meta = read_json(meta_path)
        except Exception:
            continue
        if meta.get("key_digest") != key_digest:
            continue
        candidates.append(
            {
                "task": str(child),
                "created_at": meta.get("created_at"),
                "last_used_at": meta.get("last_used_at"),
                "pinned": bool(meta.get("pinned", False)),
                "liveness": meta.get("liveness", "unknown"),
            }
        )
    return candidates


def get_scratch(
    config: Config, *, kind: str, key: str, pin: bool, dry_run: bool
) -> Result:
    if kind not in TASK_KINDS:
        raise PlanError("invalid_scratch_kind", "unsupported scratch task kind")
    if not key or not key.strip():
        raise PlanError("invalid_scratch_key", "scratch task key must not be empty")
    digest = _key_digest(key)
    candidates = _task_candidates(config.scratch_root, kind, digest)
    warnings: list[dict[str, Any]] = []
    if candidates:
        warnings.append(
            warning(
                "reuse_requires_evidence",
                "matching task keys are hints only; a unique task will be allocated unless specialist evidence proves reuse",
                details={"candidate_count": len(candidates)},
            )
        )
    if dry_run:
        return Result(
            "scratch get",
            {
                "dry_run": True,
                "would_create_under": str(config.scratch_root / "tasks" / kind),
                "kind": kind,
                "key_digest": digest,
                "pinned": pin,
                "reuse_candidates": candidates,
                "decision": "new_unique_task",
            },
            warnings,
        )

    ensure_owned_root(config.scratch_root, "scratch")
    parent = config.scratch_root / "tasks" / kind
    parent.mkdir(parents=True, exist_ok=True)
    task = parent / f"task-{uuid.uuid4().hex}"
    try:
        task.mkdir(exist_ok=False)
        timestamp = utc_now()
        atomic_write_json(
            task / TASK_META,
            {
                "schema": 1,
                "owner": "runhand",
                "kind": kind,
                "key_digest": digest,
                "created_at": timestamp,
                "last_used_at": timestamp,
                "pinned": pin,
                "liveness": "unknown",
                "attempt_state": "none",
            },
        )
    except BaseException:
        shutil.rmtree(task, ignore_errors=True)
        raise
    return Result(
        "scratch get",
        {
            "dry_run": False,
            "task": str(task),
            "kind": kind,
            "key_digest": digest,
            "pinned": pin,
            "liveness": "unknown",
            "reuse_candidates": candidates,
            "decision": "new_unique_task",
        },
        warnings,
    )


def _load_task(config: Config, task_arg: str) -> tuple[Path, dict[str, Any]]:
    require_owned_root(config.scratch_root, "scratch")
    task = Path(task_arg).expanduser().resolve()
    if not is_within(task, config.scratch_root) or task == config.scratch_root:
        raise UnsafeError(
            "task_outside_scratch",
            "task is outside the configured scratch root",
            path=task,
        )
    meta_path = task / TASK_META
    if not task.is_dir() or not meta_path.is_file():
        raise PlanError(
            "invalid_scratch_task",
            "task is not a RunHand-owned scratch task",
            path=task,
        )
    meta = read_json(meta_path)
    if meta.get("schema") != 1 or meta.get("owner") != "runhand":
        raise PlanError(
            "invalid_scratch_metadata",
            "scratch task metadata is incompatible",
            path=meta_path,
        )
    return task, meta


def set_pin(config: Config, task_arg: str, *, pinned: bool, dry_run: bool) -> Result:
    task, meta = _load_task(config, task_arg)
    command = "scratch pin" if pinned else "scratch unpin"
    if dry_run:
        return Result(
            command,
            {
                "dry_run": True,
                "task": str(task),
                "current": bool(meta.get("pinned", False)),
                "would_set": pinned,
            },
        )
    updated = dict(meta)
    updated["pinned"] = pinned
    updated["last_used_at"] = utc_now()
    atomic_write_json(task / TASK_META, updated)
    return Result(command, {"dry_run": False, "task": str(task), "pinned": pinned})
