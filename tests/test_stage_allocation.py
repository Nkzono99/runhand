from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from runhand.config import Config
from runhand.copying import CopyPlan, copy_entries
from runhand.errors import LocalIOError, PlanError, UnsafeError
from runhand.gc import collect, collect_orphan
from runhand.stage import STAGE_META, create_stage, inspect_stage, promote_stage
from runhand.storage import read_json


class StageAllocationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "input.toml").write_text("temperature = 300\n", encoding="utf-8")
        self.config = Config(
            workspace=self.root,
            scratch_root=self.root / "scratch",
            state_root=self.root / "state",
            warm_cache=False,
        )
        self.plan = CopyPlan(
            self.source, ("input.toml",), (), "internal-relative", "complete", None
        )

    def test_stage_allocation_does_not_require_formal_publication(self) -> None:
        with mock.patch(
            "runhand.stage._rename_noreplace",
            side_effect=AssertionError("private stage must not use formal publication"),
        ):
            result = create_stage(self.config, self.plan, dry_run=False)
        stage = Path(result.data["stage"])
        self.assertEqual(result.data["state"], "ready")
        self.assertEqual(read_json(stage / STAGE_META)["state"], "ready")
        self.assertEqual((stage / "tree" / "input.toml").read_text(), "temperature = 300\n")
        self.assertEqual(list((self.config.scratch_root / "stages").iterdir()), [stage])

    def test_copy_in_progress_is_inspectable_protected_and_not_promotable(self) -> None:
        def paused_copy(plan, entries, tree):
            stage = tree.parent
            inspected = inspect_stage(str(stage))
            self.assertEqual(inspected.data["state"], "incomplete")
            self.assertEqual(inspected.warnings[0]["code"], "incomplete_stage")
            self.assertFalse(tree.exists())
            gc_result = collect(self.config, kind="scratch", older_than="0s", apply=True)
            self.assertEqual(gc_result.data["deleted"], [])
            self.assertTrue(stage.exists())
            with self.assertRaises(PlanError) as preview:
                promote_stage(str(stage), str(self.root / "preview-target"), dry_run=True)
            self.assertEqual(preview.exception.code, "stage_not_ready")
            for operation in (
                lambda: promote_stage(str(stage), str(self.root / "target"), dry_run=False),
                lambda: collect_orphan(self.config, task_arg=str(stage), apply=True),
            ):
                with self.assertRaises(LocalIOError) as busy:
                    operation()
                self.assertEqual(busy.exception.code, "managed_root_busy")
            copy_entries(plan, entries, tree)

        with mock.patch("runhand.stage.copy_entries", side_effect=paused_copy):
            result = create_stage(self.config, self.plan, dry_run=False)
        self.assertEqual(result.data["state"], "ready")
        self.assertFalse((self.root / "target").exists())
        self.assertFalse((self.root / "preview-target").exists())

    def test_failed_copy_cleans_only_the_directory_it_allocated(self) -> None:
        complete = Path(create_stage(self.config, self.plan, dry_run=False).data["stage"])
        original_metadata = (complete / STAGE_META).read_bytes()
        with mock.patch(
            "runhand.stage.copy_entries", side_effect=PlanError("source_changed", "fixture changed")
        ):
            with self.assertRaises(PlanError):
                create_stage(self.config, self.plan, dry_run=False)
        self.assertEqual(list((self.config.scratch_root / "stages").iterdir()), [complete])
        self.assertEqual((complete / STAGE_META).read_bytes(), original_metadata)

    def test_uuid_collision_does_not_remove_or_modify_the_existing_stage(self) -> None:
        stage = Path(create_stage(self.config, self.plan, dry_run=False).data["stage"])
        original_metadata = (stage / STAGE_META).read_bytes()
        with mock.patch("runhand.stage._stage_id", return_value=stage.name):
            with self.assertRaises(UnsafeError) as collision:
                create_stage(self.config, self.plan, dry_run=False)
        self.assertEqual(collision.exception.code, "stage_exists")
        self.assertEqual((stage / STAGE_META).read_bytes(), original_metadata)
        self.assertEqual((stage / "tree" / "input.toml").read_text(), "temperature = 300\n")

    def test_incomplete_stage_left_after_failed_cleanup_stays_protected(self) -> None:
        def partial_copy(plan, entries, tree):
            tree.mkdir()
            (tree / "partial").write_text("unfinished", encoding="utf-8")
            raise PlanError("source_changed", "fixture changed")

        with mock.patch("runhand.stage.copy_entries", side_effect=partial_copy), mock.patch(
            "runhand.stage.shutil.rmtree", return_value=None
        ):
            with self.assertRaises(PlanError):
                create_stage(self.config, self.plan, dry_run=False)
        stage = next((self.config.scratch_root / "stages").iterdir())
        self.assertEqual(inspect_stage(str(stage)).data["state"], "incomplete")
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run), self.assertRaises(PlanError) as rejected:
                promote_stage(str(stage), str(self.root / "target"), dry_run=dry_run)
            self.assertEqual(rejected.exception.code, "stage_not_ready")
        result = collect(self.config, kind="scratch", older_than="0s", apply=True)
        self.assertEqual(result.data["deleted"], [])
        self.assertTrue(stage.exists())
        self.assertFalse((self.root / "target").exists())

    def test_formal_publication_still_fails_without_no_replace_support(self) -> None:
        failure = LocalIOError("atomic_publish_failed", "fixture storage lacks no-replace")
        with mock.patch("runhand.stage._rename_noreplace", side_effect=failure) as publish:
            stage = Path(create_stage(self.config, self.plan, dry_run=False).data["stage"])
            publish.assert_not_called()
            with self.assertRaises(LocalIOError) as rejected:
                promote_stage(str(stage), str(self.root / "target"), dry_run=False)
            self.assertEqual(rejected.exception.code, "atomic_publish_failed")
            publish.assert_called_once()
        self.assertEqual(inspect_stage(str(stage)).data["state"], "ready")
        self.assertFalse((self.root / "target").exists())
        self.assertEqual(list(self.root.glob(".runhand-promote-*")), [])


if __name__ == "__main__":
    unittest.main()
