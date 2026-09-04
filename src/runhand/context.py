"""Bounded, non-executing workspace discovery."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
from typing import Any

from .config import Config
from .result import Result, warning
from .storage import atomic_write_json, ensure_owned_root, read_json, root_is_owned

_README_NAMES = {"README", "README.md", "README.rst", "README.txt"}
_CONTROL_SUFFIXES = {".toml", ".yaml", ".yml", ".json", ".inp", ".in", ".cfg", ".ini"}
_PRUNE_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "__pycache__",
    "node_modules",
    ".runhand",
}


def _cache_path(config: Config) -> Path:
    digest = hashlib.sha256(os.fsencode(str(config.workspace))).hexdigest()[:20]
    return config.state_root / "cache" / f"context-{digest}.json"


def _root_signature(root: Path, max_depth: int, max_entries: int) -> dict[str, Any]:
    info = root.stat()
    return {
        "device": info.st_dev,
        "inode": info.st_ino,
        "mtime_ns": info.st_mtime_ns,
        "max_depth": max_depth,
        "max_entries": max_entries,
    }


def _load_cache(config: Config) -> dict[str, Any] | None:
    path = _cache_path(config)
    if not root_is_owned(config.state_root, "state"):
        return None
    if not path.is_file():
        return None
    try:
        cached = read_json(path)
        if cached.get("root_signature") != _root_signature(
            config.workspace, config.context_max_depth, config.context_max_entries
        ):
            return None
        data = cached.get("data")
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_cache(config: Config, data: dict[str, Any]) -> dict[str, Any] | None:
    path = _cache_path(config)
    try:
        ensure_owned_root(config.state_root, "state")
        atomic_write_json(
            path,
            {
                "schema": 1,
                "root_signature": _root_signature(
                    config.workspace,
                    config.context_max_depth,
                    config.context_max_entries,
                ),
                "data": data,
            },
        )
    except Exception as exc:
        return warning(
            "context_cache_write_failed",
            "live scan succeeded, but its candidate cache could not be written",
            path=str(path),
            details={"reason": str(exc)},
        )
    return None


def scan_context(config: Config, *, refresh: bool) -> Result:
    warnings: list[dict[str, Any]] = []
    if config.warm_cache and not refresh:
        cached = _load_cache(config)
        if cached is not None:
            cached = dict(cached)
            cached["cache"] = {"status": "hit", "path": str(_cache_path(config))}
            return Result("context", cached, warnings)

    candidates: dict[str, dict[str, Any]] = {}
    readmes: list[str] = []
    control_files: list[str] = []
    prune_hints: list[str] = []
    partial_reasons: list[dict[str, Any]] = []
    visited = 0
    budget_exhausted = False

    def relative(path: Path) -> str:
        value = path.relative_to(config.workspace).as_posix()
        return value or "."

    def add_candidate(directory: Path, reason: str, evidence: str) -> None:
        rel = relative(directory)
        item = candidates.setdefault(
            rel,
            {
                "path": str(directory),
                "reason": [],
                "evidence": [],
                "classification": "structural_candidate",
            },
        )
        if reason not in item["reason"]:
            item["reason"].append(reason)
        if evidence not in item["evidence"]:
            item["evidence"].append(evidence)

    def walk(directory: Path, depth: int) -> None:
        nonlocal visited, budget_exhausted
        if budget_exhausted:
            return
        if depth > config.context_max_depth:
            partial_reasons.append(
                {
                    "code": "max_depth",
                    "path": str(directory),
                    "message": "context depth limit reached",
                }
            )
            return
        try:
            children = sorted(
                os.scandir(directory), key=lambda item: os.fsencode(item.name)
            )
        except OSError as exc:
            partial_reasons.append(
                {
                    "code": "scan_error",
                    "path": str(directory),
                    "message": str(exc),
                }
            )
            return
        for child in children:
            if visited >= config.context_max_entries:
                budget_exhausted = True
                partial_reasons.append(
                    {
                        "code": "max_entries",
                        "path": str(directory),
                        "message": "context entry limit reached",
                    }
                )
                return
            visited += 1
            path = Path(child.path)
            rel = relative(path)
            try:
                is_dir = child.is_dir(follow_symlinks=False)
                is_file = child.is_file(follow_symlinks=False)
            except OSError as exc:
                partial_reasons.append(
                    {"code": "stat_error", "path": str(path), "message": str(exc)}
                )
                continue
            if is_dir:
                if child.name in _PRUNE_NAMES:
                    prune_hints.append(rel)
                    continue
                walk(path, depth + 1)
                continue
            if not is_file:
                continue
            if child.name in _README_NAMES:
                readmes.append(rel)
            if path.suffix.lower() in _CONTROL_SUFFIXES:
                control_files.append(rel)
                add_candidate(path.parent, "contains_control_file", rel)
            if child.name.startswith(("job", "submit")) and path.suffix.lower() in {
                ".sh",
                ".bash",
            }:
                add_candidate(path.parent, "contains_job_script", rel)

    walk(config.workspace, 0)
    candidate_values = []
    for key in sorted(candidates, key=lambda item: os.fsencode(item)):
        value = candidates[key]
        value["reason"].sort()
        value["evidence"].sort(key=os.fsencode)
        candidate_values.append(value)

    data: dict[str, Any] = {
        "workspace": str(config.workspace),
        "partial": bool(partial_reasons),
        "partial_reasons": sorted(
            partial_reasons,
            key=lambda item: (item["code"], os.fsencode(item["path"])),
        ),
        "candidate_runs": candidate_values,
        "readmes": sorted(readmes, key=os.fsencode),
        "control_files": sorted(control_files, key=os.fsencode),
        "prune_hints": sorted(prune_hints, key=os.fsencode),
        "naming_patterns": _naming_patterns(candidates),
        "scratch": {
            "root": str(config.scratch_root),
            "exists": config.scratch_root.exists(),
        },
        "cache": {"status": "miss" if config.warm_cache else "disabled"},
        "scan": {
            "entries_visited": visited,
            "max_depth": config.context_max_depth,
            "max_entries": config.context_max_entries,
        },
    }
    if config.warm_cache:
        cache_warning = _write_cache(config, data)
        if cache_warning is not None:
            warnings.append(cache_warning)
    return Result("context", data, warnings)


def _naming_patterns(candidates: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[str]] = {}
    for relative in candidates:
        path = PurePosixPath(relative)
        name = path.name
        prefix = name.rstrip("0123456789._-")
        suffix = name[len(prefix) :]
        if prefix and suffix and any(character.isdigit() for character in suffix):
            groups.setdefault((path.parent.as_posix(), prefix), []).append(name)
    result = []
    for (parent, prefix), examples in sorted(groups.items()):
        if len(examples) >= 2:
            result.append(
                {"parent": parent, "prefix": prefix, "examples": sorted(examples)[:5]}
            )
    return result
