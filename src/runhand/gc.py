"""Conservative preview-first garbage collection."""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import Config
from .errors import LocalIOError, PlanError, RunHandError, UnsafeError
from .result import Result
from .scratch import TASK_KINDS, TASK_META, _load_task
from .stage import STAGE_META
from .storage import OWNER_FILE, is_within, managed_root_lock, read_json, require_owned_root, root_is_owned


def parse_duration(value: str | None, default_days: int) -> timedelta:
    if value is None:
        return timedelta(days=default_days)
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    if len(value) < 2 or value[-1].lower() not in units:
        raise PlanError(
            "invalid_duration", "duration must look like 30m, 12h, 14d, or 2w"
        )
    try:
        amount = int(value[:-1])
    except ValueError as exc:
        raise PlanError(
            "invalid_duration", "duration must use an integer amount"
        ) from exc
    if amount < 0:
        raise PlanError("invalid_duration", "duration must not be negative")
    return timedelta(seconds=amount * units[value[-1].lower()])


def _parse_time(value: Any, fallback: float) -> datetime:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.fromtimestamp(fallback, timezone.utc)


def _tree_size(path: Path) -> int:
    total = 0
    try:
        for root, directories, files in os.walk(path, followlinks=False):
            directories[:] = sorted(directories, key=os.fsencode)
            for name in sorted(files, key=os.fsencode):
                candidate = Path(root) / name
                try:
                    total += candidate.lstat().st_size
                except OSError:
                    continue
    except OSError:
        return 0
    return total


def _stage_reason(meta: dict[str, Any], last_used: datetime, cutoff: datetime) -> str | None:
    if meta.get("pinned", False):
        return "pinned"
    if last_used > cutoff:
        return "fresh"
    if meta.get("state") == "ready":
        return "nonterminal"
    if meta.get("state") != "promoted":
        return "state_unknown"
    return None


def _task_reason(meta: dict[str, Any], last_used: datetime, cutoff: datetime) -> str | None:
    if meta.get("pinned", False):
        return "pinned"
    if last_used > cutoff:
        return "fresh"
    if meta.get("liveness") != "terminal":
        return "liveness_unknown" if meta.get("liveness") == "unknown" else "nonterminal"
    # Missing or malformed attempt evidence is not proof of no active writer.
    if meta.get("attempt_state") not in ("none", "terminal"):
        return "attempt_unresolved"
    return None


