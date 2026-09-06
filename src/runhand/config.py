"""Configuration discovery and strict precedence handling."""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .errors import UsageError


@dataclass(frozen=True, slots=True)
class Config:
    workspace: Path
    scratch_root: Path
    state_root: Path
    completion_budget_seconds: int = 600
    scratch_ttl_days: int = 14
    context_max_depth: int = 8
    context_max_entries: int = 10_000
    warm_cache: bool = True

    def public_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key in ("workspace", "scratch_root", "state_root"):
            value[key] = str(value[key])
        return value


_DEFAULTS: dict[str, Any] = {
    "completion_budget_seconds": 600,
    "scratch_ttl_days": 14,
    "context_max_depth": 8,
    "context_max_entries": 10_000,
    "warm_cache": True,
}

_ENV_KEYS = {
    "RUNHAND_SCRATCH_ROOT": "scratch_root",
    "RUNHAND_STATE_ROOT": "state_root",
    "RUNHAND_COMPLETION_BUDGET_SECONDS": "completion_budget_seconds",
    "RUNHAND_SCRATCH_TTL_DAYS": "scratch_ttl_days",
    "RUNHAND_CONTEXT_MAX_DEPTH": "context_max_depth",
    "RUNHAND_CONTEXT_MAX_ENTRIES": "context_max_entries",
    "RUNHAND_WARM_CACHE": "warm_cache",
}


