# RunHand

RunHand is a Codex plugin and CLI for computational research. Its skills create simulation cases, run bounded checks, compare models, coordinate job submission, and clean temporary work. The CLI manages isolated working copies and publishes new Runs without overwriting existing paths.

Scientific choices follow Simulator instructions; host and scheduler operations follow Site instructions.

## Install

Requires Python 3.11 or newer and Codex with plugin support.

```bash
codex plugin marketplace add Nkzono99/runhand --ref main
codex plugin add runhand@runhand
```

Start a new Codex session, then ask naturally:

- “Create a case with 0.7 times the density and run it.”
- “Try these settings for 20 steps and check the result.”
- “Compare the old and new models and check whether the result changes.”

Update with `codex plugin marketplace upgrade runhand`; remove with `codex plugin remove runhand@runhand`.

For the bundled context hooks, review and trust RunHand's definitions in `/hooks`. See [hook setup and behavior](docs/hooks.md).

## Skills and guides

| Task | Skill |
|---|---|
| Create cases or run temporary checks | [runhand](skills/runhand/SKILL.md) |
| Compare models and validate effects | [research-loop](skills/research-loop/SKILL.md) |
| Submit, retry, or restart prepared Runs | [runhand-submit](skills/runhand-submit/SKILL.md) |
| Inspect storage and clean disposable work | [runhand-maintenance](skills/runhand-maintenance/SKILL.md) |
| Reorient after feedback, stalled progress, or context resumption | [situational-check](skills/situational-check/SKILL.md) |

The skills share a [work policy](skills/runhand/references/work-policy.md) for effort allocation and judgment, plus an [observation guide](skills/runhand/references/observe-work.md) for periodic progress checks.

Detailed procedures: [create a Run](skills/runhand/references/create-work.md), [complete scratch work](skills/runhand/references/scratch-work.md), and [diagnose publication storage](skills/runhand/references/storage-check.md). CLI commands, configuration, and contracts are documented in [SPEC.md](SPEC.md).

Publication defaults to atomic directory rename and checks destination support before copying. On shared filesystems that reject `RENAME_NOREPLACE`, including some Lustre mounts, `promote --publish-mode symlink` publishes a relative link to an independent durable sibling tree. It refuses all existing targets and survives stage cleanup. Keep the link and its hidden backing directory together; see the [storage and recovery guide](skills/runhand/references/storage-check.md) before using this mode.

## CLI and development

The plugin can use the CLI from its bundled checkout. For direct shell use:

```bash
python3.11 -m pip install "git+https://github.com/Nkzono99/runhand.git@main"
runhand doctor
```

For development from a source checkout:

```bash
python -m pip install -e .
PYTHONPATH=src python -m unittest discover -s tests -v
```

On an HPC login node, route tests through the site's compute-node launcher. See [CLI setup](skills/runhand/references/cli-setup.md) for running without installation.

Changes to this checkout reach installed plugins after distribution and refresh. Plugin metadata lives in [.codex-plugin/plugin.json](.codex-plugin/plugin.json); evaluation cases cover [skill routing](evals/skill-routing.json) and [situational behavior](evals/situational-check.json).
