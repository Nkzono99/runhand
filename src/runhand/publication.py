"""No-replace filesystem primitives shared by publication and its probes."""

from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
import sys

PUBLISH_MODES = ("atomic", "symlink")


def rename_noreplace(source: Path, target: Path) -> None:
    """Rename a directory atomically, refusing any existing destination."""
    if sys.platform != "linux":
        raise OSError(errno.ENOSYS, "atomic publication requires Linux renameat2")
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise OSError(errno.ENOSYS, "this runtime does not expose renameat2")
    rename.argtypes = [
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint,
    ]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def symlink_noreplace(source: Path, target: Path) -> None:
    """Publish a relative directory link using exclusive symlink creation."""
    os.symlink(os.path.relpath(source, target.parent), target, target_is_directory=True)
