"""Focused storage probes for RunHand's atomic publication requirement."""

from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

from .errors import LocalIOError
from .result import Result


def probe_storage(parent: str) -> dict[str, Any]:
    """Check no-replace publication using only a unique temporary child."""
    result: dict[str, Any] = {
        "parent": str(Path(parent).expanduser().resolve()),
        "supported": False,
    }
    try:
        if sys.platform != "linux":
            raise OSError(errno.ENOSYS, "RunHand publication requires Linux renameat2")
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, "renameat2", None)
        if rename is None:
            raise OSError(errno.ENOSYS, "this runtime does not expose renameat2")
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int

        def publish(source: Path, target: Path) -> None:
            if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1) != 0:
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error))

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
            if source.exists() or (target / "sentinel").read_text(encoding="utf-8") != "probe\n":
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
        result["supported"] = True
    except OSError as exc:
        result["error"] = {"errno": exc.errno, "message": str(exc)}
    return result


def check_storage(parents: list[str]) -> Result:
    """Probe every requested parent and retain diagnostics for all failures."""
    checks = [probe_storage(parent) for parent in parents]
    if not all(check["supported"] for check in checks):
        raise LocalIOError(
            "storage_check_failed",
            "atomic no-replace publication is unavailable in one or more storage parents",
            details={"checks": checks},
        )
    return Result("storage check", {"supported": True, "checks": checks})
