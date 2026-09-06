# RunHand

RunHand is a lightweight workflow accelerator for agent-driven computational research. It keeps scientific meaning in Simulator capabilities, site and scheduler policy in Site capabilities, and gives agents a small deterministic CLI for filesystem and local-state work.

The current implementation is the first 1.0 vertical slice:

- bounded, non-executing workspace context scans;
- versioned copy plans and source-safe scratch stages;
- Linux `renameat2(RENAME_NOREPLACE)` promotion into formal run directories;
- one-copy scratch preparation and explicit runtime evidence;
- preview-first garbage collection coordinated with pinning, evidence updates, and stage use;
- focused Codex skills for filesystem transitions, submission/retry handoff, and local-state maintenance.

## Install the Codex plugin

RunHand is distributed from this repository as a Codex plugin marketplace. Python 3.11 or newer and a Codex release with plugin support are required.

Register the GitHub repository and install the plugin:

```bash
codex plugin marketplace add Nkzono99/runhand --ref main
codex plugin add runhand@runhand
```

Then start a new Codex session so the `runhand`, `runhand-submit`, and `runhand-maintenance` skills are loaded. In the Codex CLI, you can also run `/plugins` to inspect or install plugins from registered marketplaces. Plugins are not currently supported by the Codex IDE extension; use the Codex CLI or desktop app.

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

Apply the matching Simulator instructions to identify the actual inputs and dependencies. Pass the selection directly to the CLI; no handwritten JSON is needed. This example assumes `input.toml`, `model.dat`, and `run.sh` are the required files:

```bash
RH_STAGE=$(runhand stage create --source /project/runs/base \
    --include 'input.toml' --include 'model.dat' --include 'run.sh' \
    --print-path stage)
# Make the requested edits in "$RH_STAGE/tree" and check the relevant changes.
runhand promote "$RH_STAGE" /project/runs/new-run --json
```

Quote and repeat include/exclude patterns. Internal relative symlinks are allowed by default. An integration can pass a [versioned plan](schemas/v1/copy-plan.schema.json) with `--plan FILE|-`; its basis and completeness fields are annotations, not dependency checks or permission to execute. Inspection and `--dry-run --json` are available when needed.

`--print-path` emits one supported path to stdout, with warnings/errors on stderr. Check the exit status before using the path; unattended Bash scripts should use `set -euo pipefail`. It is exclusive with `--json` and `--dry-run`. Use `--json` for the complete receipt.

Promotion never overwrites an existing file, directory, or symlink. Validation is intentionally not a promotion gate. Before execution, apply the relevant Simulator check once and confirm the Site route; reuse a current check when inputs and execution conditions still match.

The formal target filesystem must support Linux `renameat2(RENAME_NOREPLACE)`. Internal stages use unique directories and protect unfinished copies, so scratch does not need this operation. The [storage check](skills/runhand/references/storage-check.md) diagnoses formal-publication support; changing scratch cannot fix an incompatible formal destination.

The scratch root must be outside the source Run. An overlapping location is rejected before any directories or ownership markers are written, including during a dry-run.

Other public commands are documented in [SPEC.md](SPEC.md). Filesystem contracts live in [`schemas/v1`](schemas/v1). Optional structured retry checks use [`schemas/v2`](schemas/v2).

## Scratch completion and cleanup

After the Site capability selects suitable scratch, prepare a writable copy directly:

```bash
RH_TASK=$(runhand scratch prepare --source /project/runs/base \
    --include 'input.toml' --include 'model.dat' --include 'run.sh' \
    --kind smoke --print-path task)
# Edit/check the bounded settings in "$RH_TASK/work", then use the Site launcher there.
```

`scratch prepare` allocates a unique task and copies the selection once into `work/`. It does not create a formal Run or execute a script. `scratch get` allocates an empty task. Neither command searches or reuses previous tasks; an optional `--key` only stores a label digest. Copy failure returns an error with any retained task path; incomplete or unknown work stays protected.

After the Site confirms all associated scheduler work has ended, record that observed state with its actual timestamp and provider identity:

```bash
runhand scratch record "$RH_TASK" --liveness terminal --attempt-state terminal \
    --observed-at "$RH_OBSERVED_AT" --capability site --identity "$RH_PROVIDER_LABEL" --json
runhand gc --kind scratch --older-than 14d --json
```

