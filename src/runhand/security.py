"""Small defensive redaction for local state and JSON output."""

from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEY = re.compile(
    r"(^|_)(secret|password|passwd|token|credential|api_key|private_key|access_key)($|_)",
    re.IGNORECASE,
)


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "<redacted>"
            if _SENSITIVE_KEY.search(str(key))
            else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return [redact_sensitive(item) for item in value]
    return value
