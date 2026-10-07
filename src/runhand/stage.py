"""Mutable scratch stages and atomic publication of formal Runs."""

from __future__ import annotations

import errno
import os
import shutil
import tempfile
import uuid
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from .config import Config
from .copying import CopyPlan, Entry, copy_entries, select_entries
from .errors import LocalIOError, PlanError, RunHandError, UnsafeError
from .probes import probe_storage
from .publication import PUBLISH_MODES, rename_noreplace, symlink_noreplace
from .result import Result, warning
from .storage import (
    atomic_write_json,
    ensure_owned_subdir,
    is_within,
    managed_root_lock,
    read_json,
    root_is_owned,
    utc_now,
)

STAGE_META = "stage.json"
STAGE_TREE = "tree"
PUBLICATION_META = "publication.json"


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
    if is_within(config.scratch_root, plan.source) or is_within(
        config.scratch_root / "stages", plan.source
    ):
        raise UnsafeError(
            "scratch_overlaps_source",
            "scratch root and stage container must be outside the copy source",
            path=config.scratch_root,
            details={"source": str(plan.source)},
        )
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

    stages_root = ensure_owned_subdir(config.scratch_root, "scratch", "stages")
    stage = stages_root / _stage_id()
    created = utc_now()
    metadata = {
        "schema": 1,
        "owner": "runhand",
        "kind": "stage",
        "state": "incomplete",
        "created_at": created,
        "last_used_at": created,
        "pinned": False,
        "copy_plan": plan.as_dict(),
        "selection": [_entry_dict(entry) for entry in selection],
        "warnings": [],
    }
    # This private mutable directory needs exclusive allocation, not formal
    # publication. Share its lock with promotion and exact orphan cleanup.
    with managed_root_lock(config.scratch_root, "scratch", target=stage):
        try:
            stage.mkdir(exist_ok=False)
        except FileExistsError as exc:
            raise UnsafeError(
                "stage_exists", "unique stage path already exists", path=stage
            ) from exc
        except OSError as exc:
            raise LocalIOError(
                "stage_create_failed", f"cannot allocate stage: {exc}", path=stage
            ) from exc
        # Cleanup is limited to the directory this call successfully claimed.
        try:
            atomic_write_json(stage / STAGE_META, metadata, create_parents=False)
            copy_entries(plan, entries, stage / STAGE_TREE)
            metadata.update(state="ready", last_used_at=utc_now())
            atomic_write_json(stage / STAGE_META, metadata, create_parents=False)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
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
    missing_incomplete_tree = meta.get("state") == "incomplete" and not tree.exists()
    if tree.is_symlink() or (not tree.is_dir() and not missing_incomplete_tree):
        raise PlanError("incomplete_stage", "stage tree is missing", path=tree)
    return stage, meta


def inspect_stage(stage_arg: str) -> Result:
    stage, meta = _load_stage(stage_arg)
    warnings: list[dict[str, Any]] = list(meta.get("warnings", []))
    if meta.get("state") == "incomplete":
        warnings.append(
            warning("incomplete_stage", "stage copy has not finished", path=str(stage))
        )
    elif meta.get("state") not in {"ready", "promoted"}:
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


def promote_stage(
    stage_arg: str, target_arg: str, *, dry_run: bool, publish_mode: str = "atomic"
) -> Result:
    if publish_mode not in PUBLISH_MODES:
        raise PlanError("invalid_publish_mode", "unknown publication mode")
    stage_path = Path(stage_arg).expanduser().resolve()
    lock = nullcontext()
    if not dry_run:
        for ancestor in stage_path.parents:
            if root_is_owned(ancestor, "scratch"):
                lock = managed_root_lock(ancestor, "scratch", target=stage_path)
                break
    # Standalone stages have no RunHand-owned scratch ancestor and cannot be
    # collected by GC. Managed stages must remain alive until publication and
    # their metadata update both finish, including a second promotion.
    with lock:
        return _promote_stage(stage_arg, target_arg, dry_run=dry_run, publish_mode=publish_mode)


def _promote_stage(
    stage_arg: str, target_arg: str, *, dry_run: bool, publish_mode: str
) -> Result:
    stage, meta = _load_stage(stage_arg)
    if meta.get("state") not in {"ready", "promoted"}:
        raise PlanError(
            "stage_not_ready",
            "stage copy must finish before publication",
            path=stage,
            details={"state": meta.get("state", "unknown")},
        )
    tree = stage / STAGE_TREE
    target = resolve_target_path(target_arg)
    parent = target.parent
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
    if publish_mode == "symlink" and (
        is_within(parent, stage)
        or any(root_is_owned(ancestor, "scratch") for ancestor in (parent, *parent.parents))
    ):
        raise UnsafeError(
            "publication_in_scratch",
            "durable symlink publication must be outside stages and managed scratch",
            path=target,
        )
    if not dry_run:
        # Diagnose the actual destination before scanning/copying a large tree.
        # The final primitive still refuses collisions; this probe is not a lock.
        check = probe_storage(str(parent), publish_mode=publish_mode)
        if not check["supported"]:
            raise LocalIOError(
                "publication_unavailable",
                f"{publish_mode} no-replace publication is unavailable on the destination filesystem",
                path=parent,
                details={"check": check},
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
                "publish_mode": publish_mode,
                "summary": summary,
            },
        )

    warnings: list[dict[str, Any]] = []
    backing: Path | None = None
    if publish_mode == "symlink":
        backing, publication_warnings = _publish_symlink(plan, entries, stage, target)
        warnings.extend(publication_warnings)
    else:
        temporary = parent / f".runhand-promote-{target.name}-{uuid.uuid4().hex}"
        try:
            copy_entries(plan, entries, temporary)
            _rename_noreplace(temporary, target)
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    promoted_at = utc_now()
    try:
        updated = dict(meta)
        updated["state"] = "promoted"
        updated["last_used_at"] = promoted_at
        promotions = list(updated.get("promotions", []))
        promotion = {"target": str(target), "promoted_at": promoted_at, "publish_mode": publish_mode}
        if backing is not None:
            promotion["backing_path"] = str(backing)
        promotions.append(promotion)
        updated["promotions"] = promotions
        atomic_write_json(stage / STAGE_META, updated, create_parents=False)
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
            "publish_mode": publish_mode,
            **({"backing_path": str(backing)} if backing is not None else {}),
            "summary": summary,
        },
        warnings,
    )


