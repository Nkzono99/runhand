from __future__ import annotations

import contextlib
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from runhand.config import Config
from runhand.context import scan_context
from runhand.copying import CopyPlan, select_entries
from runhand.errors import PlanError, UnsafeError
from runhand.stage import create_stage, promote_stage
from runhand.storage import atomic_write_json, read_json


class FilesystemBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.config = Config(
            workspace=self.source,
            scratch_root=self.root / "scratch",
            state_root=self.root / "state",
            warm_cache=False,
        )

    def plan(self, *include: str) -> CopyPlan:
        return CopyPlan(
            self.source, include or ("**",), (), "internal-relative", "complete", None
        )

    def test_nested_scratch_is_rejected_before_writes_including_dry_run(self) -> None:
        (self.source / "case.toml").write_text("value = 1\n", encoding="utf-8")
        before = self.source.stat()
        alias = self.root / "source-alias"
        alias.symlink_to(self.source, target_is_directory=True)
        for scratch in (self.source, self.source / ".scratch", alias / ".scratch"):
            for dry_run in (True, False):
                with self.subTest(scratch=scratch, dry_run=dry_run):
                    config = replace(self.config, scratch_root=scratch)
                    with self.assertRaises(UnsafeError) as raised:
                        create_stage(config, self.plan(), dry_run=dry_run)
                    self.assertEqual(raised.exception.code, "scratch_overlaps_source")
                    self.assertEqual(
                        sorted(path.name for path in self.source.iterdir()), ["case.toml"]
                    )
                    after = self.source.stat()
                    self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)
                    self.assertEqual(before.st_ctime_ns, after.st_ctime_ns)
        self.assertFalse(self.config.state_root.exists())

    def test_selected_link_requires_intermediate_directory_before_dotdot(self) -> None:
        (self.source / "mid").mkdir()
        (self.source / "target.txt").write_text("input", encoding="utf-8")
        (self.source / "link").symlink_to("mid/../target.txt")
        with self.assertRaises(PlanError) as raised:
            create_stage(self.config, self.plan("link", "target.txt"), dry_run=False)
        self.assertEqual(raised.exception.code, "uncopied_symlink_target")
        self.assertFalse(self.config.scratch_root.exists())

    def test_source_cannot_be_its_own_stage_container(self) -> None:
        scratch_root = self.root / "managed"
        self.source = scratch_root / "stages"
        self.source.mkdir(parents=True)
        config = replace(self.config, scratch_root=scratch_root)
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run):
                with self.assertRaises(UnsafeError) as raised:
                    create_stage(config, self.plan(), dry_run=dry_run)
                self.assertEqual(raised.exception.code, "scratch_overlaps_source")
                self.assertEqual(list(scratch_root.iterdir()), [self.source])
                self.assertEqual(list(self.source.iterdir()), [])

    def test_internal_directory_symlink_chain_survives_stage_and_promotion(self) -> None:
        (self.source / "real").mkdir()
        (self.source / "real" / "input.txt").write_text("input", encoding="utf-8")
        (self.source / "alias").symlink_to("real", target_is_directory=True)
        (self.source / "link").symlink_to("alias/input.txt")
        stage = Path(create_stage(self.config, self.plan(), dry_run=False).data["stage"])
        self.assertEqual((stage / "tree" / "link").read_text(), "input")
        target = self.root / "formal"
        promote_stage(str(stage), str(target), dry_run=False)
        self.assertEqual((target / "link").read_text(), "input")
        self.assertEqual(os.readlink(target / "link"), "alias/input.txt")

    def test_symlink_dotdot_follows_directory_alias_before_parent(self) -> None:
        (self.source / "real" / "nested").mkdir(parents=True)
        (self.source / "real" / "input.txt").write_text("input", encoding="utf-8")
        (self.source / "alias").symlink_to("real/nested", target_is_directory=True)
        (self.source / "link").symlink_to("alias/../input.txt")
        selection = select_entries(self.plan())
        self.assertIn("link", [entry.relative for entry in selection])
        stage = Path(create_stage(self.config, self.plan(), dry_run=False).data["stage"])
        self.assertEqual((stage / "tree" / "link").read_text(), "input")

    def test_standalone_stage_can_still_be_promoted(self) -> None:
        (self.source / "case.toml").write_text("value = 1\n", encoding="utf-8")
        stage = Path(create_stage(self.config, self.plan(), dry_run=False).data["stage"])
        relocated = self.root / "standalone-stage"
        stage.rename(relocated)
        target = self.root / "standalone-target"
        result = promote_stage(str(relocated), str(target), dry_run=False)
        self.assertTrue(result.data["published"])
        self.assertEqual((target / "case.toml").read_text(), "value = 1\n")

    def scan_counted(self, *, limit: int, reverse: bool = False):
        original_scandir = os.scandir
        consumed = []

        @contextlib.contextmanager
        def counted_scandir(path):
            with original_scandir(path) as scanner:
                entries = list(scanner)
            if reverse:
                entries.reverse()

            def counted():
                for entry in entries:
                    consumed.append(entry.path)
                    yield entry

            yield counted()

        with mock.patch("runhand.context.os.scandir", side_effect=counted_scandir):
            result = scan_context(
                replace(self.config, context_max_entries=limit), refresh=True
            )
        return result.data, consumed

    def test_context_omits_overwide_directory_without_exceeding_enumeration_cap(self) -> None:
        for number in range(8):
            (self.source / f"case{number}.toml").touch()
        forward, consumed = self.scan_counted(limit=3)
        backward, reversed_consumed = self.scan_counted(limit=3, reverse=True)
        self.assertEqual(len(consumed), 3)
        self.assertEqual(len(reversed_consumed), 3)
        self.assertEqual(forward, backward)
        self.assertTrue(forward["partial"])
        self.assertEqual(forward["candidate_runs"], [])
        self.assertEqual(forward["control_files"], [])
        self.assertEqual(forward["scan"]["entries_visited"], 3)
        self.assertEqual(forward["partial_reasons"][0]["code"], "max_entries")

    def test_context_keeps_complete_parent_when_child_exhausts_remaining_budget(self) -> None:
        (self.source / "case.toml").touch()
        output = self.source / "output"
        output.mkdir()
        later = self.source / "zrun"
        later.mkdir()
        (later / "case.toml").touch()
        for number in range(8):
            (output / f"data{number}.json").touch()
        data, consumed = self.scan_counted(limit=6)
        self.assertEqual(len(consumed), 6)
        self.assertEqual(data["control_files"], ["case.toml"])
        self.assertEqual(
            [item["path"] for item in data["candidate_runs"]], [str(self.source)]
        )
        self.assertEqual(
            {item["path"] for item in data["partial_reasons"]},
            {str(output), str(later)},
        )
        self.assertFalse(any(str(later / "case.toml") == path for path in consumed))

    def test_context_exact_cap_is_conservatively_partial(self) -> None:
        (self.source / "case.toml").touch()
        data, consumed = self.scan_counted(limit=1)
        self.assertEqual(len(consumed), 1)
        self.assertTrue(data["partial"])
        self.assertEqual(data["candidate_runs"], [])

    def test_context_ignores_cache_from_previous_scan_algorithm(self) -> None:
        config = replace(self.config, warm_cache=True)
        scan_context(config, refresh=False)
        cached_path = next((config.state_root / "cache").glob("context-*.json"))
        cached = read_json(cached_path)
        cached["root_signature"].pop("scan_version")
        cached["data"]["readmes"] = ["stale-value"]
        atomic_write_json(cached_path, cached)
        result = scan_context(config, refresh=False)
        self.assertEqual(result.data["cache"]["status"], "miss")
        self.assertEqual(result.data["readmes"], [])


if __name__ == "__main__":
    unittest.main()
