from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from runhand.cli import main
from runhand.copying import copy_entries
from runhand.errors import PlanError
from runhand.scratch import TASK_META


class WorkflowCLITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='runhand-workflow-')
        self.root = Path(self.temporary.name)
        self.workspace = self.root / 'research'
        self.source = self.workspace / 'base case'
        self.source.mkdir(parents=True)
        (self.source / 'input.toml').write_text('steps = 1000\n')
        (self.source / 'job.sh').write_text('touch SHOULD_NOT_EXECUTE\n')
        (self.source / 'output.dat').write_text('old output\n')
        self.scratch = self.root / 'scratch space'
        self.state = self.root / 'state'
        self.prefix = ['--workspace', str(self.workspace), '--scratch-root', str(self.scratch), '--state-root', str(self.state)]
        self.selection = ['--source', str(self.source), '--include', 'input.toml', '--include', 'job.sh']

    def tearDown(self):
        self.temporary.cleanup()

    def invoke(self, *args, json_mode=True, stdin=''):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), mock.patch.object(sys, 'stdin', io.StringIO(stdin)):
            code = main([*self.prefix, *args, *(['--json'] if json_mode else [])])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_stage_flags_and_typed_paths_compose_without_json_glue(self):
        code, out, err = self.invoke('stage', 'create', *self.selection, '--print-path', 'stage', json_mode=False)
        self.assertEqual((code, err), (0, ''))
        stage = Path(out.rstrip('\n'))
        self.assertEqual({p.name for p in (stage / 'tree').iterdir()}, {'input.toml', 'job.sh'})
        (stage / 'tree/input.toml').write_text('steps = 20\n')
        target = self.workspace / 'new case'
        code, out, err = self.invoke('promote', str(stage), str(target), '--print-path', 'target', json_mode=False)
        self.assertEqual((code, out, err), (0, str(target)+'\n', ''))
        self.assertEqual((target/'input.toml').read_text(), 'steps = 20\n')
        self.assertEqual((self.source/'input.toml').read_text(), 'steps = 1000\n')
        code, out, err = self.invoke('promote', str(stage), str(target), '--print-path', 'target', json_mode=False)
        self.assertEqual((code, out), (3, ''))
        self.assertIn('target_exists', err)

    def test_symlink_publication_cli_and_selected_storage_probe(self):
        code, out, err = self.invoke('storage', 'check', str(self.workspace), '--publish-mode', 'symlink')
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)['data']['publish_mode'], 'symlink')
        code, out, err = self.invoke('stage', 'create', *self.selection, '--print-path', 'stage', json_mode=False)
        self.assertEqual(code, 0, err)
        stage = out.rstrip('\n')
        target = self.workspace / 'linked case'
        code, out, err = self.invoke('promote', stage, str(target), '--publish-mode', 'symlink', '--print-path', 'target', json_mode=False)
        self.assertEqual((code, out, err), (0, str(target) + '\n', ''))
        self.assertTrue(target.is_symlink())
        self.assertEqual((target / 'input.toml').read_text(), 'steps = 1000\n')
        code, out, err = self.invoke('promote', stage, str(target), '--publish-mode', 'symlink', '--print-path', 'target', json_mode=False)
        self.assertEqual((code, out), (3, ''))
        self.assertIn('target_exists', err)

    def test_flag_selection_defaults_to_partial_and_preserves_quoted_patterns(self):
        code, out, _ = self.invoke('stage', 'create', '--source', str(self.source), '--include', '*.toml', '--dry-run')
        self.assertEqual(code, 0)
        data = json.loads(out)['data']
        self.assertEqual(data['copy_plan']['completeness'], 'partial')
        self.assertEqual([entry['path'] for entry in data['selection']], ['input.toml'])
        self.assertFalse(self.scratch.exists())

    def test_conflicting_plan_or_output_options_fail_before_effects(self):
        cases = [
            ['stage', 'create', *self.selection, '--plan', '-'],
            ['stage', 'create', '--source', str(self.source), '--plan', '-', '--exclude', 'output.dat'],
            ['stage', 'create', *self.selection, '--print-path', 'task'],
            ['stage', 'create', *self.selection, '--print-path', 'stage', '--dry-run'],
            ['stage', 'create', '--source', str(self.source)],
        ]
        for args in cases:
            with self.subTest(args=args):
                code, _, _ = self.invoke(*args, json_mode=False)
                self.assertEqual(code, 2)
                self.assertFalse(self.scratch.exists())

    def test_newline_path_rejected_before_allocation_and_resolved_target_publication(self):
        self.scratch = self.root / 'bad\nroot'
        self.prefix[3] = str(self.scratch)
        code, out, _ = self.invoke('scratch', 'get', '--kind', 'smoke', '--key', 'test', '--print-path', 'task', json_mode=False)
        self.assertEqual((code, out), (2, ''))
        self.assertFalse(self.scratch.exists())
        self.prefix[3] = str(self.root/'good')
        code, out, _ = self.invoke('stage', 'create', *self.selection)
        self.assertEqual(code, 0)
        stage = json.loads(out)['data']['stage']
        bad_parent = self.root/'parent\nname'
        bad_parent.mkdir()
        alias = self.root/'ordinary-alias'
        alias.symlink_to(bad_parent, target_is_directory=True)
        code, out, _ = self.invoke('promote', stage, str(alias/'target'), '--print-path', 'target', json_mode=False)
        self.assertEqual((code, out), (2, ''))
        self.assertEqual(list(bad_parent.iterdir()), [])
        (bad_parent/'child').mkdir()
        nested_alias = self.root/'nested-alias'
        nested_alias.symlink_to(bad_parent/'child', target_is_directory=True)
        code, out, _ = self.invoke('promote', stage, str(nested_alias/'..'/'target'), '--print-path', 'target', json_mode=False)
        self.assertEqual((code, out), (2, ''))
        self.assertFalse((bad_parent/'target').exists())
        self.assertFalse((self.root/'target').exists())

    def test_prepare_copies_once_without_stage_publication_or_execution(self):
        with mock.patch('runhand.scratch.copy_entries', wraps=copy_entries) as copy, mock.patch('runhand.stage._rename_noreplace', side_effect=AssertionError('formal publication must not be used')):
            code, out, _ = self.invoke('scratch', 'prepare', *self.selection, '--kind', 'smoke', '--key', '20 steps')
        self.assertEqual(code, 0, out)
        copy.assert_called_once()
        data = json.loads(out)['data']
        task, work = Path(data['task']), Path(data['workdir'])
        self.assertEqual(work, task/'work')
        self.assertEqual({p.name for p in work.iterdir()}, {'input.toml', 'job.sh'})
        self.assertEqual(data['preparation'], 'ready')
        meta = json.loads((task/TASK_META).read_text())
        self.assertEqual((meta['liveness'], meta['attempt_state'], meta['preparation']), ('unknown', 'none', 'ready'))
        self.assertFalse((self.scratch/'stages').exists())
        self.assertFalse((work/'SHOULD_NOT_EXECUTE').exists())
        self.assertEqual(list(self.workspace.iterdir()), [self.source])

    def test_prepare_dry_run_and_bad_selection_leave_no_task(self):
        code, out, _ = self.invoke('scratch', 'prepare', *self.selection, '--kind', 'smoke', '--key', 'dry', '--dry-run')
        self.assertEqual(code, 0, out)
        self.assertNotIn('task', json.loads(out)['data'])
        self.assertFalse(self.scratch.exists())
        code, _, _ = self.invoke('scratch', 'prepare', '--source', str(self.source), '--include', 'absent', '--kind', 'smoke', '--key', 'empty')
        self.assertEqual(code, 4)
        self.assertFalse(self.scratch.exists())
        (self.source/'unsafe').symlink_to('../outside')
        code, _, _ = self.invoke('scratch', 'prepare', '--source', str(self.source), '--include', 'unsafe', '--kind', 'smoke', '--key', 'unsafe')
        self.assertEqual(code, 4)
        self.assertFalse(self.scratch.exists())

    def test_prepare_holds_task_lock_against_exact_orphan_deletion(self):
        def copy_while_checked(plan, entries, work):
            code, out, _ = self.invoke('gc', '--orphan', str(work.parent), '--apply')
            self.assertEqual(code, 5)
            self.assertEqual(json.loads(out)['errors'][0]['code'], 'managed_root_busy')
            self.assertTrue(work.parent.exists())
            copy_entries(plan, entries, work)

        with mock.patch('runhand.scratch.copy_entries', side_effect=copy_while_checked):
            code, out, _ = self.invoke('scratch', 'prepare', *self.selection, '--kind', 'smoke', '--key', 'locked')
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(out)['data']['preparation'], 'ready')

    def test_prepare_source_overlap_rejected_even_in_dry_run(self):
        self.prefix[3] = str(self.source/'scratch')
        for preview in ([], ['--dry-run']):
            code, _, _ = self.invoke('scratch', 'prepare', *self.selection, '--kind', 'smoke', '--key', 'overlap', *preview)
            self.assertEqual(code, 3)
            self.assertFalse((self.source/'scratch').exists())

    def test_failed_preparation_retains_protected_task_and_reports_it_on_stderr(self):
        with mock.patch('runhand.scratch.copy_entries', side_effect=PlanError('source_changed', 'fixture changed')):
            code, out, err = self.invoke('scratch', 'prepare', *self.selection, '--kind', 'smoke', '--key', 'failed', '--print-path', 'task', json_mode=False)
        self.assertEqual((code, out), (4, ''))
        task = next((self.scratch/'tasks/smoke').iterdir())
        self.assertIn(str(task), err)
        meta = json.loads((task/TASK_META).read_text())
        self.assertEqual(meta['liveness'], 'unknown')
        self.assertNotEqual(meta['preparation'], 'ready')
        code, out, _ = self.invoke('gc', '--kind', 'scratch', '--older-than', '0s', '--apply')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['data']['deleted'], [])
        self.assertTrue(task.exists())

    def test_path_output_without_key_allocates_fresh_tasks_without_reuse_warnings(self):
        args = ['scratch', 'prepare', *self.selection, '--kind', 'smoke', '--print-path', 'task']
        code, first, _ = self.invoke(*args, json_mode=False)
        self.assertEqual(code, 0)
        code, second, err = self.invoke(*args, json_mode=False)
        self.assertEqual(code, 0)
        self.assertNotEqual(first, second)
        self.assertEqual(len(second.splitlines()), 1)
        self.assertEqual(err, '')

    def test_scalar_evidence_requires_explicit_fresh_compatible_observation(self):
        code, out, _ = self.invoke('scratch', 'get', '--kind', 'smoke', '--key', 'evidence')
        self.assertEqual(code, 0)
        task = json.loads(out)['data']['task']
        flags = ['--liveness', 'terminal', '--attempt-state', 'terminal', '--capability', 'site', '--identity', 'fixture-site']
        before = (Path(task)/TASK_META).read_bytes()
        for invalid in [flags, [*flags, '--observed-at', '2026-01-01'], [*flags, '--observed-at', '2026-01-01T00:00:00Z', '--evidence', '-']]:
            code, _, _ = self.invoke('scratch', 'record', task, *invalid)
            self.assertNotEqual(code, 0)
            self.assertEqual((Path(task)/TASK_META).read_bytes(), before)
        code, out, _ = self.invoke('scratch', 'record', task, *flags, '--observed-at', '2026-01-01T00:00:00Z')
        self.assertEqual(code, 0, out)
        terminal = (Path(task)/TASK_META).read_bytes()
        code, _, _ = self.invoke('scratch', 'record', task, *flags, '--observed-at', '2025-01-01T00:00:00Z')
        self.assertEqual(code, 4)
        self.assertEqual((Path(task)/TASK_META).read_bytes(), terminal)

    def test_pure_checks_do_not_require_workspace_configuration_and_gate_unknown(self):
        self.prefix = ['--workspace', str(self.root/'absent')]
        code, out, _ = self.invoke('retry', 'check', '--evidence', '-', stdin='{}')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['data']['decision'], 'unknown')
        code, out, _ = self.invoke('retry', 'check', '--evidence', '-', '--require-allowed', stdin='{}')
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(out)['errors'][0]['details']['decision'], 'unknown')
        code, out, _ = self.invoke('storage', 'check', str(self.root))
        self.assertEqual(code, 0, out)
        self.assertTrue(json.loads(out)['data']['supported'])
        code, out, _ = self.invoke('storage', 'check', str(self.root/'missing'))
        self.assertEqual(code, 5)
        self.assertFalse(json.loads(out)['errors'][0]['details']['checks'][0]['supported'])
        self.assertFalse((self.root/'missing').exists())


if __name__ == '__main__':
    unittest.main()
