"""Scratch stage creation, inspection, and atomic promotion."""

from __future__ import annotations

import ctypes
import errno
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from .config import Config
from .copying import CopyPlan, Entry, copy_entries, select_entries
from .errors import LocalIOError, PlanError, UnsafeError
from .result import Result, warning
from .storage import atomic_write_json, ensure_owned_root, read_json, utc_now

STAGE_META = "stage.json"
STAGE_TREE = "tree"


def _stage_id() -> str:
    return f"stage-{uuid.uuid4().hex}"


def _entry_dict(entry: Entry) -> dict[str, Any]:
    value: dict[str, Any] = {
        "path": entry.relative,
        "kind": entry.kind,
        "size": entry.size,
    }
    if entry.link_target is not None:
        value["link_target"] = entry.link_target
    return value


def _selection(entries: list[Entry]) -> list[Entry]:
    return [entry for entry in entries if entry.kind != "guard"]


def create_stage(config: Config, plan: CopyPlan, *, dry_run: bool) -> Result:
    entries = select_entries(plan)
    selection = _selection(entries)
    summary = {
        "directories": sum(entry.kind == "directory" for entry in selection),
        "files": sum(entry.kind == "file" for entry in selection),
        "symlinks": sum(entry.kind == "symlink" for entry in selection),
        "bytes": sum(entry.size for entry in selection),
    }
    if dry_run:
        return Result(
            "stage create",
            {
                "dry_run": True,
                "would_create_under": str(config.scratch_root / "stages"),
                "copy_plan": plan.as_dict(),
                "selection": [_entry_dict(entry) for entry in selection],
                "summary": summary,
            },
        )

    ensure_owned_root(config.scratch_root, "scratch")
    stages_root = config.scratch_root / "stages"
    stages_root.mkdir(parents=True, exist_ok=True)
    stage = stages_root / _stage_id()
    temporary = stages_root / f".{stage.name}.tmp-{uuid.uuid4().hex}"
    created = utc_now()
    try:
        temporary.mkdir(exist_ok=False)
        copy_entries(plan, entries, temporary / STAGE_TREE)
        atomic_write_json(
            temporary / STAGE_META,
            {
                "schema": 1,
                "owner": "runhand",
                "kind": "stage",
                "state": "ready",
                "created_at": created,
                "last_used_at": created,
                "pinned": False,
                "copy_plan": plan.as_dict(),
                "selection": [_entry_dict(entry) for entry in selection],
                "warnings": [],
            },
        )
        _rename_noreplace(temporary, stage)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return Result(
        "stage create",
        {
            "dry_run": False,
            "stage": str(stage),
            "tree": str(stage / STAGE_TREE),
            "state": "ready",
            "copy_plan": plan.as_dict(),
            "selection": [_entry_dict(entry) for entry in selection],
            "summary": summary,
        },
    )


def _load_stage(stage_arg: str) -> tuple[Path, dict[str, Any]]:
    stage = Path(stage_arg).expanduser().resolve()
    meta_path = stage / STAGE_META
    tree = stage / STAGE_TREE
    if not stage.is_dir() or not meta_path.is_file():
        raise PlanError(
            "invalid_stage", "stage must be a RunHand stage directory", path=stage
        )
    meta = read_json(meta_path)
    if (
        meta.get("schema") != 1
        or meta.get("owner") != "runhand"
        or meta.get("kind") != "stage"
    ):
        raise PlanError(
            "invalid_stage_metadata", "stage metadata is incompatible", path=meta_path
        )
    if tree.is_symlink() or not tree.is_dir():
        raise PlanError("incomplete_stage", "stage tree is missing", path=tree)
    return stage, meta


