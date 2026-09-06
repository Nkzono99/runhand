"""Disposable scratch task allocation and pinning."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import uuid
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Config
from .copying import CopyPlan, copy_entries, select_entries
from .errors import PlanError, RunHandError, UnsafeError
from .result import Result
from .security import redact_sensitive
from .storage import (
    atomic_write_json,
    ensure_owned_subdir,
    is_within,
    managed_root_lock,
    read_json,
    require_owned_root,
    utc_now,
)

TASK_META = ".runhand-task.json"
TASK_KINDS = {"smoke", "pilot", "debug", "analysis", "preview"}


def _key_digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def get_scratch(
    config: Config, *, kind: str, key: str | None = None, pin: bool, dry_run: bool
) -> Result:
    if kind not in TASK_KINDS:
        raise PlanError("invalid_scratch_kind", "unsupported scratch task kind")
    if key is not None and not key.strip():
        raise PlanError("invalid_scratch_key", "scratch task key must not be empty")
    label = {"key_digest": _key_digest(key)} if key is not None else {}
    if dry_run:
        return Result(
            "scratch get",
            {
                "dry_run": True,
                "would_create_under": str(config.scratch_root / "tasks" / kind),
                "kind": kind,
                **label,
                "pinned": pin,
                "decision": "new_unique_task",
            },
        )

    parent = ensure_owned_subdir(config.scratch_root, "scratch", "tasks", kind)
    task = parent / f"task-{uuid.uuid4().hex}"
    task.mkdir(exist_ok=False)
    try:
        timestamp = utc_now()
        atomic_write_json(
            task / TASK_META,
            {
                "schema": 1,
                "owner": "runhand",
                "kind": kind,
                **label,
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
            **label,
            "pinned": pin,
            "liveness": "unknown",
            "decision": "new_unique_task",
        },
    )


def _load_task(config: Config, task_arg: str) -> tuple[Path, dict[str, Any]]:
    require_owned_root(config.scratch_root, "scratch")
    task = Path(os.path.abspath(Path(task_arg).expanduser()))
    if not is_within(task, config.scratch_root) or task == config.scratch_root:
        raise UnsafeError(
            "task_outside_scratch",
            "task is outside the configured scratch root",
            path=task,
        )
    meta_path = task / TASK_META
    if (
        task.parent.parent != config.scratch_root / "tasks"
        or task.parent.name not in TASK_KINDS
        or any(path.is_symlink() for path in (task, task.parent, task.parent.parent, meta_path))
        or not task.is_dir()
        or not meta_path.is_file()
    ):
        raise PlanError(
            "invalid_scratch_task",
            "task is not a RunHand-owned scratch task",
            path=task,
        )
    meta = read_json(meta_path)
    if (
        meta.get("schema") != 1
        or meta.get("owner") != "runhand"
        or meta.get("kind") != task.parent.name
    ):
        raise PlanError(
            "invalid_scratch_metadata",
            "scratch task metadata is incompatible",
            path=meta_path,
        )
    return task, meta


def prepare_scratch(
    config: Config, plan: CopyPlan, *, kind: str, key: str | None = None, pin: bool, dry_run: bool
) -> Result:
    """Copy once into a fresh task's mutable work directory; never publish a Run."""
    if is_within(config.scratch_root, plan.source) or is_within(
        config.scratch_root / "tasks", plan.source
    ):
        raise UnsafeError(
            "scratch_overlaps_source",
            "scratch root and task container must be outside the copy source",
            path=config.scratch_root,
        )
    if kind not in TASK_KINDS or (key is not None and not key.strip()):
        raise PlanError("invalid_scratch_request", "scratch kind must be supported and any supplied key must be nonempty")
    entries = select_entries(plan)
    selection = [
        {"path": entry.relative, "kind": entry.kind, "size": entry.size,
         **({"link_target": entry.link_target} if entry.link_target is not None else {})}
        for entry in entries if entry.kind != "guard"
    ]
    if not selection:
        raise PlanError("empty_copy_selection", "scratch preparation requires at least one selected file or directory; use scratch get for empty work")
    description = {
        "copy_plan": plan.as_dict(),
        "selection": selection,
        "summary": {
            "directories": sum(entry["kind"] == "directory" for entry in selection),
            "files": sum(entry["kind"] == "file" for entry in selection),
            "symlinks": sum(entry["kind"] == "symlink" for entry in selection),
            "bytes": sum(entry["size"] for entry in selection),
        },
    }
    allocation = get_scratch(config, kind=kind, key=key, pin=pin, dry_run=dry_run)
    if dry_run:
        return Result("scratch prepare", {**allocation.data, **description, "work_subdir": "work"}, allocation.warnings)
    task = Path(allocation.data["task"])
    workdir = task / "work"
    try:
        with managed_root_lock(config.scratch_root, "scratch", target=task):
            _, metadata = _load_task(config, str(task))
            metadata.update(preparation="preparing", **description)
            atomic_write_json(task / TASK_META, metadata, create_parents=False)
            copy_entries(plan, entries, workdir)
            metadata.update(preparation="ready", last_used_at=utc_now())
            atomic_write_json(task / TASK_META, metadata, create_parents=False)
    except (RunHandError, OSError, KeyboardInterrupt) as exc:
        # The task may contain useful partial inputs. It remains protected as
        # unknown; expose its actual path even when path-only output was asked.
        if isinstance(exc, RunHandError):
            exc.details.update(task=str(task), workdir=str(workdir), preparation="incomplete")
            raise
        raise RunHandError(
            "scratch_prepare_failed", "scratch preparation did not finish", 5,
            path=task,
            details={"task": str(task), "workdir": str(workdir), "preparation": "incomplete", "reason": str(exc)},
        ) from exc
    return Result(
        "scratch prepare",
        {**allocation.data, **description, "workdir": str(workdir), "preparation": "ready"},
        allocation.warnings,
    )


