from __future__ import annotations

import errno
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from runhand.errors import LocalIOError
from runhand.probes import check_storage, probe_storage


@unittest.skipUnless(sys.platform == "linux", "RunHand publication requires Linux")
class StorageProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.parent = self.root / "storage"
        self.parent.mkdir()

    def test_real_probe_preserves_existing_entries_and_cleans_its_temporary_tree(self) -> None:
        existing = self.parent / "existing"
        existing.mkdir()
        (existing / "sentinel").write_text("user data\n", encoding="utf-8")
        inode = existing.stat().st_ino
        result = check_storage([str(self.parent)])
        self.assertEqual(result.command, "storage check")
        self.assertEqual(result.data, {
            "supported": True,
            "checks": [{"parent": str(self.parent), "supported": True}],
        })
        self.assertEqual(list(self.parent.iterdir()), [existing])
        self.assertEqual(existing.stat().st_ino, inode)
        self.assertEqual((existing / "sentinel").read_text(), "user data\n")

    def test_probe_detects_replacement_and_cleans_up_after_failure(self) -> None:
        # Simulate a filesystem/runtime that ignores RENAME_NOREPLACE. The
        # probe must test collision behavior, not merely find the libc symbol.
        def replacing_rename(source_fd, source, target_fd, target, flags):
            os.replace(os.fsdecode(source), os.fsdecode(target))
            return 0

        rename = mock.Mock(side_effect=replacing_rename)
        with mock.patch(
            "runhand.probes.ctypes.CDLL", return_value=SimpleNamespace(renameat2=rename)
        ):
            result = probe_storage(str(self.parent))
        self.assertFalse(result["supported"])
        self.assertEqual(result["error"]["errno"], errno.EIO)
        self.assertIn("existing destination was replaced", result["error"]["message"])
        self.assertEqual(rename.call_count, 2)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_nonexistent_parent_is_not_created(self) -> None:
        missing = self.parent / "missing" / "nested"
        result = probe_storage(str(missing))
        self.assertFalse(result["supported"])
        self.assertEqual(result["parent"], str(missing))
        self.assertEqual(result["error"]["errno"], errno.ENOENT)
        self.assertFalse(missing.parent.exists())
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_failed_batch_retains_successes_and_all_failed_checks(self) -> None:
        first_missing = self.parent / "first-missing"
        second_missing = self.parent / "second-missing"
        parents = [str(first_missing), str(self.parent), str(second_missing)]
        with self.assertRaises(LocalIOError) as raised:
            check_storage(parents)
        error = raised.exception
        self.assertEqual(error.code, "storage_check_failed")
        self.assertEqual(error.exit_code, 5)
        checks = error.details["checks"]
        self.assertEqual([check["parent"] for check in checks], parents)
        self.assertEqual([check["supported"] for check in checks], [False, True, False])
        self.assertEqual(checks[0]["error"]["errno"], errno.ENOENT)
        self.assertEqual(checks[2]["error"]["errno"], errno.ENOENT)
        self.assertEqual(list(self.parent.iterdir()), [])

if __name__ == "__main__":
    unittest.main()
