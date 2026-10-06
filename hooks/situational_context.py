"""Offer bounded context at conversation boundaries without controlling execution."""

from __future__ import annotations

import json
from pathlib import Path
import sys


SKILL_PATH = Path(__file__).resolve().parents[1] / "skills/situational-check/SKILL.md"


def context_for(payload: object) -> str | None:
    """Select a reminder using stable event fields, without inspecting user content."""
    if not isinstance(payload, dict):
        return None

    event = payload.get("hook_event_name")
    if event == "SessionStart":
        source = payload.get("source")
        if source not in ("startup", "resume", "clear", "compact"):
            return None
        context = (
            "RunHand: keep the requested outcome, accepted constraints, and user effort "
            "ahead of a self-chosen method. Use $situational-check when feedback, "
            "repeated failure without new evidence, or unexpected cost calls for "
            "reorientation. During smooth progress, continue directly."
        )
        if source in ("resume", "compact"):
            context += (
                " Before continuing, recover the current goal and corrections from "
                "available context, retaining pending jobs, artifacts, authorization, "
                "budgets, and observation deadlines."
            )
    elif event == "UserPromptSubmit":
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return None
        context = (
            "RunHand: incorporate the latest message into the ongoing task. If it "
            "changes direction or constraints, or reveals friction or stalled work, "
            "use $situational-check to change the next action accordingly. Preserve "
            "existing authorization and budgets. During smooth progress, continue directly."
        )
    else:
        return None

    if not SKILL_PATH.is_file():
        return None
    return f"{context} Read {json.dumps(str(SKILL_PATH), ensure_ascii=False)} if needed; reuse it if already read."


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    context = context_for(payload)
    if context:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": payload["hook_event_name"],
            "additionalContext": context,
        }}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
