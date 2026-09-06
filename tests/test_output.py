"""Path-output contract checks independent of command dispatch."""

from __future__ import annotations

import copy
import contextlib
import io
from pathlib import Path
import unittest

from runhand.errors import PlanError, UsageError
from runhand.output import emit_success_path, validate_printable_path


class PathOutputTests(unittest.TestCase):
    def test_preserves_spaces_unicode_and_shell_metacharacters(self) -> None:
        path = "/project/測定 run/it's $(literal) `name`; * [0] /case"
        self.assertEqual(validate_printable_path(path), path)
        self.assertEqual(validate_printable_path(Path(path)), path)
        self.assertEqual(
            emit_success_path({"ok": True, "data": {"tree": path}}, "tree"), path
        )

    def test_does_not_resolve_or_require_an_existing_path(self) -> None:
        path = "/does-not-need-to-exist/parent/../future-run"
        self.assertEqual(validate_printable_path(path), path)

    def test_rejects_unprintable_destination_before_dispatch(self) -> None:
        for path in (
            "", "relative/run", Path("relative/run"),
            "/project/run\n", "/project/one\ntwo", "/project/run\r", "/project/run\0",
        ):
            with self.subTest(path=repr(path)):
                with self.assertRaises(UsageError) as caught:
                    validate_printable_path(path)
                self.assertEqual(caught.exception.code, "invalid_print_path")
                self.assertEqual(caught.exception.exit_code, 2)

    def test_failed_receipt_never_exposes_a_retained_path_as_success(self) -> None:
        for ok in (False, None, 1, "true"):
            with self.subTest(ok=ok):
                receipt = {"ok": ok, "data": {"task": "/scratch/retained-task"}}
                with self.assertRaises(PlanError) as caught:
                    emit_success_path(receipt, "task")
                self.assertEqual(caught.exception.code, "invalid_path_result")

    def test_requires_a_top_level_path_string_in_a_valid_receipt(self) -> None:
        for receipt, field in (
            ({}, "tree"),
            ({"ok": True}, "tree"),
            ({"ok": True, "data": None}, "tree"),
            ({"ok": True, "data": []}, "tree"),
            ({"ok": True, "data": {}}, "tree"),
            ({"ok": True, "data": {"tree": None}}, "tree"),
            ({"ok": True, "data": {"tree": {"path": "/scratch/tree"}}}, "tree"),
            ({"ok": True, "data": {"tree": Path("/scratch/tree")}}, "tree"),
            ({"ok": True, "data": {"tree": "/scratch/tree"}}, "data.tree"),
            ({"ok": True, "data": {"tree": "/scratch/tree"}}, ""),
        ):
            with self.subTest(receipt=receipt, field=field):
                with self.assertRaises(PlanError) as caught:
                    emit_success_path(receipt, field)
                self.assertEqual(caught.exception.code, "invalid_path_result")

    def test_malformed_success_path_is_a_result_error_not_a_caller_error(self) -> None:
        for path in ("", "relative/tree", "/scratch/tree\n", "/scratch/tree\r", "/scratch/tree\0"):
            with self.subTest(path=repr(path)):
                with self.assertRaises(PlanError) as caught:
                    emit_success_path({"ok": True, "data": {"tree": path}}, "tree")
                self.assertEqual(caught.exception.code, "invalid_path_result")

    def test_extraction_leaves_receipt_and_output_channels_to_the_caller(self) -> None:
        receipt = {
            "ok": True,
            "data": {"target": "/project/new run"},
            "warnings": [{"code": "record_unavailable", "message": "retain this warning"}],
        }
        original = copy.deepcopy(receipt)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            extracted = emit_success_path(receipt, "target")
        self.assertEqual(extracted, "/project/new run")
        self.assertEqual(receipt, original)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
