from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from runhand.cli import main
from runhand.config import Config, load_config
from runhand.context import scan_context
from runhand.copying import CopyPlan, select_entries
from runhand.doctor import run_doctor
from runhand.errors import PlanError, UnsafeError
from runhand.gc import collect, collect_orphan
from runhand.scratch import TASK_META, get_scratch
from runhand.stage import STAGE_META, create_stage, inspect_stage, promote_stage
from runhand.storage import atomic_write_json, ensure_owned_root, read_json


class WorkspaceCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        (self.workspace / ".git").mkdir()
        self.scratch = self.root / "scratch"
        self.state = self.root / "state"
        self.config = Config(
            workspace=self.workspace,
            scratch_root=self.scratch,
            state_root=self.state,
            warm_cache=False,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def source(self) -> Path:
        source = self.workspace / "base"
        (source / "input").mkdir(parents=True)
        (source / "empty").mkdir()
        (source / "input" / "case.toml").write_text("value = 1\n", encoding="utf-8")
        (source / "README.md").write_text("base run\n", encoding="utf-8")
        os.symlink("input/case.toml", source / "case-link")
        return source

    def plan(self, source: Path) -> CopyPlan:
        return CopyPlan(
            source=source.resolve(),
            include=("**",),
            exclude=(),
            symlink_policy="internal-relative",
            completeness="complete",
            basis={"kind": "user", "api_token": "do-not-store"},
        )


class ContextTests(WorkspaceCase):
    def test_scan_is_deterministic_bounded_and_never_executes_discoveries(self) -> None:
        marker = self.root / "executed"
        malicious = self.workspace / "run01" / "submit.sh"
        malicious.parent.mkdir()
        malicious.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
        malicious.chmod(0o755)
        (malicious.parent / "case.toml").write_text("x = 1\n", encoding="utf-8")

        first = scan_context(self.config, refresh=True).data
        second = scan_context(self.config, refresh=True).data

        self.assertEqual(first, second)
        self.assertFalse(marker.exists())
        self.assertEqual(
            first["candidate_runs"][0]["classification"], "structural_candidate"
        )

        limited = Config(
            workspace=self.workspace,
            scratch_root=self.scratch,
            state_root=self.state,
            context_max_entries=1,
            warm_cache=False,
        )
        data = scan_context(limited, refresh=True).data
        self.assertTrue(data["partial"])
        self.assertEqual(data["partial_reasons"][0]["code"], "max_entries")

    def test_cache_hit_refreshes_recent_and_unresolved_history(self) -> None:
        config = Config(
            workspace=self.workspace,
            scratch_root=self.scratch,
            state_root=self.state,
            warm_cache=True,
        )
        first = scan_context(config, refresh=False)
        self.assertEqual(first.data["cache"]["status"], "miss")

        history = self.state / "history"
        record = history / "submit-1.json"
        atomic_write_json(
            record,
            {
                "schema": 1,
                "owner": "runhand",
                "kind": "submission",
                "result": "unknown_unresolved",
                "target": str(self.workspace / "run01"),
                "recorded_at": "2026-09-04T00:00:00Z",
                "reconciled": False,
            },
        )

        second = scan_context(config, refresh=False)
        self.assertEqual(second.data["cache"]["status"], "hit")
        self.assertEqual(
            second.data["history"]["recent"][0]["result"], "unknown_unresolved"
        )
        self.assertEqual(second.data["history"]["unresolved"], [str(record)])
        self.assertIn(
            "unresolved_submission_evidence",
            {item["code"] for item in second.warnings},
        )

        doctor = run_doctor(config)
        self.assertEqual(doctor.data["unresolved_history"], [str(record)])


class StageTests(WorkspaceCase):
    def test_create_preserves_source_and_promotion_is_complete(self) -> None:
        source = self.source()
        before = sorted(
            path.relative_to(source).as_posix() for path in source.rglob("*")
        )
        result = create_stage(self.config, self.plan(source), dry_run=False)
        stage = Path(result.data["stage"])
        after = sorted(
            path.relative_to(source).as_posix() for path in source.rglob("*")
        )
        self.assertEqual(before, after)
        self.assertTrue((stage / "tree" / "empty").is_dir())

        metadata = read_json(stage / STAGE_META)
        self.assertEqual(metadata["copy_plan"]["basis"]["api_token"], "<redacted>")

        target = self.workspace / "run02"
        promoted = promote_stage(str(stage), str(target), dry_run=False)
        self.assertTrue(promoted.data["published"])
        self.assertEqual(
            (target / "input" / "case.toml").read_text(encoding="utf-8"), "value = 1\n"
        )
        self.assertTrue((target / "empty").is_dir())
        self.assertFalse((target / STAGE_META).exists())
        self.assertEqual(inspect_stage(str(stage)).data["state"], "promoted")

    def test_promotion_never_replaces_any_target_type(self) -> None:
        source = self.source()
        stage = Path(
            create_stage(self.config, self.plan(source), dry_run=False).data["stage"]
        )
        targets = {
            "file": lambda path: path.write_text("keep", encoding="utf-8"),
            "directory": lambda path: path.mkdir(),
            "symlink": lambda path: os.symlink("missing", path),
        }
        for name, create in targets.items():
            target = self.workspace / name
            create(target)
            with self.assertRaises(UnsafeError):
                promote_stage(str(stage), str(target), dry_run=False)
            if name == "file":
                self.assertEqual(target.read_text(encoding="utf-8"), "keep")

    def test_concurrent_promotion_has_exactly_one_winner(self) -> None:
        source = self.source()
        stage = Path(
            create_stage(self.config, self.plan(source), dry_run=False).data["stage"]
        )
        target = self.workspace / "race-target"

        def attempt() -> str:
            try:
                promote_stage(str(stage), str(target), dry_run=False)
                return "published"
            except UnsafeError:
                return "collision"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _: attempt(), range(2)))
        self.assertEqual(sorted(outcomes), ["collision", "published"])
        self.assertEqual(
            (target / "input" / "case.toml").read_text(encoding="utf-8"), "value = 1\n"
        )
        self.assertEqual(list(self.workspace.glob(".runhand-promote-*")), [])

    def test_source_change_aborts_and_cleans_temporary_stage(self) -> None:
        source = self.source()
        original_copy2 = __import__("shutil").copy2
        changed = False

        def changing_copy(source_path, destination_path, *, follow_symlinks=True):
            nonlocal changed
            result = original_copy2(
                source_path, destination_path, follow_symlinks=follow_symlinks
            )
            if not changed and Path(source_path).name == "README.md":
                Path(source_path).write_text("changed during copy\n", encoding="utf-8")
                changed = True
            return result

        with mock.patch("runhand.copying.shutil.copy2", side_effect=changing_copy):
            with self.assertRaises(PlanError) as raised:
                create_stage(self.config, self.plan(source), dry_run=False)
        self.assertEqual(raised.exception.code, "source_changed")
        self.assertEqual(list((self.scratch / "stages").iterdir()), [])

    def test_dry_run_does_not_create_managed_state(self) -> None:
        source = self.source()
        result = create_stage(self.config, self.plan(source), dry_run=True)
        self.assertTrue(result.data["dry_run"])
        self.assertFalse(self.scratch.exists())
        self.assertFalse(self.state.exists())

    def test_external_dangling_and_uncopied_symlinks_are_rejected(self) -> None:
        source = self.workspace / "source"
        source.mkdir()
        outside = self.workspace / "outside"
        outside.write_text("outside", encoding="utf-8")

        os.symlink("../outside", source / "external")
        with self.assertRaises(PlanError) as raised:
            select_entries(self.plan(source))
        self.assertEqual(raised.exception.code, "external_symlink")
        (source / "external").unlink()

        os.symlink("missing", source / "dangling")
        with self.assertRaises(PlanError) as raised:
            select_entries(self.plan(source))
        self.assertEqual(raised.exception.code, "dangling_symlink")
        (source / "dangling").unlink()

        target = source / "target.txt"
        target.write_text("x", encoding="utf-8")
        os.symlink("target.txt", source / "link")
        partial = CopyPlan(
            source.resolve(), ("link",), (), "internal-relative", "partial", None
        )
        with self.assertRaises(PlanError) as raised:
            select_entries(partial)
        self.assertEqual(raised.exception.code, "uncopied_symlink_target")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO unsupported")
    def test_selected_special_file_is_rejected(self) -> None:
        source = self.workspace / "source"
        source.mkdir()
        os.mkfifo(source / "pipe")
        with self.assertRaises(PlanError) as raised:
            select_entries(self.plan(source))
        self.assertEqual(raised.exception.code, "special_file")


