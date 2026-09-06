"""Pure path-output formatting for shell composition.

The CLI validates a command's supported selector and its destination paths
before dispatch. These helpers never inspect the filesystem or write output.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import PlanError, UsageError


def validate_printable_path(value: str | Path) -> str:
    """Return an absolute path that can be captured as one shell output line.

    Preserve spaces and shell metacharacters verbatim. Callers must quote the
    captured value when using it; this function neither quotes nor normalizes it.
    Reject line breaks before a mutating command, since command substitution
    would otherwise discard trailing newlines from a valid POSIX filename.
    """
    path = str(value) if isinstance(value, Path) else value
    if (
        not isinstance(path, str)
        or not path
        or any(character in path for character in ("\n", "\r", "\0"))
        or not Path(path).is_absolute()
    ):
        raise UsageError(
            "invalid_print_path",
            "path output requires a nonempty absolute path without LF, CR, or NUL",
        )
    return path


def emit_success_path(envelope: dict[str, Any], field_name: str) -> str:
    """Extract one top-level data path from a successful command receipt.

    Return only the path text. The CLI owns redaction, the final newline,
    warnings/errors on stderr, and command-specific selector validation.
    """
    if not isinstance(envelope, dict) or envelope.get("ok") is not True:
        raise PlanError(
            "invalid_path_result", "cannot print a path from an unsuccessful command result"
        )
    data = envelope.get("data")
    if (
        not isinstance(field_name, str)
        or not field_name
        or not isinstance(data, dict)
        or not isinstance(data.get(field_name), str)
    ):
        raise PlanError(
            "invalid_path_result",
            "command reported success but did not return the requested path string",
        )
    try:
        return validate_printable_path(data[field_name])
    except UsageError as exc:
        raise PlanError(
            "invalid_path_result",
            "command reported success but returned a path that cannot be printed safely",
        ) from exc
