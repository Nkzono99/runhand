# RunHand

RunHand is a lightweight workflow accelerator for agent-driven computational research. It keeps scientific meaning in Simulator capabilities, site and scheduler policy in Site capabilities, and gives agents a small deterministic CLI for filesystem and local-state work.

The current implementation is the first 1.0 vertical slice:

- bounded, non-executing workspace context scans;
- versioned copy plans and source-safe scratch stages;
- Linux `renameat2(RENAME_NOREPLACE)` promotion into formal run directories;
- unique scratch allocation with key matches treated only as reuse hints;
- live recent/unresolved submission-history summaries without making history a gate;
- preview-first garbage collection that protects unknown liveness;
- focused Codex skills for Run/stage/scratch transitions and RunHand-owned state maintenance.

## Install the Codex plugin

RunHand is distributed from this repository as a Codex plugin marketplace. Python 3.11 or newer and a Codex release with plugin support are required.

Register the GitHub repository and install the plugin:

```bash
codex plugin marketplace add Nkzono99/runhand --ref main
codex plugin add runhand@runhand
```

Then start a new Codex session so the `runhand` and `runhand-maintenance` skills are loaded. In the Codex CLI, you can also run `/plugins` to inspect or install plugins from registered marketplaces. Plugins are not currently supported by the Codex IDE extension; use the Codex CLI or desktop app.

The plugin can invoke the CLI from its bundled source checkout. To also make the `runhand` command available directly in your shell, install the Python package:

```bash
python3.11 -m pip install "git+https://github.com/Nkzono99/runhand.git@main"
runhand doctor
```

To refresh or remove the plugin later:

```bash
codex plugin marketplace upgrade runhand
codex plugin remove runhand@runhand
```

See the official [Codex plugin guide](https://learn.chatgpt.com/docs/plugins) and [plugin packaging guide](https://developers.openai.com/plugins/build/plugins) for the host's current behavior.

## Install for development

Python 3.11 or newer is required. RunHand has no runtime dependencies.

```bash
python -m pip install -e .
runhand doctor
```

Without installation, run it from the checkout with:

```bash
PYTHONPATH=src python -m runhand --help
```

## Minimal workflow

Have the matching Simulator capability produce a copy plan tailored to the source Run. Do not use a broad `include: ["**"]` fallback; generated outputs and binaries often need to be excluded. The plan's `source` must resolve to the same directory passed to `--source`, and its format is defined by [`schemas/v1/copy-plan.schema.json`](schemas/v1/copy-plan.schema.json).

Then create and publish a stage. Inspection and dry-run are optional tools for real uncertainty, not mandatory gates:

```bash
runhand stage create --source /project/runs/base --plan copy-plan.json --json
runhand stage inspect /scratch/runhand/stages/stage-... --json
runhand promote /scratch/runhand/stages/stage-... /project/runs/new-run --dry-run --json
runhand promote /scratch/runhand/stages/stage-... /project/runs/new-run --json
```

Promotion never overwrites an existing file, directory, or symlink. Validation is intentionally not a promotion gate; the Agent applies live Simulator validation and Site routing immediately before execution or submission.

Other public commands are documented in [SPEC.md](SPEC.md). Versioned machine contracts live in [`schemas/v1`](schemas/v1).

## Codex plugin

This repository is itself the `runhand` plugin root. The manifest is [`.codex-plugin/plugin.json`](.codex-plugin/plugin.json). [`skills/runhand/SKILL.md`](skills/runhand/SKILL.md) handles safe Run/stage/scratch transitions, while [`skills/runhand-maintenance/SKILL.md`](skills/runhand-maintenance/SKILL.md) handles doctor and cleanup requests.

The core skill does not activate for simulator-only questions, bounded read-only output analysis, or scheduler-only operations on an identified Run or Job ID. Those route directly to the matching Simulator, output, or Site capability. Routing examples are recorded in [`evals/skill-routing.json`](evals/skill-routing.json).

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

On an HPC login node, route the test controller through the site's compute-node mechanism.