class ScratchAndGCTests(WorkspaceCase):
    def test_key_is_a_hint_and_unknown_liveness_is_protected(self) -> None:
        first = get_scratch(
            self.config,
            kind="analysis",
            key="same-secret-key",
            pin=False,
            dry_run=False,
        )
        second = get_scratch(
            self.config,
            kind="analysis",
            key="same-secret-key",
            pin=False,
            dry_run=False,
        )
        first_path = Path(first.data["task"])
        second_path = Path(second.data["task"])
        self.assertNotEqual(first_path, second_path)
        self.assertEqual(second.data["decision"], "new_unique_task")
        self.assertEqual(len(second.data["reuse_candidates"]), 1)
        self.assertNotIn(
            "same-secret-key", (first_path / TASK_META).read_text(encoding="utf-8")
        )

        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        meta = read_json(first_path / TASK_META)
        meta["last_used_at"] = old
        atomic_write_json(first_path / TASK_META, meta)
        preview = collect(self.config, kind="scratch", older_than="1d", apply=False)
        protected = {item["path"]: item["reason"] for item in preview.data["protected"]}
        self.assertEqual(protected[str(first_path)], "liveness_unknown")
        self.assertTrue(first_path.exists())

        applied = collect(self.config, kind="scratch", older_than="1d", apply=True)
        self.assertTrue(first_path.exists())
        self.assertEqual(applied.data["deleted"], [])

        orphan_preview = collect_orphan(
            self.config, task_arg=str(first_path), apply=False
        )
        self.assertTrue(orphan_preview.data["requires_site_recheck"])
        self.assertTrue(first_path.exists())
        collect_orphan(self.config, task_arg=str(first_path), apply=True)
        self.assertFalse(first_path.exists())

    def test_ready_stage_is_protected_and_old_promoted_stage_is_deleted(self) -> None:
        source = self.source()
        stage = Path(
            create_stage(self.config, self.plan(source), dry_run=False).data["stage"]
        )
        meta = read_json(stage / STAGE_META)
        meta["last_used_at"] = (
            datetime.now(timezone.utc) - timedelta(days=30)
        ).isoformat()
        atomic_write_json(stage / STAGE_META, meta)
        preview = collect(self.config, kind="scratch", older_than="1d", apply=False)
        self.assertEqual(preview.data["candidates"], [])
        self.assertEqual(
            {item["path"]: item["reason"] for item in preview.data["protected"]}[
                str(stage)
            ],
            "nonterminal",
        )
        self.assertTrue(stage.exists())
        meta["state"] = "promoted"
        atomic_write_json(stage / STAGE_META, meta)
        collect(self.config, kind="scratch", older_than="1d", apply=True)
        self.assertFalse(stage.exists())

    def test_gc_protects_foreign_child_metadata(self) -> None:
        ensure_owned_root(self.scratch, "scratch")
        ensure_owned_root(self.state, "state")
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

        foreign_stage = self.scratch / "stages" / "stage-foreign"
        foreign_stage.mkdir(parents=True)
        atomic_write_json(
            foreign_stage / STAGE_META,
            {"schema": 1, "state": "promoted", "last_used_at": old},
        )
        foreign_task = self.scratch / "tasks" / "analysis" / "task-foreign"
        foreign_task.mkdir(parents=True)
        atomic_write_json(
            foreign_task / TASK_META,
            {
                "schema": 1,
                "kind": "analysis",
                "liveness": "terminal",
                "attempt_state": "none",
                "last_used_at": old,
            },
        )
        foreign_cache = self.state / "cache" / "foreign.json"
        atomic_write_json(foreign_cache, {"schema": 1, "kind": "cache"})
        foreign_history = self.state / "history" / "foreign.json"
        atomic_write_json(
            foreign_history,
            {"schema": 1, "result": "rejected", "reconciled": True},
        )
        owned_history = self.state / "history" / "owned.json"
        atomic_write_json(
            owned_history,
            {
                "schema": 1,
                "owner": "runhand",
                "kind": "submission",
                "result": "rejected",
                "reconciled": True,
            },
        )
        active_history = self.state / "history" / "active.json"
        atomic_write_json(
            active_history,
            {
                "schema": 1,
                "owner": "runhand",
                "kind": "submission",
                "result": "accepted",
                "reconciled": True,
                "attempt_state": "active",
            },
        )
        old_timestamp = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
        for path in (
            foreign_cache,
            foreign_history,
            owned_history,
            active_history,
        ):
            os.utime(path, (old_timestamp, old_timestamp))

        result = collect(self.config, kind="all", older_than="1d", apply=True)
        for path in (
            foreign_stage,
            foreign_task,
            foreign_cache,
            foreign_history,
            active_history,
        ):
            self.assertTrue(path.exists())
        self.assertFalse(owned_history.exists())
        protected = {item["path"]: item["reason"] for item in result.data["protected"]}
        self.assertEqual(protected[str(foreign_stage)], "metadata_unknown")
        self.assertEqual(protected[str(foreign_task)], "metadata_unknown")
        self.assertEqual(protected[str(foreign_cache)], "cache_incompatible")
        self.assertEqual(protected[str(foreign_history)], "history_incompatible")
        self.assertEqual(protected[str(active_history)], "history_nonterminal")

    def test_managed_container_symlinks_never_escape_owned_roots(self) -> None:
        source = self.source()
        outside_root = self.root / "outside-root"
        outside_root.mkdir()
        symlink_root = self.root / "scratch-root-link"
        os.symlink(outside_root, symlink_root)
        with self.assertRaises(UnsafeError):
            ensure_owned_root(symlink_root, "scratch")

        ensure_owned_root(self.scratch, "scratch")
        ensure_owned_root(self.state, "state")
        outside = self.root / "outside"
        outside_stages = outside / "stages"
        outside_tasks = outside / "tasks"
        outside_cache = outside / "cache"
        outside_history = outside / "history"
        for path in (
            outside_stages,
            outside_tasks,
            outside_cache,
            outside_history,
        ):
            path.mkdir(parents=True)
        os.symlink(outside_stages, self.scratch / "stages")
        os.symlink(outside_tasks, self.scratch / "tasks")
        os.symlink(outside_cache, self.state / "cache")
        os.symlink(outside_history, self.state / "history")

        with self.assertRaises(UnsafeError):
            create_stage(self.config, self.plan(source), dry_run=False)
        with self.assertRaises(UnsafeError):
            get_scratch(
                self.config,
                kind="analysis",
                key="escape",
                pin=False,
                dry_run=False,
            )

        context = scan_context(
            Config(
                workspace=self.workspace,
                scratch_root=self.scratch,
                state_root=self.state,
                warm_cache=True,
            ),
            refresh=True,
        )
        self.assertIn(
            "context_cache_write_failed", {item["code"] for item in context.warnings}
        )
        self.assertIn(
            "history_unavailable", {item["code"] for item in context.warnings}
        )

        result = collect(self.config, kind="all", older_than="0s", apply=True)
        protected = {item["path"]: item["reason"] for item in result.data["protected"]}
        self.assertEqual(protected[str(self.scratch / "stages")], "unsafe_container")
        self.assertEqual(protected[str(self.scratch / "tasks")], "unsafe_container")
        self.assertEqual(protected[str(self.state / "cache")], "unsafe_container")
        self.assertEqual(protected[str(self.state / "history")], "unsafe_container")
        for path in (
            outside_stages,
            outside_tasks,
            outside_cache,
            outside_history,
        ):
            self.assertEqual(list(path.iterdir()), [])


