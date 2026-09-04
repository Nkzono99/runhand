"""Conservative preview-first garbage collection."""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import Config
from .errors import LocalIOError, PlanError, UnsafeError
from .result import Result
from .scratch import TASK_META, _load_task
from .stage import STAGE_META
from .storage import OWNER_FILE, is_within, read_json, require_owned_root, root_is_owned


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
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
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


def _stage_candidates(
    config: Config, cutoff: datetime
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    deletable: list[dict[str, Any]] = []
    protected: list[dict[str, Any]] = []
    root = config.scratch_root / "stages"
    if not root.is_dir():
        return deletable, protected
    for stage in sorted(root.iterdir(), key=lambda path: os.fsencode(path.name)):
        meta_path = stage / STAGE_META
        if not stage.is_dir() or not meta_path.is_file():
            continue
        try:
            meta = read_json(meta_path)
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
        reason = None
        if meta.get("pinned", False):
            reason = "pinned"
        elif last_used > cutoff:
            reason = "fresh"
        elif meta.get("state") == "ready":
            reason = "nonterminal"
        elif meta.get("state") != "promoted":
            reason = "state_unknown"
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
    if not root.is_dir():
        return deletable, protected
    for meta_path in sorted(
        root.glob(f"*/*/{TASK_META}"), key=lambda path: os.fsencode(str(path))
    ):
        task = meta_path.parent
        try:
            meta = read_json(meta_path)
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
        reason = None
        if meta.get("pinned", False):
            reason = "pinned"
        elif last_used > cutoff:
            reason = "fresh"
        elif meta.get("liveness") != "terminal":
            reason = (
                "liveness_unknown"
                if meta.get("liveness") == "unknown"
                else "nonterminal"
            )
        elif meta.get("attempt_state") in {"active", "unresolved", "unknown"}:
            reason = "attempt_unresolved"
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


def _state_candidates(
    config: Config, kind: str, cutoff: datetime
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    deletable: list[dict[str, Any]] = []
    protected: list[dict[str, Any]] = []
    requested = {kind} if kind in {"cache", "history"} else {"cache", "history"}
    for item_kind in sorted(requested):
        root = config.state_root / item_kind
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir(), key=lambda value: os.fsencode(value.name)):
            if path.name == OWNER_FILE:
                continue
            try:
                modified = datetime.fromtimestamp(path.lstat().st_mtime, timezone.utc)
            except OSError as exc:
                protected.append(
                    {
                        "path": str(path),
                        "kind": item_kind,
                        "reason": "stat_error",
                        "detail": str(exc),
                    }
                )
                continue
            entry = {
                "path": str(path),
                "kind": item_kind,
                "bytes": _tree_size(path) if path.is_dir() else path.lstat().st_size,
            }
            if item_kind == "history":
                try:
                    record = read_json(path)
                except Exception as exc:
                    entry["reason"] = "history_unknown"
                    entry["detail"] = str(exc)
                    protected.append(entry)
                    continue
                if record.get("reconciled") is not True:
                    entry["reason"] = "history_unreconciled"
                    protected.append(entry)
                    continue
            if modified <= cutoff:
                deletable.append(entry)
            else:
                entry["reason"] = "fresh"
                protected.append(entry)
    return deletable, protected


def collect(
    config: Config, *, kind: str, older_than: str | None, apply: bool
) -> Result:
    if kind not in {"scratch", "cache", "history", "all"}:
        raise PlanError("invalid_gc_kind", "unsupported GC kind")
    now = datetime.now(timezone.utc)
    scratch_cutoff = now - parse_duration(older_than, config.scratch_ttl_days)
    cache_cutoff = now - parse_duration(older_than, config.scratch_ttl_days)
    history_cutoff = now - parse_duration(older_than, config.history_ttl_days)
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
    if kind in {"cache", "history", "all"}:
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
            requested = ("cache", "history") if kind == "all" else (kind,)
            for state_kind in requested:
                state_cutoff = cache_cutoff if state_kind == "cache" else history_cutoff
                found, kept = _state_candidates(config, state_kind, state_cutoff)
                candidates.extend(found)
                protected.extend(kept)
    candidates.sort(key=lambda item: os.fsencode(item["path"]))
    protected.sort(key=lambda item: os.fsencode(item["path"]))
    deleted: list[str] = []
    if apply:
        for item in candidates:
            path = Path(item["path"])
            _remove_owned_candidate(config, path, item["kind"])
            deleted.append(str(path))
    return Result(
        "gc",
        {
            "apply": apply,
            "cutoffs": {
                "scratch": scratch_cutoff.isoformat(),
                "cache": cache_cutoff.isoformat(),
                "history": history_cutoff.isoformat(),
            },
            "candidates": candidates,
            "protected": protected,
            "estimated_bytes": sum(item.get("bytes", 0) for item in candidates),
            "deleted": deleted,
        },
        warnings,
    )


def _remove_owned_candidate(config: Config, path: Path, kind: str) -> None:
    roots = [config.scratch_root] if kind == "scratch" else [config.state_root / kind]
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
    data = {
        "apply": apply,
        "orphan": str(task),
        "bytes": _tree_size(task),
        "requires_site_recheck": True,
        "deleted": False,
    }
    if apply:
        _remove_owned_candidate(config, task, "scratch")
        data["deleted"] = True
    return Result("gc", data)