def _cleanup_unpublished_backing(container: Path, backing: Path, target: Path) -> bool:
    """Remove only our unreachable copy, preserving it after an uncertain publish.

    A signal can arrive after symlink() succeeds but before Python returns.
    Never remove a backing tree referenced by the formal name, or when the
    current link cannot be read reliably. Interrupted copies retain their
    publication record if cleanup itself fails.
    """
    try:
        if os.readlink(target) == os.path.relpath(backing, target.parent):
            return False
    except OSError as exc:
        if exc.errno not in {errno.ENOENT, errno.EINVAL}:
            return False
    try:
        shutil.rmtree(container)
    except OSError:
        return False
    return True


def _publish_symlink(
    plan: CopyPlan, entries: list[Entry], stage: Path, target: Path
) -> tuple[Path, list[dict[str, Any]]]:
    try:
        container = Path(tempfile.mkdtemp(prefix=".runhand-run-", dir=target.parent))
    except OSError as exc:
        raise LocalIOError(
            "publication_create_failed", f"cannot allocate durable Run copy: {exc}", path=target.parent
        ) from exc
    backing = container / "tree"
    record = container / PUBLICATION_META
    metadata = {
        "schema": 1, "owner": "runhand", "kind": "publication",
        "publish_mode": "symlink", "state": "incomplete",
        "stage": str(stage), "target": str(target), "backing_path": str(backing),
        "created_at": utc_now(),
    }
    try:
        atomic_write_json(record, metadata, create_parents=False)
        copy_entries(plan, entries, backing)
        # Construction is private. Once complete, preserve the Run tree's
        # group/other access through its extra parent while retaining owner
        # access for recovery-record updates.
        os.chmod(container, 0o700 | (backing.stat().st_mode & 0o077))
        metadata["state"] = "ready"
        atomic_write_json(record, metadata, create_parents=False)
        try:
            symlink_noreplace(backing, target)
        except FileExistsError as exc:
            raise UnsafeError("target_exists", "target appeared during symlink publication", path=target) from exc
        except OSError as exc:
            raise LocalIOError(
                "symlink_publish_failed", f"no-replace symlink publication failed: {exc}",
                path=target, details={"errno": exc.errno},
            ) from exc
    except BaseException as exc:
        cleaned = _cleanup_unpublished_backing(container, backing, target)
        if isinstance(exc, KeyboardInterrupt):
            exc = RunHandError("interrupted", "publication interrupted", 1, retryable=True)
        elif isinstance(exc, OSError):
            exc = LocalIOError(
                "publication_prepare_failed", f"cannot prepare durable Run copy: {exc}",
                path=backing, details={"errno": exc.errno},
            )
        if isinstance(exc, RunHandError):
            if not cleaned:
                exc.details.update(retained_backing=str(backing), publication_record=str(record), target=str(target))
            raise exc
        raise

    warnings = []
    try:
        metadata.update(state="published", published_at=utc_now())
        atomic_write_json(record, metadata, create_parents=False)
    except Exception as exc:
        warnings.append(warning(
            "publication_state_update_failed",
            "publication succeeded, but its recovery record could not be updated",
            path=str(record), details={"reason": str(exc)},
        ))
    return backing, warnings


def resolve_target_path(target_arg: str) -> Path:
    """Resolve a publication parent, preserving the leaf and symlink/.. semantics."""
    target = Path(target_arg).expanduser()
    if not target.is_absolute():
        target = Path.cwd() / target
    # Resolving the leaf would follow an existing (even dangling) symlink.
    # abspath/normpath would instead erase .. before resolving parent symlinks.
    return target.parent.resolve(strict=False) / target.name


def _rename_noreplace(source: Path, target: Path) -> None:
    """Atomically rename on Linux without replacing any existing path."""
    try:
        rename_noreplace(source, target)
    except OSError as exc:
        if exc.errno in {errno.EEXIST, errno.ENOTEMPTY}:
            raise UnsafeError(
                "target_exists", "target appeared during atomic promotion", path=target
            ) from exc
        raise LocalIOError(
            "atomic_noreplace_unavailable" if exc.errno == errno.ENOSYS else "atomic_publish_failed",
            f"atomic no-replace publish failed: {exc}", path=target,
            retryable=exc.errno in {errno.EINTR, errno.EBUSY}, details={"errno": exc.errno},
        ) from exc
