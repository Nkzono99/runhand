from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import errno
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest import mock

from runhand.config import Config
from runhand.copying import CopyPlan, copy_entries
from runhand.errors import LocalIOError, PlanError, RunHandError, UnsafeError
from runhand.gc import collect
from runhand.publication import symlink_noreplace
from runhand.stage import PUBLICATION_META, STAGE_META, create_stage, inspect_stage, promote_stage
from runhand.storage import atomic_write_json, read_json


class PublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "input.toml").write_text("steps = 20\n", encoding="utf-8")
        (self.source / "alias").symlink_to("input.toml")
        self.parent = self.root / "runs"
        self.parent.mkdir()
        self.target = self.parent / "new case"
        self.config = Config(
            workspace=self.root, scratch_root=self.root / "scratch",
            state_root=self.root / "state", warm_cache=False,
        )
        self.plan = CopyPlan(self.source, ("**",), (), "internal-relative", "complete", None)
        self.stage = Path(create_stage(self.config, self.plan, dry_run=False).data["stage"])

    def promote(self, **kwargs):
        return promote_stage(str(self.stage), str(self.target), dry_run=False, publish_mode="symlink", **kwargs)

    def test_unsupported_atomic_storage_is_detected_before_copy(self) -> None:
        before = (self.stage / STAGE_META).read_bytes()
        with mock.patch("runhand.probes.rename_noreplace", side_effect=OSError(errno.EINVAL, "Invalid argument")), mock.patch(
            "runhand.stage.copy_entries"
        ) as copy, mock.patch("runhand.stage._scan_tree_as_plan") as scan:
            with self.assertRaises(LocalIOError) as raised:
                promote_stage(str(self.stage), str(self.target), dry_run=False)
            copy.assert_not_called()
            scan.assert_not_called()
        self.assertEqual(raised.exception.code, "publication_unavailable")
        self.assertEqual(raised.exception.details["check"]["error"]["errno"], errno.EINVAL)
        self.assertEqual(raised.exception.path, self.parent)
        self.assertEqual(list(self.parent.iterdir()), [])
        self.assertEqual((self.stage / STAGE_META).read_bytes(), before)

    def test_symlink_is_independent_durable_and_relocatable_with_its_sibling(self) -> None:
        with mock.patch("runhand.probes.rename_noreplace", side_effect=OSError(errno.EINVAL, "Invalid argument")), mock.patch(
            "runhand.stage._rename_noreplace", side_effect=AssertionError("no rename fallback")
        ):
            result = self.promote()
        backing = Path(result.data["backing_path"])
        self.assertTrue(self.target.is_symlink())
        self.assertFalse(Path(os.readlink(self.target)).is_absolute())
        self.assertEqual(self.target.resolve(), backing)
        self.assertEqual(backing.parent.parent, self.parent)
        self.assertEqual(read_json(backing.parent / PUBLICATION_META)["state"], "published")
        self.assertFalse((self.target / PUBLICATION_META).exists())
        self.assertEqual(inspect_stage(str(self.stage)).data["promotions"][0]["backing_path"], str(backing))
        result = collect(self.config, kind="scratch", older_than="0s", apply=True)
        self.assertIn(str(self.stage), result.data["deleted"])
        shutil.rmtree(self.source)
        shutil.rmtree(self.config.state_root, ignore_errors=True)
        self.assertEqual((self.target / "alias").read_text(), "steps = 20\n")
        relocated = self.root / "archived"
        self.parent.rename(relocated)
        self.assertEqual((relocated / self.target.name / "alias").read_text(), "steps = 20\n")

    def test_existing_targets_of_every_type_are_unchanged(self) -> None:
        for kind in ("file", "empty-directory", "directory", "dangling-link", "link"):
            with self.subTest(kind=kind):
                target = self.parent / kind
                if kind == "file":
                    target.write_text("keep")
                elif kind in {"empty-directory", "directory"}:
                    target.mkdir()
                    if kind == "directory":
                        (target / "sentinel").write_text("keep")
                else:
                    target.symlink_to("missing" if kind == "dangling-link" else self.source)
                inode = target.lstat().st_ino
                with mock.patch("runhand.stage.copy_entries") as copy, self.assertRaises(UnsafeError) as raised:
                    promote_stage(str(self.stage), str(target), dry_run=False, publish_mode="symlink")
                self.assertEqual(raised.exception.code, "target_exists")
                copy.assert_not_called()
                self.assertEqual(target.lstat().st_ino, inode)
        self.assertEqual(list(self.parent.glob(".runhand-run-*")), [])

    def test_backing_container_preserves_group_and_other_access(self) -> None:
        (self.stage / "tree").chmod(0o750)
        result = self.promote()
        backing = Path(result.data["backing_path"])
        self.assertEqual(backing.stat().st_mode & 0o777, 0o750)
        self.assertEqual(backing.parent.stat().st_mode & 0o777, 0o750)

    def test_target_appearing_after_copy_is_preserved(self) -> None:
        def collided(source, target):
            target.mkdir()
            (target / "sentinel").write_text("concurrent writer")
            symlink_noreplace(source, target)

        with mock.patch("runhand.stage.symlink_noreplace", side_effect=collided):
            with self.assertRaises(UnsafeError) as raised:
                self.promote()
        self.assertEqual(raised.exception.code, "target_exists")
        self.assertEqual((self.target / "sentinel").read_text(), "concurrent writer")
        self.assertEqual(list(self.parent.glob(".runhand-run-*")), [])
        self.assertEqual(inspect_stage(str(self.stage)).data["state"], "ready")

    def test_two_distinct_stages_have_exactly_one_publication_winner(self) -> None:
        second = Path(create_stage(self.config, self.plan, dry_run=False).data["stage"])
        barrier = threading.Barrier(2)

        def synchronized_copy(plan, entries, destination):
            copy_entries(plan, entries, destination)
            barrier.wait(timeout=10)

        def attempt(stage):
            try:
                promote_stage(str(stage), str(self.target), dry_run=False, publish_mode="symlink")
                return "published"
            except UnsafeError as exc:
                self.assertEqual(exc.code, "target_exists")
                return "collision"

        with mock.patch("runhand.stage.copy_entries", side_effect=synchronized_copy), ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(attempt, (self.stage, second)))
        self.assertEqual(sorted(outcomes), ["collision", "published"])
        self.assertEqual(len(list(self.parent.glob(".runhand-run-*"))), 1)
        self.assertEqual((self.target / "alias").read_text(), "steps = 20\n")

    def test_failed_or_interrupted_copy_is_not_exposed_under_formal_name(self) -> None:
        for failure in (LocalIOError("copy_failed", "disk full"), KeyboardInterrupt()):
            def partial_copy(plan, entries, destination):
                destination.mkdir()
                (destination / "partial").write_text("incomplete")
                raise failure

            with self.subTest(failure=type(failure).__name__), mock.patch("runhand.stage.copy_entries", side_effect=partial_copy):
                with self.assertRaises(RunHandError):
                    self.promote()
            self.assertEqual(list(self.parent.iterdir()), [])
            self.assertEqual(inspect_stage(str(self.stage)).data["state"], "ready")

    def test_final_symlink_io_failure_is_not_exposed_or_reported_as_internal_error(self) -> None:
        for error in (errno.ENOSPC, errno.EACCES):
            with self.subTest(errno=error), mock.patch(
                "runhand.stage.symlink_noreplace", side_effect=OSError(error, os.strerror(error))
            ):
                with self.assertRaises(LocalIOError) as raised:
                    self.promote()
            self.assertEqual(raised.exception.code, "symlink_publish_failed")
            self.assertEqual(raised.exception.exit_code, 5)
            self.assertEqual(raised.exception.details["errno"], error)
            self.assertEqual(list(self.parent.iterdir()), [])
            self.assertEqual(inspect_stage(str(self.stage)).data["state"], "ready")

    def test_backing_permission_failure_keeps_the_stage_ready(self) -> None:
        chmod = os.chmod

        def deny_container(path, *args, **kwargs):
            if Path(path).name.startswith(".runhand-run-"):
                raise OSError(errno.EACCES, "permission denied")
            return chmod(path, *args, **kwargs)

        with mock.patch("runhand.stage.os.chmod", side_effect=deny_container):
            with self.assertRaises(LocalIOError) as raised:
                self.promote()
        self.assertEqual(raised.exception.code, "publication_prepare_failed")
        self.assertEqual(raised.exception.exit_code, 5)
        self.assertEqual(inspect_stage(str(self.stage)).data["state"], "ready")
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_failed_cleanup_reports_retained_incomplete_tree_for_recovery(self) -> None:
        def partial_copy(plan, entries, destination):
            destination.mkdir()
            raise LocalIOError("copy_failed", "disk full")

        with mock.patch("runhand.stage.copy_entries", side_effect=partial_copy), mock.patch(
            "runhand.stage._cleanup_unpublished_backing", return_value=False
        ):
            with self.assertRaises(LocalIOError) as raised:
                self.promote()
        record = Path(raised.exception.details["publication_record"])
        self.assertEqual(read_json(record)["state"], "incomplete")
        self.assertEqual(read_json(record)["target"], str(self.target))
        self.assertEqual(raised.exception.details["retained_backing"], str(record.parent / "tree"))
        self.assertFalse(self.target.exists())

    def test_interrupt_after_symlink_creation_keeps_the_complete_published_tree(self) -> None:
        def interrupted(source, target):
            symlink_noreplace(source, target)
            raise KeyboardInterrupt()

        with mock.patch("runhand.stage.symlink_noreplace", side_effect=interrupted):
            with self.assertRaises(RunHandError) as raised:
                self.promote()
        self.assertEqual(raised.exception.code, "interrupted")
        self.assertEqual((self.target / "alias").read_text(), "steps = 20\n")
        self.assertEqual(self.target.resolve(), Path(raised.exception.details["retained_backing"]))
        self.assertEqual(read_json(Path(raised.exception.details["publication_record"]))["state"], "ready")
        self.assertEqual(inspect_stage(str(self.stage)).data["state"], "ready")

    def test_recovery_record_failure_after_publication_is_a_warning(self) -> None:
        def failing_record(path, metadata, **kwargs):
            if path.name == PUBLICATION_META and metadata["state"] == "published":
                raise LocalIOError("state_write_failed", "disk full")
            atomic_write_json(path, metadata, **kwargs)

        with mock.patch("runhand.stage.atomic_write_json", side_effect=failing_record):
            result = self.promote()
        self.assertTrue(result.data["published"])
        self.assertEqual(result.warnings[0]["code"], "publication_state_update_failed")
        self.assertEqual((self.target / "alias").read_text(), "steps = 20\n")
        self.assertEqual(inspect_stage(str(self.stage)).data["state"], "promoted")

    def test_durable_copy_is_rejected_inside_managed_scratch_or_standalone_stage(self) -> None:
        for parent in (self.config.scratch_root, self.stage, self.stage / "tree"):
            for dry_run in (True, False):
                with self.subTest(parent=parent, dry_run=dry_run), self.assertRaises(UnsafeError) as raised:
                    promote_stage(str(self.stage), str(parent / "formal"), dry_run=dry_run, publish_mode="symlink")
                self.assertEqual(raised.exception.code, "publication_in_scratch")
        relocated = self.root / "standalone"
        self.stage.rename(relocated)
        with self.assertRaises(UnsafeError):
            promote_stage(str(relocated), str(relocated / "formal"), dry_run=False, publish_mode="symlink")

    def test_symlink_preview_does_not_probe_or_allocate(self) -> None:
        before = (self.stage / STAGE_META).read_bytes()
        with mock.patch("runhand.stage.probe_storage") as probe, mock.patch("runhand.stage.tempfile.mkdtemp") as allocate:
            result = promote_stage(str(self.stage), str(self.target), dry_run=True, publish_mode="symlink")
            probe.assert_not_called()
            allocate.assert_not_called()
        self.assertEqual(result.data["publish_mode"], "symlink")
        self.assertEqual((self.stage / STAGE_META).read_bytes(), before)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_unknown_mode_is_rejected(self) -> None:
        with self.assertRaises(PlanError) as raised:
            promote_stage(str(self.stage), str(self.target), dry_run=False, publish_mode="copy")
        self.assertEqual(raised.exception.code, "invalid_publish_mode")


if __name__ == "__main__":
    unittest.main()
