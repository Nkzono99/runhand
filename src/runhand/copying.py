"""Versioned copy-plan parsing and source-safe copying."""

from __future__ import annotations

import fnmatch
import json
import os
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, NoReturn

from .errors import LocalIOError, PlanError


@dataclass(frozen=True, slots=True)
class CopyPlan:
    source: Path
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    symlink_policy: str
    completeness: str
    basis: dict[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema": 1,
            "source": str(self.source),
            "include": list(self.include),
            "exclude": list(self.exclude),
            "symlink_policy": self.symlink_policy,
            "completeness": self.completeness,
        }
        if self.basis is not None:
            value["basis"] = self.basis
        return value


@dataclass(frozen=True, slots=True)
class Entry:
    relative: str
    kind: str
    size: int
    signature: tuple[int, int, int, int, int]
    link_target: str | None = None


def _normalize_pattern(pattern: Any) -> str:
    if not isinstance(pattern, str) or not pattern:
        raise PlanError(
            "invalid_pattern", "copy-plan patterns must be non-empty strings"
        )
    candidate = PurePosixPath(pattern)
    if candidate.is_absolute() or ".." in candidate.parts or "\\" in pattern:
        raise PlanError(
            "unsafe_pattern", "copy-plan patterns must be relative POSIX paths"
        )
    return pattern.removeprefix("./").rstrip("/") or "."


def load_copy_plan(path: str, source_argument: str) -> CopyPlan:
    try:
        if path == "-":
            raw = json.load(sys.stdin)
        else:
            with Path(path).open("r", encoding="utf-8") as stream:
                raw = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PlanError(
            "copy_plan_read_failed", f"cannot read copy plan: {exc}"
        ) from exc
    return parse_copy_plan(raw, source_argument)