def _stage_candidates(
    config: Config, cutoff: datetime
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    deletable: list[dict[str, Any]] = []
    protected: list[dict[str, Any]] = []
    root = config.scratch_root / "stages"
    if root.is_symlink() or (
        root.exists() and not is_within(root, config.scratch_root)
    ):
        protected.append(
            {
                "path": str(root),
                "kind": "scratch",
                "reason": "unsafe_container",
            }
        )
        return deletable, protected
    if not root.is_dir():
        return deletable, protected
    for stage in sorted(root.iterdir(), key=lambda path: os.fsencode(path.name)):
        meta_path = stage / STAGE_META
        if not is_within(stage, config.scratch_root):
            protected.append(
                {
                    "path": str(stage),
                    "kind": "scratch",
                    "reason": "root_escape",
                }
            )
            continue
        if not stage.is_dir() or not meta_path.is_file():
            continue
        try:
            if stage.is_symlink() or meta_path.is_symlink():
                raise ValueError("stage entry is a symlink")
            meta = read_json(meta_path)
            if (
                meta.get("schema") != 1
                or meta.get("owner") != "runhand"
                or meta.get("kind") != "stage"
            ):
                raise ValueError("stage metadata is not owned or compatible")
            last_used = _parse_time(meta.get("last_used_at"), stage.stat().st_mtime)
        except Exception as exc:
            protected.append(
                {
                    "path": str(stage),
                    "kind": "scratch",
                    "reason": "metadata_unknown",
                    "detail": str(exc),
                }
            )
            continue
        reason = _stage_reason(meta, last_used, cutoff)
        item = {
            "path": str(stage),
            "kind": "scratch",
            "bytes": _tree_size(stage),
            "last_used_at": last_used.isoformat(),
        }
        if reason:
            item["reason"] = reason
            protected.append(item)
        else:
            deletable.append(item)
    return deletable, protected


def _task_candidates(
    config: Config, cutoff: datetime
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    deletable: list[dict[str, Any]] = []
    protected: list[dict[str, Any]] = []
    root = config.scratch_root / "tasks"
    if root.is_symlink() or (
        root.exists() and not is_within(root, config.scratch_root)
    ):
        protected.append(
            {
                "path": str(root),
                "kind": "scratch",
                "reason": "unsafe_container",
            }
        )
        return deletable, protected
    if not root.is_dir():
        return deletable, protected
    try:
        kind_roots = sorted(root.iterdir(), key=lambda path: os.fsencode(path.name))
    except OSError as exc:
        protected.append(
            {
                "path": str(root),
                "kind": "scratch",
                "reason": "scan_error",
                "detail": str(exc),
            }
        )
        return deletable, protected
    tasks: list[Path] = []
    for kind_root in kind_roots:
        if (
            kind_root.is_symlink()
            or not is_within(kind_root, config.scratch_root)
            or not kind_root.is_dir()
        ):
            protected.append(
                {
                    "path": str(kind_root),
                    "kind": "scratch",
                    "reason": "unsafe_container",
                }
            )
            continue
        try:
            tasks.extend(
                sorted(kind_root.iterdir(), key=lambda path: os.fsencode(path.name))
            )
        except OSError as exc:
            protected.append(
                {
                    "path": str(kind_root),
                    "kind": "scratch",
                    "reason": "scan_error",
                    "detail": str(exc),
                }
            )
    for task in sorted(tasks, key=lambda path: os.fsencode(str(path))):
        meta_path = task / TASK_META
        if not meta_path.is_file():
            continue
        try:
            if task.is_symlink() or meta_path.is_symlink():
                raise ValueError("task entry is a symlink")
            meta = read_json(meta_path)
            expected_kind = task.parent.name
            if (
                meta.get("schema") != 1
                or meta.get("owner") != "runhand"
                or meta.get("kind") != expected_kind
                or expected_kind not in TASK_KINDS
            ):
                raise ValueError("task metadata is not owned or compatible")
            last_used = _parse_time(meta.get("last_used_at"), task.stat().st_mtime)
        except Exception as exc:
            protected.append(
                {
                    "path": str(task),
                    "kind": "scratch",
                    "reason": "metadata_unknown",
                    "detail": str(exc),
                }
            )
            continue
        reason = _task_reason(meta, last_used, cutoff)
        item = {
            "path": str(task),
            "kind": "scratch",
            "bytes": _tree_size(task),
            "last_used_at": last_used.isoformat(),
        }
        if reason:
            item["reason"] = reason
            protected.append(item)
        else:
            deletable.append(item)
    return deletable, protected


def _cache_candidates(
    config: Config, cutoff: datetime
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    deletable: list[dict[str, Any]] = []
    protected: list[dict[str, Any]] = []
    root = config.state_root / "cache"
    if root.is_symlink() or (
        root.exists() and not is_within(root, config.state_root)
    ):
        protected.append(
            {"path": str(root), "kind": "cache", "reason": "unsafe_container"}
        )
        return deletable, protected
    if not root.is_dir():
        return deletable, protected
    for path in sorted(root.iterdir(), key=lambda value: os.fsencode(value.name)):
        if path.name == OWNER_FILE:
            continue
        if not is_within(path, config.state_root):
            protected.append(
                {"path": str(path), "kind": "cache", "reason": "root_escape"}
            )
            continue
        try:
            modified = datetime.fromtimestamp(path.lstat().st_mtime, timezone.utc)
        except OSError as exc:
            protected.append(
                {"path": str(path), "kind": "cache", "reason": "stat_error", "detail": str(exc)}
            )
            continue
        entry = {
            "path": str(path), "kind": "cache",
            "bytes": _tree_size(path) if path.is_dir() else path.lstat().st_size,
        }
        try:
            if path.is_symlink() or not path.is_file():
                raise ValueError("cache entry is not a regular file")
            record = read_json(path)
        except Exception as exc:
            entry.update(reason="cache_unknown", detail=str(exc))
            protected.append(entry)
            continue
        if (record.get("schema"), record.get("owner"), record.get("kind")) != (1, "runhand", "cache"):
            entry["reason"] = "cache_incompatible"
            protected.append(entry)
        elif modified <= cutoff:
            deletable.append(entry)
        else:
            entry["reason"] = "fresh"
            protected.append(entry)
    return deletable, protected


def _live_candidate_reason(
    config: Config, path: Path, kind: str, cutoff: datetime
) -> str | None:
    """Recheck one selected object under its mutation lock, without a tree scan."""
    root = config.scratch_root if kind == "scratch" else config.state_root
    try:
        relative = path.relative_to(root)
        if not relative.parts or not is_within(path, root):
            return "root_escape"
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return "unsafe_container"
        if not path.exists():
            return "disappeared"
        if kind == "scratch":
            if path.parent == root / "stages":
                meta_path = path / STAGE_META
                if meta_path.is_symlink() or not path.is_dir():
                    return "metadata_unknown"
                meta = read_json(meta_path)
                if (meta.get("schema"), meta.get("owner"), meta.get("kind")) != (1, "runhand", "stage"):
                    return "metadata_unknown"
                last_used = _parse_time(meta.get("last_used_at"), path.stat().st_mtime)
                return _stage_reason(meta, last_used, cutoff)
            _, meta = _load_task(config, str(path))
            last_used = _parse_time(meta.get("last_used_at"), path.stat().st_mtime)
            return _task_reason(meta, last_used, cutoff)
        if path.parent != root / kind or not path.is_file():
            return f"{kind}_incompatible"
        record = read_json(path)
        if (record.get("schema"), record.get("owner"), record.get("kind")) != (1, "runhand", "cache"):
            return "cache_incompatible"
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        return "fresh" if modified > cutoff else None
    except (OSError, RunHandError, ValueError):
        return "metadata_unknown"


def collect(
    config: Config, *, kind: str, older_than: str | None, apply: bool
) -> Result:
    if kind not in {"scratch", "cache", "all"}:
        raise PlanError("invalid_gc_kind", "unsupported GC kind")
    now = datetime.now(timezone.utc)
    scratch_cutoff = now - parse_duration(older_than, config.scratch_ttl_days)
    cache_cutoff = now - parse_duration(older_than, config.scratch_ttl_days)
    candidates: list[dict[str, Any]] = []
    protected: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if kind in {"scratch", "all"}:
        if config.scratch_root.exists() and not root_is_owned(
            config.scratch_root, "scratch"
        ):
            warnings.append(
                {
                    "code": "unowned_scratch_skipped",
                    "message": "configured scratch root is not marked as RunHand-owned and was skipped",
                    "retryable": False,
                    "path": str(config.scratch_root),
                }
            )
        elif root_is_owned(config.scratch_root, "scratch"):
            for provider in (_stage_candidates, _task_candidates):
                found, kept = provider(config, scratch_cutoff)
                candidates.extend(found)
                protected.extend(kept)
    if kind in {"cache", "all"}:
        if config.state_root.exists() and not root_is_owned(config.state_root, "state"):
            warnings.append(
                {
                    "code": "unowned_state_skipped",
                    "message": "configured state root is not marked as RunHand-owned and was skipped",
                    "retryable": False,
                    "path": str(config.state_root),
                }
            )
        elif root_is_owned(config.state_root, "state"):
            found, kept = _cache_candidates(config, cache_cutoff)
            candidates.extend(found)
            protected.extend(kept)
    candidates.sort(key=lambda item: os.fsencode(item["path"]))
    protected.sort(key=lambda item: os.fsencode(item["path"]))
    deleted: list[str] = []
    if apply:
        live_candidates = []
        for item in candidates:
            path = Path(item["path"])
            item_kind = item["kind"]
            root, owner_kind = (
                (config.scratch_root, "scratch") if item_kind == "scratch" else (config.state_root, "state")
            )
            cutoff = scratch_cutoff if item_kind == "scratch" else cache_cutoff
            try:
                with managed_root_lock(root, owner_kind, target=path):
                    reason = _live_candidate_reason(config, path, item_kind, cutoff)
                    if reason is None:
                        _remove_owned_candidate(config, path, item_kind)
                        deleted.append(str(path))
                        live_candidates.append(item)
            except LocalIOError as exc:
                if exc.code != "managed_root_busy":
                    raise
                reason = "busy"
            if reason is not None:
                protected.append({**item, "reason": reason})
        candidates = live_candidates
        protected.sort(key=lambda item: os.fsencode(item["path"]))
    return Result(
        "gc",
        {
            "apply": apply,
            "cutoffs": {
                "scratch": scratch_cutoff.isoformat(),
                "cache": cache_cutoff.isoformat(),
            },
            "candidates": candidates,
            "protected": protected,
            "estimated_bytes": sum(item.get("bytes", 0) for item in candidates),
            "deleted": deleted,
        },
        warnings,
    )


def _remove_owned_candidate(config: Config, path: Path, kind: str) -> None:
    roots = [config.scratch_root] if kind == "scratch" else [config.state_root]
    if not any(is_within(path, root) and path != root for root in roots):
        raise UnsafeError(
            "gc_target_outside_owned_root",
            "GC candidate escaped its owned root",
            path=path,
        )
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError as exc:
        raise LocalIOError(
            "gc_delete_failed", f"cannot delete GC candidate: {exc}", path=path
        ) from exc


def collect_orphan(config: Config, *, task_arg: str, apply: bool) -> Result:
    # Read-only validation precedes lock creation; the complete check repeats
    # under the same stable object lock used by pin, record, and bulk GC.
    preview = _collect_orphan(config, task_arg=task_arg, apply=False)
    if not apply:
        return preview
    candidate = Path(preview.data["orphan"])
    with managed_root_lock(config.scratch_root, "scratch", target=candidate):
        return _collect_orphan(
            config, task_arg=str(candidate), apply=True, size_bytes=preview.data["bytes"]
        )


def _collect_orphan(
    config: Config, *, task_arg: str, apply: bool, size_bytes: int | None = None
) -> Result:
    require_owned_root(config.scratch_root, "scratch")
    candidate = Path(task_arg).expanduser().resolve()
    if (
        not is_within(candidate, config.scratch_root)
        or candidate == config.scratch_root
    ):
        raise UnsafeError(
            "task_outside_scratch",
            "orphan is outside the configured scratch root",
            path=candidate,
        )
    if (candidate / TASK_META).is_file():
        task, meta = _load_task(config, task_arg)
    elif (candidate / STAGE_META).is_file():
        task = candidate
        if (candidate / STAGE_META).is_symlink():
            raise PlanError("invalid_stage_metadata", "orphan stage metadata is a symlink", path=candidate)
        meta = read_json(candidate / STAGE_META)
        if (
            meta.get("schema") != 1
            or meta.get("owner") != "runhand"
            or meta.get("kind") != "stage"
        ):
            raise PlanError(
                "invalid_stage_metadata",
                "orphan stage metadata is incompatible",
                path=candidate / STAGE_META,
            )
    else:
        raise PlanError(
            "invalid_orphan",
            "orphan must be an exact RunHand task or stage",
            path=candidate,
        )
    if meta.get("pinned", False):
        raise UnsafeError(
            "pinned_orphan",
            "pinned task must be unpinned before orphan deletion",
            path=task,
        )
    if meta.get("liveness") == "active" or meta.get("attempt_state") == "active":
        raise UnsafeError(
            "active_orphan", "active evidence must be reconciled before orphan deletion", path=task
        )
    data = {
        "apply": apply,
        "orphan": str(task),
        "bytes": _tree_size(task) if size_bytes is None else size_bytes,
        "requires_site_recheck": True,
        "deleted": False,
    }
    if apply:
        _remove_owned_candidate(config, task, "scratch")
        data["deleted"] = True
    return Result("gc", data)