Capture `RH_OBSERVED_AT` when obtaining the observation; it never defaults to the current time inside the CLI. The [complete scratch procedure](skills/runhand/references/scratch-work.md) covers preparation, execution, result checks, and the state mapping for running, unresolved, or unscheduled work. An existing evidence object can still be supplied with `--evidence FILE|-` instead of scalar flags.

Pin an existing terminal task before dispatching work into it. Record active evidence after execution or scheduler acceptance is observed, and terminal evidence only after the responsible capability confirms completion. Remove a temporary handoff pin after recording completion; retain any existing user pin. The [evidence schema](schemas/v1/scratch-evidence.schema.json) binds the observation to the exact task, its observation time, and the capability that supplied it. `--evidence -` reads the same JSON from standard input. Recording evidence does not execute or query a scheduler.

Completed, unpinned tasks become eligible for normal garbage collection after the retention period. Tasks without completion evidence remain protected. Pinning, evidence updates, stage use, and deletion coordinate through a shared lock; a failed update cannot recreate a task that GC has removed. These guarantees require working cross-process filesystem locks on the configured managed roots. GC remains preview-first: add `--apply` only for an authorized cleanup.

Retention uses the last preparation or observed active use. Recording a terminal observation or changing a pin does not restart it. Site observations retain their own timestamps.

## Checks through the same CLI

```bash
runhand storage check /existing/target-parent --json
runhand retry check --evidence preflight.json --require-allowed --json
```

The storage check makes tiny temporary probe directories and exits nonzero with per-parent diagnostics when publication is unsupported. Ordinary retries use Site observations directly; no RunHand JSON is required. The optional retry checker is for integrations that already supply structured evidence. Without `--require-allowed`, inspect `data.decision`; with it, blocked/unknown decisions fail with exit 3 before dispatch. The CLI never queries or submits a scheduler job.

## Bounded context discovery

`context.max_entries` limits workspace directory entries actually enumerated. A directory that cannot be fully enumerated within the remaining budget is omitted and reported as partial; this can also happen at an exact budget boundary. Complete directories are sorted before use, so an incomplete directory's filesystem enumeration order cannot silently select the precedent. Increase the budget or supply a narrower workspace when the needed candidates were omitted.

## Codex plugin

This repository is itself the `runhand` plugin root. The manifest is [`.codex-plugin/plugin.json`](.codex-plugin/plugin.json). [`runhand`](skills/runhand/SKILL.md) handles safe Run/stage/scratch transitions, [`runhand-submit`](skills/runhand-submit/SKILL.md) handles submission and retry evidence before invoking Site, and [`runhand-maintenance`](skills/runhand-maintenance/SKILL.md) handles doctor and cleanup requests.

The distributed guides explain how to apply installed Simulator/Site skills and pass actual paths and commands to the next operation. Start with the [CLI setup](skills/runhand/references/cli-setup.md), then follow the [working-copy example](skills/runhand/references/create-work.md) for creation or the scratch procedure for a bounded test. The [submission skill](skills/runhand-submit/SKILL.md) covers ordinary retries; a [structured preflight example](skills/runhand-submit/references/retry-preflight.md) is available for integrations.

Simulator-only questions and bounded read-only analysis route to the matching specialist. Identified-job status and cancellation route to Site. Submissions and scheduler-only retries use the submission companion even when no filesystem transition is needed. A retry needs fresh, complete evidence for the relevant Run and sites/clusters, including resolution of any prior unknown submission; an empty queue or an unavailable query does not establish that a retry is safe. The CLI and submission helper never call a scheduler. Routing examples are recorded in [`evals/skill-routing.json`](evals/skill-routing.json).

The reduced CLI no longer manages submission history or provider request/response contracts. Existing `state/history` files are left untouched, including by `gc --kind all`. Remove retired `[behavior]` settings and `[scratch].max_gib` / `history_ttl_days` from old configuration files. Verification and observation choices belong to the requested workflow; the unused CLI knobs have been removed. The standalone storage/retry wrappers are replaced by their CLI commands above.

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

On an HPC login node, route the test controller through the site's compute-node mechanism.
