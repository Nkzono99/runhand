from __future__ import annotations

import contextlib
import io
import json
import multiprocessing
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from runhand import gc as gc_module
from runhand.cli import main
from runhand.config import Config
from runhand.errors import LocalIOError, PlanError, UnsafeError
from runhand.gc import collect, collect_orphan
from runhand.scratch import TASK_META, get_scratch, record_evidence, set_pin
from runhand.storage import atomic_write_json, ensure_owned_root, managed_root_lock, read_json


def _hold_task_lock(root: str, target: str, ready: object, release: object) -> None:
    with managed_root_lock(Path(root), "scratch", target=Path(target)):
        ready.set()
        if not release.wait(15):
            raise RuntimeError("parent did not release the task lock")


class ScratchLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.scratch = self.root / "scratch"
        self.state = self.root / "state"
        self.config = Config(
            workspace=self.workspace,
            scratch_root=self.scratch,
            state_root=self.state,
            warm_cache=False,
        )

    def task(self) -> Path:
        result = get_scratch(
            self.config, kind="smoke", key="case", pin=False, dry_run=False
        )
        return Path(result.data["task"])

    def evidence(self, task_path: Path, **changes: object) -> dict[str, object]:
        value = {
            "schema": 1,
            "task": str(task_path),
            "observed_at": "2026-09-05T00:00:00Z",
            "liveness": "terminal",
            "attempt_state": "terminal",
            "source": {"capability": "site", "identity": "fixture-site/v1"},
        }
        value.update(changes)
        return value

    def terminal_task(self) -> Path:
        task = self.task()
        record_evidence(self.config, str(task), self.evidence(task), dry_run=False)
        return task

    def snapshot(self) -> dict[str, bytes | None]:
        return {
            str(path.relative_to(self.root)): path.read_bytes() if path.is_file() else None
            for path in self.root.rglob("*")
        }

    def test_record_stdin_completes_task_and_enables_normal_gc(self) -> None:
        task = self.task()
        evidence = self.evidence(task, details={"job_id": "123", "api_token": "do-not-store"})
        output = io.StringIO()
        with mock.patch("runhand.cli._config", return_value=self.config), mock.patch(
            "sys.stdin", io.StringIO(json.dumps(evidence))
        ), contextlib.redirect_stdout(output):
            status = main(["scratch", "record", str(task), "--evidence", "-", "--json"])
        self.assertEqual(status, 0)
        receipt = json.loads(output.getvalue())
        self.assertEqual(receipt["command"], "scratch record")
        self.assertEqual(receipt["data"]["liveness"], "terminal")
        self.assertNotIn("do-not-store", (task / TASK_META).read_text())
        self.assertEqual(read_json(task / TASK_META)["evidence"]["details"]["api_token"], "<redacted>")
        result = collect(self.config, kind="scratch", older_than="0s", apply=True)
        self.assertEqual(result.data["deleted"], [str(task)])
        self.assertFalse(task.exists())

    def test_dry_runs_and_preview_do_not_create_lock_or_change_state(self) -> None:
        task = self.task()
        before = self.snapshot()
        record_evidence(self.config, str(task), self.evidence(task), dry_run=True)
        set_pin(self.config, str(task), pinned=True, dry_run=True)
        collect(self.config, kind="scratch", older_than="0s", apply=False)
        self.assertEqual(before, self.snapshot())
        self.assertFalse((self.scratch / ".runhand-locks").exists())

    def test_invalid_evidence_is_rejected_without_writes(self) -> None:
        task = self.task()
        cases = [
            {"schema": True},
            {"task": str(self.root / "other")},
            {"task": "relative"},
            {"attempt_state": None},
            {"liveness": "success"},
            {"observed_at": "2026-09-05T00:00:00"},
            {"attempt_state": "active"},
            {"source": {"capability": "simulator", "identity": "fixture"}},
            {"details": "raw text"},
            {"unexpected": True},
        ]
        before = self.snapshot()
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(PlanError):
                record_evidence(self.config, str(task), self.evidence(task, **changes), dry_run=False)
            self.assertEqual(before, self.snapshot())

    def test_observation_order_and_site_ownership_protect_running_task(self) -> None:
        task = self.task()
        active = self.evidence(task, liveness="active", attempt_state="active", details={"token": "secret"})
        record_evidence(self.config, str(task), active, dry_run=False)
        # Repeating the same redacted observation is allowed.
        record_evidence(self.config, str(task), active, dry_run=False)
        before = (task / TASK_META).read_bytes()
        stale = self.evidence(task, observed_at="2026-09-04T00:00:00Z")
        conflicting = self.evidence(task)
        simulator = self.evidence(
            task, observed_at="2026-09-06T00:00:00Z", attempt_state="none",
            source={"capability": "simulator", "identity": "fixture"},
        )
        for evidence in (stale, conflicting, simulator):
            with self.subTest(evidence=evidence), self.assertRaises(PlanError):
                record_evidence(self.config, str(task), evidence, dry_run=False)
            self.assertEqual(before, (task / TASK_META).read_bytes())
        with self.assertRaises(UnsafeError):
            collect_orphan(self.config, task_arg=str(task), apply=True)
        terminal = self.evidence(task, observed_at="2026-09-06T00:00:00Z")
        record_evidence(self.config, str(task), terminal, dry_run=False)
        result = collect(self.config, kind="scratch", older_than="0s", apply=True)
        self.assertEqual(result.data["deleted"], [str(task)])

    def test_gc_rechecks_pin_after_initial_selection(self) -> None:
        task = self.terminal_task()
        select = gc_module._task_candidates

        def select_then_pin(*args: object) -> object:
            selected = select(*args)
            set_pin(self.config, str(task), pinned=True, dry_run=False)
            return selected

        with mock.patch.object(gc_module, "_task_candidates", side_effect=select_then_pin):
            result = collect(self.config, kind="scratch", older_than="0s", apply=True)
        self.assertEqual(result.data["deleted"], [])
        self.assertEqual(result.data["protected"][0]["reason"], "pinned")
        self.assertTrue(task.exists())

    def test_gc_rechecks_new_active_evidence_after_initial_selection(self) -> None:
        task = self.terminal_task()
        select = gc_module._task_candidates

        def select_then_record(*args: object) -> object:
            selected = select(*args)
            record_evidence(
                self.config, str(task),
                self.evidence(task, liveness="active", attempt_state="active", observed_at="2026-09-06T00:00:00Z"),
                dry_run=False,
            )
            return selected

        with mock.patch.object(gc_module, "_task_candidates", side_effect=select_then_record):
            result = collect(self.config, kind="scratch", older_than="0s", apply=True)
        self.assertEqual(result.data["deleted"], [])
        self.assertEqual(read_json(task / TASK_META)["liveness"], "active")

    def test_gc_and_writers_share_cross_process_target_lock(self) -> None:
        task = self.terminal_task()
        other = self.task()
        context = multiprocessing.get_context("spawn")
        ready, release = context.Event(), context.Event()
        process = context.Process(
            target=_hold_task_lock, args=(str(self.scratch), str(task), ready, release)
        )
        process.start()
        try:
            self.assertTrue(ready.wait(10), "child failed to acquire task lock")
            result = collect(self.config, kind="scratch", older_than="0s", apply=True)
            reasons = {item["path"]: item["reason"] for item in result.data["protected"]}
            self.assertEqual(reasons[str(task)], "busy")
            self.assertTrue(task.exists())
            for operation in (
                lambda: set_pin(self.config, str(task), pinned=True, dry_run=False),
                lambda: record_evidence(self.config, str(task), self.evidence(task), dry_run=False),
            ):
                with self.assertRaises(LocalIOError) as raised:
                    operation()
                self.assertEqual(raised.exception.code, "managed_root_busy")
            # An unrelated task remains usable while promotion/GC owns this one.
            set_pin(self.config, str(other), pinned=True, dry_run=False)
        finally:
            release.set()
            process.join(10)
            if process.is_alive():
                process.terminate()
                process.join(5)
        self.assertEqual(process.exitcode, 0)

    def test_writer_rechecks_after_gc_and_does_not_resurrect_task(self) -> None:
        for action in ("pin", "record"):
            with self.subTest(action=action):
                task = self.terminal_task()

                @contextlib.contextmanager
                def delete_before_writer_lock(*args: object, **kwargs: object) -> object:
                    result = collect(self.config, kind="scratch", older_than="0s", apply=True)
                    self.assertIn(str(task), result.data["deleted"])
                    with managed_root_lock(*args, **kwargs):
                        yield

                with mock.patch("runhand.scratch.managed_root_lock", delete_before_writer_lock):
                    with self.assertRaises(PlanError):
                        if action == "pin":
                            set_pin(self.config, str(task), pinned=True, dry_run=False)
                        else:
                            record_evidence(self.config, str(task), self.evidence(task), dry_run=False)
                self.assertFalse(task.exists())

    def test_missing_attempt_evidence_and_retired_history_stay_untouched(self) -> None:
        task = self.terminal_task()
        meta = read_json(task / TASK_META)
        del meta["attempt_state"]
        atomic_write_json(task / TASK_META, meta)
        ensure_owned_root(self.state, "state")
        histories = []
        old = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
        for index, (result, attempt) in enumerate((
            ("unknown_unresolved", "terminal"),
            ("rejected", "unresolved"),
            ("rejected", "active"),
        )):
            path = self.state / "history" / f"record-{index}.json"
            atomic_write_json(path, {
                "schema": 1, "owner": "runhand", "kind": "submission",
                "result": result, "attempt_state": attempt, "reconciled": True,
            })
            os.utime(path, (old, old))
            histories.append(path)
        result = collect(self.config, kind="all", older_than="0s", apply=True)
        self.assertEqual(result.data["deleted"], [])
        self.assertTrue(task.exists())
        self.assertTrue(all(path.exists() for path in histories))

    def test_reconciliation_and_unpin_do_not_restart_retention(self) -> None:
        task = self.task()
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        meta = read_json(task / TASK_META)
        meta.update(last_used_at=old, pinned=True)
        atomic_write_json(task / TASK_META, meta)
        record_evidence(self.config, str(task), self.evidence(
            task, observed_at=datetime.now(timezone.utc).isoformat()
        ), dry_run=False)
        set_pin(self.config, str(task), pinned=False, dry_run=False)
        self.assertEqual(read_json(task / TASK_META)["last_used_at"], old)
        preview = collect(self.config, kind="scratch", older_than=None, apply=False)
        self.assertEqual([item["path"] for item in preview.data["candidates"]], [str(task)])

    def test_active_observation_updates_use_time_without_regressing_it(self) -> None:
        task = self.task()
        meta = read_json(task / TASK_META)
        meta["last_used_at"] = "2026-08-01T00:00:00Z"
        atomic_write_json(task / TASK_META, meta)
        record_evidence(self.config, str(task), self.evidence(
            task, liveness="active", attempt_state="active"
        ), dry_run=False)
        self.assertEqual(read_json(task / TASK_META)["last_used_at"], "2026-09-05T00:00:00Z")
        record_evidence(self.config, str(task), self.evidence(
            task, observed_at="2026-09-06T00:00:00Z"
        ), dry_run=False)
        self.assertEqual(read_json(task / TASK_META)["last_used_at"], "2026-09-05T00:00:00Z")

    def test_allocation_needs_no_key_or_scan_and_preserves_uuid_collision(self) -> None:
        ensure_owned_root(self.scratch, "scratch")
        with mock.patch.object(Path, "iterdir", side_effect=AssertionError("unexpected task discovery")):
            task = Path(get_scratch(self.config, kind="smoke", pin=False, dry_run=False).data["task"])
        before = (task / TASK_META).read_bytes()
        with mock.patch("runhand.scratch.uuid.uuid4") as uuid:
            uuid.return_value.hex = task.name.removeprefix("task-")
            with self.assertRaises(FileExistsError):
                get_scratch(self.config, kind="smoke", pin=False, dry_run=False)
        self.assertEqual((task / TASK_META).read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
