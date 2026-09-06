"""RunHand command-line interface."""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from . import __version__
from .config import Config, load_config
from .context import scan_context
from .copying import load_copy_plan, parse_copy_plan
from .doctor import run_doctor
from .errors import PlanError, RunHandError, UnsafeError, UsageError
from .gc import collect, collect_orphan
from .result import Result
from .output import emit_success_path, validate_printable_path
from .probes import check_storage
from .scratch import get_scratch, load_scratch_evidence, prepare_scratch, record_evidence, set_pin
from .security import redact_sensitive
from .stage import create_stage, inspect_stage, promote_stage, resolve_target_path
from .submission import retry_decision


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise UsageError("usage_error", message)


def _leaf_options(
    parser: argparse.ArgumentParser, *, dry_run: bool = False, paths: tuple[str, ...] = ()
) -> None:
    if dry_run:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="show effects without modifying state",
        )
    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "--json", action="store_true", help="emit one versioned JSON object"
    )
    if paths:
        output.add_argument(
            "--print-path", choices=paths,
            help="print only the selected absolute path; warnings/errors go to stderr (not with --dry-run)",
        )


def _copy_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source", required=True, metavar="PATH")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--plan", metavar="FILE|-", help="versioned JSON copy plan or stdin")
    selection.add_argument("--include", action="append", metavar="PATTERN", help="explicit relative pattern; repeat and quote globs")
    parser.add_argument("--exclude", action="append", metavar="PATTERN")
    parser.add_argument("--symlink-policy", choices=["internal-relative", "reject"])