class ContractTests(WorkspaceCase):
    def test_cli_accepts_copy_plan_on_stdin_and_promotes(self) -> None:
        source = self.source()
        plan = {
            "schema": 1,
            "source": str(source),
            "include": ["**"],
            "exclude": [],
            "symlink_policy": "internal-relative",
            "completeness": "complete",
            "basis": {"kind": "user", "api_token": "do-not-emit"},
        }
        stdout = io.StringIO()
        with (
            mock.patch.object(sys, "stdin", io.StringIO(json.dumps(plan))),
            contextlib.redirect_stdout(stdout),
        ):
            code = main(
                [
                    "--workspace",
                    str(self.workspace),
                    "--scratch-root",
                    str(self.scratch),
                    "--state-root",
                    str(self.state),
                    "stage",
                    "create",
                    "--source",
                    str(source),
                    "--plan",
                    "-",
                    "--json",
                ]
            )
        self.assertEqual(code, 0)
        self.assertNotIn("do-not-emit", stdout.getvalue())
        self.assertIn("<redacted>", stdout.getvalue())
        stage = json.loads(stdout.getvalue())["data"]["stage"]

        target = self.workspace / "cli-promoted"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(
                [
                    "--workspace",
                    str(self.workspace),
                    "--scratch-root",
                    str(self.scratch),
                    "--state-root",
                    str(self.state),
                    "promote",
                    stage,
                    str(target),
                    "--json",
                ]
            )
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(stdout.getvalue())["data"]["published"])
        self.assertTrue((target / "input" / "case.toml").is_file())

    def test_config_precedence_and_invalid_config(self) -> None:
        (self.workspace / "runhand.toml").write_text(
            "version = 1\n[context]\nmax_depth = 3\n[scratch]\nttl_days = 10\n",
            encoding="utf-8",
        )
        env = {
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "XDG_STATE_HOME": str(self.root / "xdg-state"),
            "XDG_CACHE_HOME": str(self.root / "xdg-cache"),
            "RUNHAND_CONTEXT_MAX_DEPTH": "5",
            "RUNHAND_SCRATCH_ROOT": str(self.root / "env-scratch"),
        }
        global_config = self.root / "config" / "runhand" / "config.toml"
        global_config.parent.mkdir(parents=True)
        global_config.write_text(
            "version = 1\n[context]\nmax_depth = 2\n", encoding="utf-8"
        )
        config = load_config(
            workspace_arg=str(self.workspace),
            context_path=None,
            scratch_root_arg=str(self.root / "cli-scratch"),
            state_root_arg=None,
            environ=env,
        )
        self.assertEqual(config.context_max_depth, 5)
        self.assertEqual(config.scratch_ttl_days, 10)
        self.assertEqual(config.scratch_root, (self.root / "cli-scratch").resolve())

        (self.workspace / "runhand.toml").write_text("version = 2\n", encoding="utf-8")
        with self.assertRaises(Exception) as raised:
            load_config(
                workspace_arg=str(self.workspace),
                context_path=None,
                scratch_root_arg=None,
                state_root_arg=None,
                environ=env,
            )
        self.assertEqual(raised.exception.exit_code, 2)

        (self.workspace / "runhand.toml").write_text("version = 1\n", encoding="utf-8")
        with self.assertRaises(Exception) as raised:
            load_config(
                workspace_arg=str(self.workspace),
                context_path=None,
                scratch_root_arg=str(self.root / "same-root"),
                state_root_arg=str(self.root / "same-root"),
                environ={"XDG_CONFIG_HOME": str(self.root / "empty-config")},
            )
        self.assertEqual(raised.exception.code, "conflicting_managed_roots")

        invalid_configs = (
            "version = true\n",
            "version = 1\n[behavior]\nverification = []\n",
            "version = 1\n[scratch]\nroot = 42\n",
        )
        for content in invalid_configs:
            with self.subTest(content=content):
                (self.workspace / "runhand.toml").write_text(content, encoding="utf-8")
                with self.assertRaises(Exception) as invalid:
                    load_config(
                        workspace_arg=str(self.workspace),
                        context_path=None,
                        scratch_root_arg=None,
                        state_root_arg=None,
                        environ={"XDG_CONFIG_HOME": str(self.root / "empty-config")},
                    )
                self.assertEqual(invalid.exception.exit_code, 2)

    def test_bulk_gc_skips_unowned_roots(self) -> None:
        unowned = self.root / "unowned"
        fake = unowned / "stages" / "stage-fake"
        fake.mkdir(parents=True)
        (fake / STAGE_META).write_text(
            json.dumps(
                {"schema": 1, "owner": "runhand", "kind": "stage", "state": "promoted"}
            ),
            encoding="utf-8",
        )
        config = Config(
            workspace=self.workspace, scratch_root=unowned, state_root=self.state
        )
        result = collect(config, kind="scratch", older_than="0s", apply=True)
        self.assertTrue(fake.exists())
        self.assertEqual(result.data["deleted"], [])
        self.assertEqual(result.warnings[0]["code"], "unowned_scratch_skipped")

    def test_json_usage_error_is_one_object(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["context", "--not-an-option", "--json"])
        self.assertEqual(code, 2)
        lines = stdout.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        value = json.loads(lines[0])
        self.assertFalse(value["ok"])
        self.assertEqual(value["schema"], 1)
        self.assertEqual(value["errors"][0]["code"], "usage_error")
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
