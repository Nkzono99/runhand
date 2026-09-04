"""RunHand command-line interface."""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from collections.abc import Sequence
from typing import NoReturn

from . import __version__
from .config import Config, load_config
from .context import scan_context
from .copying import load_copy_plan
from .doctor import run_doctor
from .errors import RunHandError, UsageError
from .gc import collect, collect_orphan
from .result import Result
from .scratch import get_scratch, set_pin
from .security import redact_sensitive
from .stage import create_stage, inspect_stage, promote_stage


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise UsageError("usage_error", message)


def _leaf_options(parser: argparse.ArgumentParser, *, dry_run: bool = False) -> None:
    if dry_run:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="show effects without modifying state",
        )
    parser.add_argument(
        "--json", action="store_true", help="emit one versioned JSON object"
    )


def build_parser() -> Parser:
    parser = Parser(
        prog="runhand",
        description="Deterministic filesystem worker for research workflows",
    )
    parser.add_argument("--version", action="version", version=f"runhand {__version__}")
    parser.add_argument(
        "--workspace", metavar="PATH", help="override workspace discovery"
    )
    parser.add_argument(
        "--scratch-root", metavar="PATH", help="override disposable storage root"
    )
    parser.add_argument(
        "--state-root", metavar="PATH", help="override local state root"
    )
    commands = parser.add_subparsers(dest="command", required=True, parser_class=Parser)

    context = commands.add_parser(
        "context", help="scan workspace context without executing discoveries"
    )
    context.add_argument(
        "path", nargs="?", help="directory from which to resolve a workspace"
    )
    context.add_argument(
        "--refresh", action="store_true", help="ignore the candidate cache"
    )
    _leaf_options(context)

    stage = commands.add_parser("stage", help="manage working stages")
    stage_commands = stage.add_subparsers(
        dest="stage_command", required=True, parser_class=Parser
    )
    stage_create = stage_commands.add_parser(
        "create", help="create a stage from a versioned copy plan"
    )
    stage_create.add_argument("--source", required=True, metavar="PATH")
    stage_create.add_argument(
        "--plan", required=True, metavar="FILE|-", help="JSON copy plan or stdin"
    )
    _leaf_options(stage_create, dry_run=True)
    stage_inspect = stage_commands.add_parser("inspect", help="inspect a RunHand stage")
    stage_inspect.add_argument("stage", metavar="STAGE")
    _leaf_options(stage_inspect)

    promote = commands.add_parser(
        "promote", help="atomically publish a complete stage without replacement"
    )
    promote.add_argument("stage", metavar="STAGE")
    promote.add_argument("target", metavar="TARGET")
    _leaf_options(promote, dry_run=True)

    scratch = commands.add_parser("scratch", help="manage unique disposable tasks")
    scratch_commands = scratch.add_subparsers(
        dest="scratch_command", required=True, parser_class=Parser
    )
    scratch_get = scratch_commands.add_parser(
        "get", help="allocate a unique scratch task"
    )
    scratch_get.add_argument(
        "--kind",
        required=True,
        choices=["smoke", "pilot", "debug", "analysis", "preview"],
    )
    scratch_get.add_argument(
        "--key", required=True, help="lookup hint; raw value is not persisted"
    )
    scratch_get.add_argument("--pin", action="store_true")
    _leaf_options(scratch_get, dry_run=True)
    for name in ("pin", "unpin"):
        pin_parser = scratch_commands.add_parser(name, help=f"{name} a scratch task")
        pin_parser.add_argument("task", metavar="TASK")
        _leaf_options(pin_parser, dry_run=True)

    gc = commands.add_parser(
        "gc", help="preview or delete RunHand-owned disposable data"
    )
    gc.add_argument(
        "--kind", choices=["scratch", "cache", "history", "all"], default="all"
    )
    gc.add_argument("--older-than", metavar="DURATION")
    gc.add_argument(
        "--orphan",
        metavar="TASK",
        help="exact orphan task already rechecked through Site",
    )
    gc.add_argument(
        "--apply", action="store_true", help="perform deletion; omission is preview"
    )
    _leaf_options(gc)

    doctor = commands.add_parser(
        "doctor", help="inspect version, config, roots, and plugin cues"
    )
    _leaf_options(doctor)
    return parser