def _copy_plan(namespace: argparse.Namespace):
    if namespace.plan is not None:
        if any(getattr(namespace, key) is not None for key in ("exclude", "symlink_policy")):
            raise UsageError("conflicting_copy_selection", "--plan cannot be combined with selection flags")
        return load_copy_plan(namespace.plan, namespace.source)
    return parse_copy_plan({
        "schema": 1, "source": namespace.source,
        "include": namespace.include, "exclude": namespace.exclude or [],
        "symlink_policy": namespace.symlink_policy or "internal-relative",
        "completeness": "partial", "basis": {"kind": "user"},
    }, namespace.source)


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
    _copy_options(stage_create)
    _leaf_options(stage_create, dry_run=True, paths=("stage", "tree"))
    stage_inspect = stage_commands.add_parser("inspect", help="inspect a RunHand stage")
    stage_inspect.add_argument("stage", metavar="STAGE")
    _leaf_options(stage_inspect)

    promote = commands.add_parser(
        "promote", help="atomically publish a complete stage without replacement"
    )
    promote.add_argument("stage", metavar="STAGE")
    promote.add_argument("target", metavar="TARGET")
    _leaf_options(promote, dry_run=True, paths=("target",))

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
        "--key", help="optional label stored as a digest; never searches or reuses tasks"
    )
    scratch_get.add_argument("--pin", action="store_true")
    _leaf_options(scratch_get, dry_run=True, paths=("task",))
    scratch_prepare = scratch_commands.add_parser(
        "prepare", help="allocate a fresh task and copy selected inputs once into its work directory"
    )
    scratch_prepare.add_argument("--kind", required=True, choices=["smoke", "pilot", "debug", "analysis", "preview"])
    scratch_prepare.add_argument("--key", help="optional label stored as a digest; never searches or reuses tasks")
    scratch_prepare.add_argument("--pin", action="store_true")
    _copy_options(scratch_prepare)
    _leaf_options(scratch_prepare, dry_run=True, paths=("task", "workdir"))
    for name in ("pin", "unpin"):
        pin_parser = scratch_commands.add_parser(name, help=f"{name} a scratch task")
        pin_parser.add_argument("task", metavar="TASK")
        _leaf_options(pin_parser, dry_run=True)
    scratch_record = scratch_commands.add_parser(
        "record", help="record Site or Simulator task evidence without executing anything"
    )
    scratch_record.add_argument("task", metavar="TASK")
    scratch_record.add_argument("--evidence", metavar="FILE|-")
    scratch_record.add_argument("--liveness", choices=["active", "terminal", "unknown"])
    scratch_record.add_argument("--attempt-state", choices=["none", "active", "terminal", "unresolved", "unknown"])
    scratch_record.add_argument("--observed-at", metavar="ISO_TIMESTAMP", help="actual observation time with timezone; never defaults to now")
    scratch_record.add_argument("--capability", choices=["site", "simulator"])
    scratch_record.add_argument("--identity", help="actual evidence provider/skill identity")
    _leaf_options(scratch_record, dry_run=True)

    gc = commands.add_parser(
        "gc", help="preview or delete RunHand-owned disposable data"
    )
    gc.add_argument(
        "--kind", choices=["scratch", "cache", "all"], default="all"
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

    storage = commands.add_parser("storage", help="check storage operation support")
    storage_commands = storage.add_subparsers(dest="storage_command", required=True, parser_class=Parser)
    storage_check = storage_commands.add_parser("check", help="probe atomic no-replace publication using tiny temporary directories")
    storage_check.add_argument("parents", nargs="+", metavar="PARENT")
    _leaf_options(storage_check)

    retry = commands.add_parser("retry", help="check supplied submission evidence without contacting a scheduler")
    retry_commands = retry.add_subparsers(dest="retry_command", required=True, parser_class=Parser)
    retry_check = retry_commands.add_parser("check", help="decide whether observed attempts permit an authorized retry")
    retry_check.add_argument("--evidence", required=True, metavar="FILE|-")
    retry_check.add_argument("--require-allowed", action="store_true", help="exit nonzero when the decision is blocked or unknown")
    _leaf_options(retry_check)
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
    if namespace.command == "storage":
        return check_storage(namespace.parents)
    if namespace.command == "retry":
        try:
            if namespace.evidence == "-":
                evidence = json.load(sys.stdin)
            else:
                evidence = json.loads(Path(namespace.evidence).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise PlanError("retry_evidence_read_failed", f"cannot read retry evidence: {exc}") from exc
        decision = retry_decision(evidence)
        if namespace.require_allowed and decision["decision"] != "allowed":
            raise UnsafeError("retry_not_allowed", "retry evidence does not allow dispatch", details=decision)
        return Result("retry check", decision)
    config = _config(namespace)
    if getattr(namespace, "print_path", None):
        if namespace.command == "promote":
            path = str(resolve_target_path(namespace.target))
        else:
            path = str(config.scratch_root)
        validate_printable_path(path)
        if redact_sensitive(path) != path:
            raise UsageError("invalid_print_path", "path-only output cannot represent a redacted path; use --json")
    if namespace.command == "context":
        return scan_context(config, refresh=namespace.refresh)
    if namespace.command == "stage":
        if namespace.stage_command == "create":
            plan = _copy_plan(namespace)
            return create_stage(config, plan, dry_run=namespace.dry_run)
        return inspect_stage(namespace.stage)
    if namespace.command == "promote":
        return promote_stage(
            namespace.stage, namespace.target, dry_run=namespace.dry_run
        )
    if namespace.command == "scratch":
        if namespace.scratch_command == "prepare":
            return prepare_scratch(
                config, _copy_plan(namespace), kind=namespace.kind, key=namespace.key,
                pin=namespace.pin, dry_run=namespace.dry_run,
            )
        if namespace.scratch_command == "get":
            return get_scratch(
                config,
                kind=namespace.kind,
                key=namespace.key,
                pin=namespace.pin,
                dry_run=namespace.dry_run,
            )
        if namespace.scratch_command == "record":
            values = (namespace.liveness, namespace.attempt_state, namespace.observed_at, namespace.capability, namespace.identity)
            if namespace.evidence is not None:
                if any(value is not None for value in values):
                    raise UsageError("conflicting_scratch_evidence", "--evidence cannot be combined with observation flags")
                evidence = load_scratch_evidence(namespace.evidence)
            else:
                if any(value is None for value in values):
                    raise UsageError("missing_scratch_evidence", "supply --evidence or all of --liveness, --attempt-state, --observed-at, --capability, --identity")
                evidence = {
                    "schema": 1, "task": str(Path(namespace.task).expanduser().resolve()),
                    "liveness": namespace.liveness, "attempt_state": namespace.attempt_state,
                    "observed_at": namespace.observed_at,
                    "source": {"capability": namespace.capability, "identity": namespace.identity},
                }
            return record_evidence(
                config,
                namespace.task,
                evidence,
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
        if item not in {"context", "stage", "promote", "scratch", "gc", "doctor", "storage", "retry"}:
            continue
        if item in {"stage", "scratch", "storage", "retry"} and index + 1 < len(arguments):
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
            if item.get("path") is not None:
                print(f"path: {item['path']}", file=sys.stderr)
            if item.get("details"):
                print(json.dumps(item["details"], ensure_ascii=False), file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    json_mode = "--json" in arguments
    command = _infer_command(arguments)
    try:
        namespace = build_parser().parse_args(arguments)
        json_mode = bool(getattr(namespace, "json", False))
        print_path = getattr(namespace, "print_path", None)
        if print_path and getattr(namespace, "dry_run", False):
            raise UsageError("path_unavailable_in_preview", "--print-path cannot be combined with --dry-run; use --json for previews")
        result = dispatch(namespace)
        if print_path:
            path = emit_success_path(result.envelope(), print_path)
            for item in redact_sensitive(result.warnings):
                print(f"warning [{item['code']}]: {item['message']}", file=sys.stderr)
            print(path)
        else:
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
