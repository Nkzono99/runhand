"""Optional machine check of supplied Site evidence; never calls a scheduler."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return result


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _scope(value: Any) -> tuple[str, str]:
    if not isinstance(value, dict) or not all(
        _text(value.get(key)) for key in ("site", "cluster")
    ):
        raise ValueError("site and cluster are required")
    return value["site"], value["cluster"]


def _identity(value: Any) -> tuple[str, str]:
    scope = _scope(value)
    if not _text(value.get("job_id")):
        raise ValueError("job_id is required")
    _timestamp(value.get("submit_time"))
    return scope


def retry_decision(request: Any, *, now: datetime | None = None) -> dict[str, str]:
    """Interpret a fresh, scope-complete retry preflight supplied by a Site.

    The caller supplies the start of this preflight as ``not_before`` and every
    relevant site/cluster as ``scopes``. Missing evidence never permits retry.
    The decision is evidence, not authorization or a cross-Agent lock.
    """

    def decision(result: str, reason: str) -> dict[str, str]:
        return {"decision": result, "reason": reason}

    try:
        if not isinstance(request, dict) or not _text(request.get("target")):
            raise ValueError("target is required")
        if not {"pending_keys", "unresolved_without_key"}.issubset(request):
            raise ValueError("previous submission evidence must be explicit")
        not_before = _timestamp(request.get("not_before"))
        checked_at = now if now is not None else datetime.now(timezone.utc)
        if checked_at.tzinfo is None or checked_at.utcoffset() is None:
            raise ValueError("current time must include a timezone")
        if not_before > checked_at:
            return decision("unknown", "future_preflight")
        scopes = request.get("scopes")
        if not isinstance(scopes, list) or not scopes:
            raise ValueError("known query scopes are required")
        expected = {_scope(item) for item in scopes}
        if len(expected) != len(scopes):
            raise ValueError("query scopes must be unique")
        pending = request["pending_keys"]
        if not isinstance(pending, list):
            raise ValueError("pending_keys must be an array")
        pending_scoped: set[tuple[str, str, str]] = set()
        for item in pending:
            scope = _scope(item)
            if scope not in expected or not _text(item.get("key")):
                raise ValueError("pending keys must have a covered site/cluster and key")
            pending_scoped.add((*scope, item["key"]))
        unkeyed = request["unresolved_without_key"]
        if not isinstance(unkeyed, bool):
            raise ValueError("unresolved_without_key must be boolean")
        if unkeyed:
            return decision("unknown", "unkeyed_submission_unresolved")
        queries = request.get("queries")
        if not isinstance(queries, list):
            raise ValueError("queries are required")
        seen: set[tuple[str, str]] = set()
        resolved: set[tuple[str, str, str]] = set()
        active = False
        unresolved = False
        incomplete = False
        for query in queries:
            if not isinstance(query, dict) or query.get("schema") != 2:
                raise ValueError("query evidence must use schema 2")
            scope = _scope(query)
            if scope not in expected or scope in seen:
                return decision("unknown", "query_scope_mismatch")
            seen.add(scope)
            if query.get("target") != request["target"]:
                return decision("unknown", "query_target_mismatch")
            observed_at = _timestamp(query.get("observed_at"))
            if observed_at < not_before:
                return decision("unknown", "stale_query")
            if observed_at > checked_at:
                return decision("unknown", "future_query")
            result = query.get("result")
            coverage = query.get("coverage")
            if result not in {"available", "unavailable"} or coverage not in {
                "complete", "partial", "unknown"
            }:
                raise ValueError("query result and coverage are required")
            incomplete |= result != "available" or coverage != "complete"
            attempts = query.get("attempts")
            keys = query.get("resolved_submission_keys")
            if not isinstance(attempts, list) or not isinstance(keys, list):
                raise ValueError("attempts and resolved_submission_keys are required")
            if not all(_text(key) for key in keys):
                raise ValueError("resolved keys must be non-empty strings")
            if result == "available" and coverage == "complete":
                resolved.update((*scope, key) for key in keys)
            for attempt in attempts:
                if not isinstance(attempt, dict):
                    raise ValueError("attempt must be an object")
                if _identity(attempt.get("job_identity")) != scope:
                    return decision("unknown", "job_scope_mismatch")
                state = attempt.get("state")
                if state not in {"active", "terminal", "unresolved", "unknown"}:
                    raise ValueError("attempt state is required")
                active |= state == "active"
                unresolved |= state in {"unresolved", "unknown"}
        if active:
            return decision("blocked", "active_attempt")
        if unresolved:
            return decision("unknown", "attempt_unresolved")
        if seen != expected or incomplete:
            return decision("unknown", "query_coverage_incomplete")
        if not pending_scoped.issubset(resolved):
            return decision("unknown", "submission_key_unresolved")
        return decision("allowed", "no_active_or_unresolved_attempt")
    except (ValueError, TypeError, OverflowError):
        return decision("unknown", "invalid_evidence")