def _config(namespace: argparse.Namespace) -> Config:
    context_path = namespace.path if namespace.command == "context" else None
    return load_config(
        workspace_arg=namespace.workspace,
        context_path=context_path,
        scratch_root_arg=namespace.scratch_root,
        state_root_arg=namespace.state_root,
    )


def dispatch(namespace: argparse.Namespace) -> Result:
    config = _config(namespace)
    if namespace.command == "context":
        return scan_context(config, refresh=namespace.refresh)
    if namespace.command == "stage":
        if namespace.stage_command == "create":
            plan = load_copy_plan(namespace.plan, namespace.source)
            return create_stage(config, plan, dry_run=namespace.dry_run)
        return inspect_stage(namespace.stage)
    if namespace.command == "promote":
        return promote_stage(
            namespace.stage, namespace.target, dry_run=namespace.dry_run
        )
    if namespace.command == "scratch":
        if namespace.scratch_command == "get":
            return get_scratch(
                config,
                kind=namespace.kind,
                key=namespace.key,
                pin=namespace.pin,
                dry_run=namespace.dry_run,
            )
        return set_pin(
            config,
            namespace.task,
            pinned=namespace.scratch_command == "pin",
            dry_run=namespace.dry_run,
        )
    if namespace.command == "gc":
        if namespace.orphan:
            return collect_orphan(
                config, task_arg=namespace.orphan, apply=namespace.apply
            )
        return collect(
            config,
            kind=namespace.kind,
            older_than=namespace.older_than,
            apply=namespace.apply,
        )
    if namespace.command == "doctor":
        return run_doctor(config)
    raise UsageError("usage_error", "unknown command")


def _infer_command(arguments: Sequence[str]) -> str:
    for index, item in enumerate(arguments):
        if item not in {"context", "stage", "promote", "scratch", "gc", "doctor"}:
            continue
        if item in {"stage", "scratch"} and index + 1 < len(arguments):
            child = arguments[index + 1]
            if not child.startswith("-"):
                return f"{item} {child}"
        return item
    return "runhand"


def _error_envelope(command: str, error: RunHandError) -> dict[str, object]:
    return {
        "schema": 1,
        "ok": False,
        "command": command,
        "data": {},
        "warnings": [],
        "errors": [error.as_dict()],
    }


def _emit(value: dict[str, object], *, json_mode: bool) -> None:
    sanitized = redact_sensitive(value)
    if json_mode:
        json.dump(
            sanitized,
            sys.stdout,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        sys.stdout.write("\n")
        return
    if sanitized["ok"]:
        command = sanitized["command"]
        print(f"runhand {command}: ok")
        print(
            json.dumps(sanitized["data"], ensure_ascii=False, indent=2, sort_keys=True)
        )
        for item in sanitized["warnings"]:
            print(
                f"warning [{item.get('code', 'warning')}]: {item.get('message', 'unspecified warning')}",
                file=sys.stderr,
            )
    else:
        for item in sanitized["errors"]:
            print(
                f"error [{item.get('code', 'error')}]: {item.get('message', 'unspecified error')}",
                file=sys.stderr,
            )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    json_mode = "--json" in arguments
    command = _infer_command(arguments)
    try:
        namespace = build_parser().parse_args(arguments)
        json_mode = bool(getattr(namespace, "json", False))
        result = dispatch(namespace)
        _emit(result.envelope(), json_mode=json_mode)
        return 0
    except RunHandError as exc:
        _emit(_error_envelope(command, exc), json_mode=json_mode)
        return exc.exit_code
    except KeyboardInterrupt:
        error = RunHandError("interrupted", "operation interrupted", 1, retryable=True)
        _emit(_error_envelope(command, error), json_mode=json_mode)
        return error.exit_code
    except Exception as exc:
        if os.environ.get("RUNHAND_DEBUG"):
            traceback.print_exc(file=sys.stderr)
        error = RunHandError("internal_error", str(exc) or type(exc).__name__, 1)
        _emit(_error_envelope(command, error), json_mode=json_mode)
        return error.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