def parse_copy_plan(raw: Any, source_argument: str) -> CopyPlan:
    """Use the same contract for JSON plans and explicit CLI selections."""
    if not isinstance(raw, dict):
        raise PlanError("invalid_copy_plan", "copy plan must be a JSON object")
    allowed = {
        "schema",
        "source",
        "include",
        "exclude",
        "symlink_policy",
        "completeness",
        "basis",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise PlanError(
            "unknown_copy_plan_key",
            "copy plan contains unknown keys",
            details={"keys": unknown},
        )
    if raw.get("schema") != 1:
        raise PlanError("unsupported_copy_plan_version", "copy plan schema must be 1")
    if not isinstance(raw.get("source"), str):
        raise PlanError("missing_copy_plan_source", "copy plan source is required")
    source = Path(source_argument).expanduser().resolve()
    plan_source = Path(raw["source"]).expanduser().resolve()
    if source != plan_source:
        raise PlanError(
            "copy_plan_source_mismatch",
            "--source and copy plan source resolve to different paths",
            details={"argument": str(source), "plan": str(plan_source)},
        )
    if not source.is_dir():
        raise PlanError(
            "invalid_copy_source", "copy source must be a directory", path=source
        )
    include_raw = raw.get("include")
    exclude_raw = raw.get("exclude", [])
    if not isinstance(include_raw, list) or not include_raw:
        raise PlanError(
            "invalid_copy_include", "copy plan include must be a non-empty array"
        )
    if not isinstance(exclude_raw, list):
        raise PlanError("invalid_copy_exclude", "copy plan exclude must be an array")
    include = tuple(_normalize_pattern(item) for item in include_raw)
    exclude = tuple(_normalize_pattern(item) for item in exclude_raw)
    symlink_policy = raw.get("symlink_policy", "internal-relative")
    if symlink_policy not in {"internal-relative", "reject"}:
        raise PlanError(
            "invalid_symlink_policy",
            "symlink_policy must be internal-relative or reject",
        )
    completeness = raw.get("completeness")
    if completeness not in {"complete", "partial"}:
        raise PlanError(
            "invalid_completeness", "completeness must be complete or partial"
        )
    basis = raw.get("basis")
    if basis is not None and not isinstance(basis, dict):
        raise PlanError("invalid_copy_basis", "basis must be an object")
    return CopyPlan(source, include, exclude, symlink_policy, completeness, basis)


def _segment_match(path_parts: tuple[str, ...], pattern_parts: tuple[str, ...]) -> bool:
    if not pattern_parts:
        return not path_parts
    head = pattern_parts[0]
    if head == "**":
        return _segment_match(path_parts, pattern_parts[1:]) or (
            bool(path_parts) and _segment_match(path_parts[1:], pattern_parts)
        )
    return (
        bool(path_parts)
        and fnmatch.fnmatchcase(path_parts[0], head)
        and _segment_match(path_parts[1:], pattern_parts[1:])
    )


def _matches(relative: str, patterns: Iterable[str]) -> bool:
    parts = PurePosixPath(relative).parts
    return any(
        _segment_match(parts, PurePosixPath(pattern).parts) for pattern in patterns
    )


def _signature(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_mode,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def select_entries(plan: CopyPlan) -> list[Entry]:
    try:
        root_info = plan.source.lstat()
    except OSError as exc:
        raise LocalIOError(
            "source_stat_failed", f"cannot inspect copy source: {exc}", path=plan.source
        ) from exc
    entries: list[Entry] = [Entry(".", "guard", 0, _signature(root_info))]

    def walk(directory: Path, relative_parent: PurePosixPath) -> None:
        try:
            children = sorted(
                os.scandir(directory), key=lambda item: os.fsencode(item.name)
            )
        except OSError as exc:
            raise LocalIOError(
                "source_scan_failed", f"cannot scan source: {exc}", path=directory
            ) from exc
        for child in children:
            relative_path = relative_parent / child.name
            relative = relative_path.as_posix()
            excluded = _matches(relative, plan.exclude)
            try:
                info = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise LocalIOError(
                    "source_stat_failed",
                    f"cannot inspect source entry: {exc}",
                    path=Path(child.path),
                ) from exc
            mode = info.st_mode
            if stat.S_ISDIR(mode):
                if not excluded:
                    kind = "directory" if _matches(relative, plan.include) else "guard"
                    entries.append(Entry(relative, kind, 0, _signature(info)))
                    walk(Path(child.path), relative_path)
                continue
            selected = _matches(relative, plan.include) and not excluded
            if not selected:
                continue
            if stat.S_ISREG(mode):
                entries.append(Entry(relative, "file", info.st_size, _signature(info)))
                continue
            if stat.S_ISLNK(mode):
                if plan.symlink_policy == "reject":
                    raise PlanError(
                        "symlink_rejected",
                        "copy selection contains a symlink",
                        path=Path(child.path),
                    )
                try:
                    target = os.readlink(child.path)
                except OSError as exc:
                    raise LocalIOError(
                        "symlink_read_failed",
                        f"cannot read symlink: {exc}",
                        path=Path(child.path),
                    ) from exc
                if os.path.isabs(target):
                    raise PlanError(
                        "absolute_symlink",
                        "absolute symlinks are not allowed",
                        path=Path(child.path),
                    )
                resolved_target = (Path(child.path).parent / target).resolve(
                    strict=False
                )
                try:
                    resolved_target.relative_to(plan.source)
                except ValueError as exc:
                    raise PlanError(
                        "external_symlink",
                        "symlink resolves outside the copy source",
                        path=Path(child.path),
                    ) from exc
                if not resolved_target.exists():
                    raise PlanError(
                        "dangling_symlink",
                        "symlink target does not exist",
                        path=Path(child.path),
                    )
                entries.append(Entry(relative, "symlink", 0, _signature(info), target))
                continue
            raise PlanError(
                "special_file",
                "copy selection contains an unsupported special file",
                path=Path(child.path),
            )

    walk(plan.source, PurePosixPath())
    materialized: dict[str, tuple[str, str | None]] = {".": ("directory", None)}
    for entry in entries:
        if entry.kind == "guard":
            continue
        current = PurePosixPath(entry.relative)
        materialized[current.as_posix()] = (entry.kind, entry.link_target)
        for parent in current.parents:
            materialized.setdefault(parent.as_posix(), ("directory", None))
    for entry in entries:
        if entry.kind != "symlink" or entry.link_target is None:
            continue
        _verify_selected_link(plan.source, entry, materialized)
    return entries


def _verify_selected_link(
    source: Path,
    entry: Entry,
    materialized: dict[str, tuple[str, str | None]],
) -> None:
    """Resolve the link through the selected destination graph, component by component."""

    def unresolved(reason: str) -> NoReturn:
        raise PlanError(
            "uncopied_symlink_target",
            "selected symlink target cannot be resolved in the copy selection",
            path=source / entry.relative,
            details={"target": entry.link_target, "reason": reason},
        )

    def resolve(
        current: tuple[str, ...], parts: list[str], active: frozenset[str]
    ) -> tuple[str, ...]:
        for part in parts:
            parent = "/".join(current) or "."
            if materialized.get(parent, (None, None))[0] != "directory":
                unresolved(f"intermediate path is not a selected directory: {parent}")
            if part in {"", "."}:
                continue
            if part == "..":
                if not current:
                    unresolved("target escapes the selected tree")
                current = current[:-1]
                continue
            candidate = current + (part,)
            path = "/".join(candidate)
            selected = materialized.get(path)
            if selected is None:
                unresolved(f"path component is excluded from the copy selection: {path}")
            kind, target = selected
            if kind == "symlink":
                if path in active:
                    unresolved(f"selected symlink cycle: {path}")
                current = resolve(current, (target or "").split("/"), active | {path})
            else:
                current = candidate
        return current

    resolve(
        PurePosixPath(entry.relative).parent.parts,
        (entry.link_target or "").split("/"),
        frozenset({entry.relative}),
    )


def _verify_entry(source: Path, entry: Entry) -> None:
    path = source / entry.relative
    try:
        current = path.lstat()
    except OSError as exc:
        raise PlanError(
            "source_changed", "selected source entry disappeared during copy", path=path
        ) from exc
    if _signature(current) != entry.signature:
        raise PlanError(
            "source_changed", "selected source entry changed during copy", path=path
        )
    if entry.kind == "symlink" and os.readlink(path) != entry.link_target:
        raise PlanError(
            "source_changed", "selected symlink changed during copy", path=path
        )


def copy_entries(plan: CopyPlan, entries: list[Entry], destination: Path) -> None:
    try:
        source_root_signature = _signature(plan.source.lstat())
    except OSError as exc:
        raise LocalIOError(
            "source_stat_failed", f"cannot inspect copy source: {exc}", path=plan.source
        ) from exc
    try:
        destination.mkdir(parents=True, exist_ok=False)
        for entry in entries:
            source_path = plan.source / entry.relative
            destination_path = destination / entry.relative
            _verify_entry(plan.source, entry)
            if entry.kind == "guard":
                continue
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            if entry.kind == "directory":
                destination_path.mkdir(parents=True, exist_ok=True)
            elif entry.kind == "file":
                shutil.copy2(source_path, destination_path, follow_symlinks=False)
            else:
                os.symlink(entry.link_target or "", destination_path)
            _verify_entry(plan.source, entry)
        for entry in entries:
            _verify_entry(plan.source, entry)
        for entry in sorted(
            (item for item in entries if item.kind == "directory"),
            key=lambda item: item.relative.count("/"),
            reverse=True,
        ):
            shutil.copystat(
                plan.source / entry.relative,
                destination / entry.relative,
                follow_symlinks=False,
            )
        shutil.copystat(plan.source, destination, follow_symlinks=False)
        if _signature(plan.source.lstat()) != source_root_signature:
            raise PlanError(
                "source_changed",
                "copy source root changed during copy",
                path=plan.source,
            )
        for entry in entries:
            _verify_entry(plan.source, entry)
    except (PlanError, LocalIOError):
        raise
    except OSError as exc:
        raise LocalIOError(
            "copy_failed", f"copy failed: {exc}", path=destination
        ) from exc
