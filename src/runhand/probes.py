"""Focused storage probes for RunHand's no-replace publication modes."""

from __future__ import annotations

import errno
from pathlib import Path
import tempfile
from typing import Any

from .errors import LocalIOError
from .publication import PUBLISH_MODES, rename_noreplace, symlink_noreplace
from .result import Result


def probe_storage(parent: str, *, publish_mode: str = "atomic") -> dict[str, Any]:
    """Check no-replace publication using only a unique temporary child."""
    result: dict[str, Any] = {
        "parent": str(Path(parent).expanduser().resolve()),
        "supported": False,
        "publish_mode": publish_mode,
    }
    try:
        if publish_mode not in PUBLISH_MODES:
            raise OSError(errno.EINVAL, "unknown publication mode")
        publish = rename_noreplace if publish_mode == "atomic" else symlink_noreplace

        # The supplied parent must already exist. Only the unique probe child
        # is removed; managed roots and ownership markers are never created.
        with tempfile.TemporaryDirectory(
            prefix=".runhand-storage-probe-", dir=result["parent"]
        ) as temporary:
            root = Path(temporary)
            source, target, existing = root / "source", root / "target", root / "existing"
            source.mkdir()
            (source / "sentinel").write_text("probe\n", encoding="utf-8")
            publish(source, target)
            if (
                source.exists() != (publish_mode == "symlink")
                or (target / "sentinel").read_text(encoding="utf-8") != "probe\n"
            ):
                raise OSError(errno.EIO, "publication did not preserve the source tree")
            existing.mkdir()
            try:
                publish(target, existing)
            except OSError as exc:
                if exc.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                    raise
            else:
                raise OSError(errno.EIO, "existing destination was replaced")
            if not (target / "sentinel").is_file() or any(existing.iterdir()):
                raise OSError(errno.EIO, "refused publication changed its source or destination")
            if publish_mode == "symlink":
                # Include a dangling link: exists() alone misses this collision.
                dangling = root / "dangling"
                dangling.symlink_to("missing")
                try:
                    publish(source, dangling)
                except OSError as exc:
                    if exc.errno != errno.EEXIST:
                        raise
                else:
                    raise OSError(errno.EIO, "existing symlink was replaced")
                if dangling.readlink() != Path("missing"):
                    raise OSError(errno.EIO, "refused publication changed an existing symlink")
        result["supported"] = True
    except OSError as exc:
        result["error"] = {"errno": exc.errno, "message": str(exc)}
    return result


def check_storage(parents: list[str], *, publish_mode: str = "atomic") -> Result:
    """Probe every requested parent and retain diagnostics for all failures."""
    checks = [probe_storage(parent, publish_mode=publish_mode) for parent in parents]
    if not all(check["supported"] for check in checks):
        raise LocalIOError(
            "storage_check_failed",
            f"{publish_mode} no-replace publication is unavailable in one or more storage parents",
            details={"checks": checks},
        )
    return Result("storage check", {"supported": True, "publish_mode": publish_mode, "checks": checks})