def set_pin(config: Config, task_arg: str, *, pinned: bool, dry_run: bool) -> Result:
    command = "scratch pin" if pinned else "scratch unpin"
    task, _ = _load_task(config, task_arg)
    lock = nullcontext() if dry_run else managed_root_lock(config.scratch_root, "scratch", target=task)
    with lock:
        task, meta = _load_task(config, task_arg)
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
        atomic_write_json(task / TASK_META, updated, create_parents=False)
    return Result(command, {"dry_run": False, "task": str(task), "pinned": pinned})


def load_scratch_evidence(path: str) -> dict[str, Any]:
    try:
        if path == "-":
            value = json.load(sys.stdin)
        else:
            with Path(path).open("r", encoding="utf-8") as stream:
                value = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PlanError(
            "scratch_evidence_read_failed", f"cannot read scratch evidence: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise PlanError("invalid_scratch_evidence", "scratch evidence must be a JSON object")
    return value


def _evidence_time(value: Any) -> datetime:
    try:
        if not isinstance(value, str):
            raise ValueError("timestamp must be a string")
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
        return result
    except ValueError as exc:
        raise PlanError(
            "invalid_scratch_evidence", "observed_at must be an ISO timestamp with timezone"
        ) from exc


def _validate_evidence(value: dict[str, Any], task: Path) -> datetime:
    allowed = {"schema", "task", "observed_at", "liveness", "attempt_state", "source", "details"}
    if set(value) - allowed or type(value.get("schema")) is not int or value["schema"] != 1:
        raise PlanError("invalid_scratch_evidence", "unsupported scratch evidence fields or schema")
    if not isinstance(value.get("task"), str) or not Path(value["task"]).is_absolute():
        raise PlanError("invalid_scratch_evidence", "evidence must identify an absolute task path")
    if Path(value["task"]).resolve() != task.resolve():
        raise PlanError("scratch_evidence_task_mismatch", "evidence describes another task", path=task)
    liveness = value.get("liveness")
    attempt = value.get("attempt_state")
    if not isinstance(liveness, str) or liveness not in {"active", "terminal", "unknown"}:
        raise PlanError("invalid_scratch_evidence", "invalid or missing liveness")
    if not isinstance(attempt, str) or attempt not in {"none", "active", "terminal", "unresolved", "unknown"}:
        raise PlanError("invalid_scratch_evidence", "invalid or missing attempt_state")
    if (liveness == "terminal" and attempt not in {"none", "terminal"}) or (
        liveness == "active" and attempt == "terminal"
    ):
        raise PlanError("conflicting_scratch_evidence", "liveness conflicts with scheduler attempt state")
    source = value.get("source")
    if (
        not isinstance(source, dict)
        or set(source) != {"capability", "identity"}
        or source.get("capability") not in ("site", "simulator")
        or not isinstance(source.get("identity"), str)
        or not source["identity"].strip()
    ):
        raise PlanError("invalid_scratch_evidence", "evidence requires a Site or Simulator source identity")
    if source["capability"] != "site" and attempt != "none":
        raise PlanError("site_evidence_required", "scheduler attempt evidence must come from the Site capability")
    if "details" in value and not isinstance(value["details"], dict):
        raise PlanError("invalid_scratch_evidence", "evidence details must be an object")
    return _evidence_time(value.get("observed_at"))


def record_evidence(
    config: Config, task_arg: str, evidence: dict[str, Any], *, dry_run: bool
) -> Result:
    """Persist caller-supplied specialist evidence without querying a scheduler."""
    # Validate the payload before taking a mutating lock. A malformed request
    # must not create managed state, including the persistent lock file.
    task, _ = _load_task(config, task_arg)
    observed = _validate_evidence(evidence, task)
    evidence = redact_sensitive(evidence)
    lock = nullcontext() if dry_run else managed_root_lock(config.scratch_root, "scratch", target=task)
    with lock:
        task, meta = _load_task(config, task_arg)
        if evidence["source"]["capability"] != "site" and meta.get("attempt_state") != "none":
            raise PlanError("site_evidence_required", "only Site evidence can resolve a scheduler-associated task")
        previous = meta.get("evidence")
        if isinstance(previous, dict):
            previous_time = _evidence_time(previous.get("observed_at"))
            if observed < previous_time or (observed == previous_time and evidence != previous):
                raise PlanError("stale_scratch_evidence", "evidence must not supersede a newer or conflicting observation")
        data = {
            "dry_run": dry_run,
            "task": str(task),
            "liveness": evidence["liveness"],
            "attempt_state": evidence["attempt_state"],
            "observed_at": evidence["observed_at"],
        }
        if not dry_run:
            updated = dict(meta)
            updated.update(
                liveness=evidence["liveness"], attempt_state=evidence["attempt_state"],
                evidence=evidence,
            )
            # A reconciliation query is not new use. Active evidence identifies
            # actual use; terminal/unknown observations and pin changes do not
            # restart retention for an old task.
            if evidence["liveness"] == "active":
                last_used = _evidence_time(meta["last_used_at"])
                if observed > last_used:
                    updated["last_used_at"] = evidence["observed_at"]
            atomic_write_json(task / TASK_META, updated, create_parents=False)
    return Result("scratch record", data)