def inspect_stage(stage_arg: str) -> Result:
    stage, meta = _load_stage(stage_arg)
    warnings: list[dict[str, Any]] = list(meta.get("warnings", []))
    if meta.get("state") not in {"ready", "promoted"}:
        warnings.append(
            warning(
                "unknown_stage_state", "stage state is not recognized", path=str(stage)
            )
        )
    return Result(
        "stage inspect",
        {
            "stage": str(stage),
            "tree": str(stage / STAGE_TREE),
            "state": meta.get("state", "unknown"),
            "created_at": meta.get("created_at"),
            "last_used_at": meta.get("last_used_at"),
            "copy_plan": meta.get("copy_plan"),
            "selection": meta.get("selection", []),
            "promotions": meta.get("promotions", []),
        },
        warnings,
    )


def _scan_tree_as_plan(tree: Path) -> tuple[CopyPlan, list[Entry]]:
    plan = CopyPlan(
        source=tree,
        include=("**",),
        exclude=(),
        symlink_policy="internal-relative",
        completeness="complete",
        basis={"kind": "stage"},
    )
    return plan, select_entries(plan)


def promote_stage(stage_arg: str, target_arg: str, *, dry_run: bool) -> Result:
    stage, meta = _load_stage(stage_arg)
    tree = stage / STAGE_TREE
    target_input = Path(target_arg).expanduser()
    if not target_input.is_absolute():
        target_input = Path.cwd() / target_input
    # Resolve only the parent. Resolving the final component would follow a
    # dangling symlink and make an existing directory entry appear absent.
    parent = target_input.parent.resolve(strict=False)
    target = parent / target_input.name
    if target.exists() or target.is_symlink():
        raise UnsafeError(
            "target_exists",
            "formal target already exists; no replacement was attempted",
            path=target,
        )
    if not parent.is_dir():
        raise PlanError(
            "target_parent_missing",
            "formal target parent must already exist",
            path=parent,
        )
    plan, entries = _scan_tree_as_plan(tree)
    selection = _selection(entries)
    summary = {
        "directories": sum(entry.kind == "directory" for entry in selection),
        "files": sum(entry.kind == "file" for entry in selection),
        "symlinks": sum(entry.kind == "symlink" for entry in selection),
        "bytes": sum(entry.size for entry in selection),
    }
    if dry_run:
        return Result(
            "promote",
            {
                "dry_run": True,
                "stage": str(stage),
                "target": str(target),
                "temporary_parent": str(parent),
                "summary": summary,
            },
        )

    temporary = parent / f".runhand-promote-{target.name}-{uuid.uuid4().hex}"
    try:
        copy_entries(plan, entries, temporary)
        _rename_noreplace(temporary, target)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

    warnings: list[dict[str, Any]] = []
    promoted_at = utc_now()
    try:
        updated = dict(meta)
        updated["state"] = "promoted"
        updated["last_used_at"] = promoted_at
        promotions = list(updated.get("promotions", []))
        promotions.append({"target": str(target), "promoted_at": promoted_at})
        updated["promotions"] = promotions
        atomic_write_json(stage / STAGE_META, updated)
    except Exception as exc:
        warnings.append(
            warning(
                "stage_state_update_failed",
                "promotion succeeded, but stage metadata could not be updated",
                path=str(stage / STAGE_META),
                details={"reason": str(exc)},
            )
        )
    return Result(
        "promote",
        {
            "dry_run": False,
            "stage": str(stage),
            "target": str(target),
            "published": True,
            "summary": summary,
        },
        warnings,
    )


def _rename_noreplace(source: Path, target: Path) -> None:
    """Atomically rename on Linux without replacing any existing path."""
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise LocalIOError(
            "atomic_noreplace_unavailable",
            "this Linux runtime does not expose renameat2(RENAME_NOREPLACE)",
            path=target,
        )
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(-100, os.fsencode(source), -100, os.fsencode(target), 1)
    if result == 0:
        return
    error = ctypes.get_errno()
    if error in {errno.EEXIST, errno.ENOTEMPTY}:
        raise UnsafeError(
            "target_exists", "target appeared during atomic promotion", path=target
        )
    raise LocalIOError(
        "atomic_publish_failed",
        f"atomic no-replace publish failed: {os.strerror(error)}",
        path=target,
        retryable=error in {errno.EINTR, errno.EBUSY},
    )