def _existing_dir(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise UsageError(
            "invalid_workspace", f"{label} is not a directory", path=resolved
        )
    return resolved


def _nearest_config(start: Path) -> Path | None:
    for directory in (start, *start.parents):
        candidate = directory / "runhand.toml"
        if candidate.is_file():
            return candidate
    return None


def _git_root(start: Path) -> Path | None:
    # Do not spawn anything during context discovery. A .git directory or
    # worktree pointer file is sufficient to establish the workspace boundary.
    for directory in (start, *start.parents):
        marker = directory / ".git"
        if marker.is_dir() or marker.is_file():
            return directory.resolve()
    return None


def resolve_workspace(
    *,
    explicit: str | None,
    start: str | None,
    environ: Mapping[str, str],
) -> Path:
    if explicit:
        return _existing_dir(Path(explicit), "workspace")
    if environ.get("RUNHAND_WORKSPACE"):
        return _existing_dir(Path(environ["RUNHAND_WORKSPACE"]), "RUNHAND_WORKSPACE")

    origin = _existing_dir(Path(start or os.getcwd()), "context path")
    config_path = _nearest_config(origin)
    if config_path is not None:
        return config_path.parent.resolve()
    git_root = _git_root(origin)
    return git_root if git_root is not None else origin


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as stream:
            raw = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise UsageError(
            "invalid_config", f"cannot read config: {exc}", path=path
        ) from exc
    version = raw.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise UsageError(
            "unsupported_config_version", "config version must be 1", path=path
        )
    return raw


def _flatten(raw: Mapping[str, Any], path: Path) -> dict[str, Any]:
    allowed_top = {"version", "observation", "scratch", "state", "context"}
    unknown_top = sorted(set(raw) - allowed_top)
    if unknown_top:
        raise UsageError(
            "unknown_config_key",
            "config contains unknown top-level keys",
            path=path,
            details={"keys": unknown_top},
        )

    sections: dict[str, set[str]] = {
        "observation": {"completion_budget_seconds"},
        "scratch": {"root", "ttl_days"},
        "state": {"root"},
        "context": {"max_depth", "max_entries", "warm_cache"},
    }
    flattened: dict[str, Any] = {}
    mapping = {
        ("observation", "completion_budget_seconds"): "completion_budget_seconds",
        ("scratch", "root"): "scratch_root",
        ("scratch", "ttl_days"): "scratch_ttl_days",
        ("state", "root"): "state_root",
        ("context", "max_depth"): "context_max_depth",
        ("context", "max_entries"): "context_max_entries",
        ("context", "warm_cache"): "warm_cache",
    }
    for section, allowed in sections.items():
        value = raw.get(section, {})
        if not isinstance(value, dict):
            raise UsageError(
                "invalid_config_type", f"[{section}] must be a table", path=path
            )
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise UsageError(
                "unknown_config_key",
                f"[{section}] contains unknown keys",
                path=path,
                details={"keys": unknown},
            )
        for key, item in value.items():
            flattened[mapping[(section, key)]] = item
    return flattened


def _parse_bool(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    raise UsageError("invalid_config_type", f"{key} must be a boolean")


def _validate(values: dict[str, Any]) -> dict[str, Any]:
    for key in ("scratch_root", "state_root"):
        if key in values and (
            not isinstance(values[key], str) or not values[key].strip()
        ):
            raise UsageError("invalid_config_type", f"{key} must be a non-empty path")
    positive = (
        "completion_budget_seconds",
        "scratch_ttl_days",
        "context_max_entries",
    )
    nonnegative = ("context_max_depth",)
    for key in positive:
        if (
            isinstance(values[key], bool)
            or not isinstance(values[key], int)
            or values[key] <= 0
        ):
            raise UsageError(
                "invalid_config_value", f"{key} must be a positive integer"
            )
    for key in nonnegative:
        if (
            isinstance(values[key], bool)
            or not isinstance(values[key], int)
            or values[key] < 0
        ):
            raise UsageError(
                "invalid_config_value", f"{key} must be a non-negative integer"
            )
    values["warm_cache"] = _parse_bool(values["warm_cache"], "warm_cache")
    return values


def load_config(
    *,
    workspace_arg: str | None,
    context_path: str | None,
    scratch_root_arg: str | None,
    state_root_arg: str | None,
    environ: Mapping[str, str] | None = None,
) -> Config:
    env = dict(os.environ if environ is None else environ)
    workspace = resolve_workspace(
        explicit=workspace_arg, start=context_path, environ=env
    )
    values = dict(_DEFAULTS)

    # Site recommendations are defaults, not safety authority.
    if env.get("RUNHAND_SITE_SCRATCH_ROOT"):
        values["scratch_root"] = env["RUNHAND_SITE_SCRATCH_ROOT"]
    if env.get("RUNHAND_SITE_STATE_ROOT"):
        values["state_root"] = env["RUNHAND_SITE_STATE_ROOT"]

    xdg_config = Path(env.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    global_path = xdg_config / "runhand" / "config.toml"
    workspace_path = workspace / "runhand.toml"
    for path in (global_path, workspace_path):
        values.update(_flatten(_load_toml(path), path))

    for env_key, config_key in _ENV_KEYS.items():
        if env_key not in env:
            continue
        raw: Any = env[env_key]
        if config_key in {
            "completion_budget_seconds",
            "scratch_ttl_days",
            "context_max_depth",
            "context_max_entries",
        }:
            try:
                raw = int(raw)
            except ValueError as exc:
                raise UsageError(
                    "invalid_environment", f"{env_key} must be an integer"
                ) from exc
        values[config_key] = raw

    if scratch_root_arg:
        values["scratch_root"] = scratch_root_arg
    if state_root_arg:
        values["state_root"] = state_root_arg

    if "scratch_root" not in values:
        if env.get("XDG_CACHE_HOME"):
            values["scratch_root"] = str(
                Path(env["XDG_CACHE_HOME"]) / "runhand" / "scratch"
            )
        elif Path.home().exists():
            values["scratch_root"] = str(Path.home() / ".cache" / "runhand" / "scratch")
        else:
            values["scratch_root"] = str(workspace / ".runhand" / "scratch")
    if "state_root" not in values:
        state_base = Path(env.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
        values["state_root"] = str(state_base / "runhand")

    values = _validate(values)
    scratch_root = Path(values.pop("scratch_root")).expanduser().resolve()
    state_root = Path(values.pop("state_root")).expanduser().resolve()
    if scratch_root == state_root:
        raise UsageError(
            "conflicting_managed_roots", "scratch root and state root must be different"
        )
    return Config(
        workspace=workspace,
        scratch_root=scratch_root,
        state_root=state_root,
        **values,
    )
