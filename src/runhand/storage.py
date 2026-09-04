"""Safe local state helpers."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from .errors import LocalIOError, UnsafeError
from .security import redact_sensitive

OWNER_FILE = ".runhand-owner.json"
OWNER_SCHEMA = 1


def utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LocalIOError(
            "state_read_failed", f"cannot read local state: {exc}", path=path
        ) from exc
    if not isinstance(value, dict):
        raise LocalIOError(
            "invalid_local_state", "local state must be a JSON object", path=path
        )
    return value


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temp_path = Path(temporary)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(
                    redact_sensitive(value),
                    stream,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, path)
        finally:
            if temp_path.exists():
                temp_path.unlink()
    except OSError as exc:
        raise LocalIOError(
            "state_write_failed", f"cannot write local state: {exc}", path=path
        ) from exc


def ensure_owned_root(root: Path, kind: str) -> None:
    marker = root / OWNER_FILE
    if root.is_symlink():
        raise UnsafeError(
            "managed_root_symlink",
            "managed root must not be a symlink",
            path=root,
        )
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise LocalIOError(
            "managed_root_create_failed",
            f"cannot create managed root: {exc}",
            path=root,
        ) from exc
    if marker.exists():
        if not root_is_owned(root, kind):
            raise UnsafeError(
                "invalid_owner_marker",
                "managed root has an incompatible owner marker",
                path=marker,
            )
        return
    try:
        nonempty = any(root.iterdir())
    except OSError as exc:
        raise LocalIOError(
            "managed_root_inspect_failed",
            f"cannot inspect managed root: {exc}",
            path=root,
        ) from exc
    if nonempty:
        # Another creator may have installed the marker between the first
        # existence check and directory inspection.
        if marker.exists() and root_is_owned(root, kind):
            return
        raise UnsafeError(
            "unowned_nonempty_root",
            "refusing to adopt a non-empty directory without a RunHand owner marker",
            path=root,
        )
    atomic_write_json(
        marker, {"schema": OWNER_SCHEMA, "owner": "runhand", "kind": kind}
    )


def ensure_owned_subdir(root: Path, kind: str, *parts: str) -> Path:
    """Create an internal managed directory without following symlink containers."""
    ensure_owned_root(root, kind)
    current = root
    for part in parts:
        if not part or part in {".", ".."} or "/" in part or "\\" in part:
            raise UnsafeError(
                "invalid_managed_subdir",
                "managed subdirectory component is unsafe",
                path=current / part,
            )
        current = current / part
        try:
            current.mkdir(exist_ok=True)
            info = current.lstat()
        except OSError as exc:
            raise LocalIOError(
                "managed_subdir_create_failed",
                f"cannot create managed subdirectory: {exc}",
                path=current,
            ) from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise UnsafeError(
                "unsafe_managed_subdir",
                "managed subdirectory is not a real directory",
                path=current,
            )
        if not is_within(current, root):
            raise UnsafeError(
                "managed_subdir_escape",
                "managed subdirectory escapes its owned root",
                path=current,
            )
    return current


def root_is_owned(root: Path, kind: str) -> bool:
    if root.is_symlink():
        return False
    marker = root / OWNER_FILE
    if marker.is_symlink() or not marker.is_file():
        return False
    try:
        value = read_json(marker)
    except LocalIOError:
        return False
    return (
        value.get("schema") == OWNER_SCHEMA
        and value.get("owner") == "runhand"
        and value.get("kind") == kind
    )


def require_owned_root(root: Path, kind: str) -> None:
    if not root_is_owned(root, kind):
        raise UnsafeError(
            "unowned_managed_root",
            "configured managed root is missing a compatible RunHand owner marker",
            path=root,
        )


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True
