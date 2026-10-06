"""Exercise plugin hook commands and their context-only contract."""

from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "hooks/situational_context.py"
CONTEXT_FOR = runpy.run_path(str(HELPER))["context_for"]


class SituationalContextTests(unittest.TestCase):
    def test_valid_events_return_bounded_context_without_echoing_input(self) -> None:
        events = [
            {"hook_event_name": "SessionStart", "source": source}
            for source in ("startup", "resume", "clear", "compact")
        ] + [{"hook_event_name": "UserPromptSubmit", "prompt": "private-message-8921"}]
        for payload in events:
            with self.subTest(payload=payload):
                context = CONTEXT_FOR({
                    **payload,
                    "session_id": "private-session-8921",
                    "transcript_path": "/private-transcript-8921",
                    "cwd": "/private-project-8921",
                })
                self.assertIsInstance(context, str)
                self.assertLess(len(context), 1600)
                self.assertIn(json.dumps(str(ROOT / "skills/situational-check/SKILL.md"), ensure_ascii=False), context)
                for private_value in (
                    "private-message-8921", "private-session-8921",
                    "private-transcript-8921", "private-project-8921",
                ):
                    self.assertNotIn(private_value, context)

    def test_resume_and_compact_receive_the_same_restoration_context(self) -> None:
        def context(source: str) -> str:
            return CONTEXT_FOR({"hook_event_name": "SessionStart", "source": source})

        self.assertEqual(context("startup"), context("clear"))
        self.assertEqual(context("resume"), context("compact"))
        self.assertNotEqual(context("startup"), context("resume"))

    def test_unknown_events_and_invalid_fields_produce_no_context(self) -> None:
        for payload in (
            None, [], "string", {},
            {"hook_event_name": "Stop"},
            {"hook_event_name": "PreToolUse", "tool_name": "Bash"},
            {"hook_event_name": "SessionStart"},
            {"hook_event_name": "SessionStart", "source": "unknown"},
            {"hook_event_name": "SessionStart", "source": []},
            {"hook_event_name": "UserPromptSubmit"},
            {"hook_event_name": "UserPromptSubmit", "prompt": 123},
            {"hook_event_name": "UserPromptSubmit", "prompt": " \n\t"},
        ):
            with self.subTest(payload=payload):
                self.assertIsNone(CONTEXT_FOR(payload))

    def test_content_selection_is_stateless_and_does_not_open_files(self) -> None:
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "目的に戻って。",
            "transcript_path": str(ROOT / "README.md"),
        }
        with mock.patch("builtins.open", side_effect=AssertionError("file read")), \
             mock.patch.object(Path, "open", side_effect=AssertionError("file read")):
            first = CONTEXT_FOR(payload)
            CONTEXT_FOR({"hook_event_name": "SessionStart", "source": "compact"})
            self.assertEqual(CONTEXT_FOR(payload), first)
            self.assertEqual(CONTEXT_FOR({**payload, "prompt": "そのまま続けて。"}), first)

    def test_missing_bundled_skill_silently_skips_the_reminder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "absent/SKILL.md"
            with mock.patch.dict(CONTEXT_FOR.__globals__, {"SKILL_PATH": missing}):
                self.assertIsNone(CONTEXT_FOR({
                    "hook_event_name": "SessionStart", "source": "startup",
                }))


class PluginHookCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.plugin = self.workspace / "RunHand 日本語 $(touch injected) `touch backtick`"
        self.other_cwd = self.workspace / "other project"
        self.other_cwd.mkdir()
        (self.plugin / "hooks").mkdir(parents=True)
        (self.plugin / "skills/situational-check").mkdir(parents=True)
        shutil.copyfile(HELPER, self.plugin / "hooks/situational_context.py")
        shutil.copyfile(
            ROOT / "skills/situational-check/SKILL.md",
            self.plugin / "skills/situational-check/SKILL.md",
        )
        manifest = json.loads((ROOT / ".codex-plugin/plugin.json").read_text())
        config = json.loads((ROOT / manifest["hooks"]).read_text())
        self.handlers = {
            event: entries[0]["hooks"][0]
            for event, entries in config["hooks"].items()
        }
        self.env = dict(os.environ)
        self.env.pop("PLUGIN_ROOT", None)
        self.env.pop("CLAUDE_PLUGIN_ROOT", None)
        self.env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + self.env.get("PATH", "")

    def execute(self, event: str, raw_input: str, root_variable: str = "PLUGIN_ROOT") -> subprocess.CompletedProcess:
        return subprocess.run(
            ["/bin/sh", "-c", self.handlers[event]["command"]],
            input=raw_input, text=True, capture_output=True,
            cwd=self.other_cwd, env={**self.env, root_variable: str(self.plugin)},
            timeout=self.handlers[event]["timeout"],
        )

    def test_configured_commands_use_plugin_root_and_only_supply_context(self) -> None:
        self.assertEqual(set(self.handlers), {"SessionStart", "UserPromptSubmit"})
        before = sorted(path.relative_to(self.workspace) for path in self.workspace.rglob("*"))
        for event, fields in (
            ("SessionStart", {"source": "compact"}),
            ("UserPromptSubmit", {"prompt": "目的に戻って。"}),
        ):
            for root_variable in ("PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT"):
                with self.subTest(event=event, root_variable=root_variable):
                    result = self.execute(event, json.dumps({"hook_event_name": event, **fields}), root_variable)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stderr, "")
                    output = json.loads(result.stdout)
                    self.assertEqual(set(output), {"hookSpecificOutput"})
                    specific = output["hookSpecificOutput"]
                    self.assertEqual(set(specific), {"hookEventName", "additionalContext"})
                    self.assertEqual(specific["hookEventName"], event)
                    self.assertIn(json.dumps(str(self.plugin / "skills/situational-check/SKILL.md"), ensure_ascii=False), specific["additionalContext"])
        after = sorted(path.relative_to(self.workspace) for path in self.workspace.rglob("*"))
        self.assertEqual(before, after)

    def test_bad_or_unsupported_input_exits_successfully_without_output(self) -> None:
        for raw_input in (
            "", "{broken", "null", "[]", "{}",
            '{"hook_event_name":"Stop"}',
            '{"hook_event_name":"SessionStart","source":"future-event"}',
            '{"hook_event_name":"UserPromptSubmit","prompt":" "}',
        ):
            with self.subTest(raw_input=raw_input):
                result = self.execute("SessionStart", raw_input)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
