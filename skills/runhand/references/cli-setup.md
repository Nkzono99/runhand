# Set up the CLI when needed

Reuse a working command and known roots. Resolve only missing setup; a handoff does not require another doctor call or plugin search.

## Bind the command and roots

For an installed CLI, bind the intended existing research workspace; this example uses the current directory:

```bash
RH_WORKSPACE=$PWD
RH_BIN=(runhand)
RH=("${RH_BIN[@]}" --workspace "$RH_WORKSPACE")
```

Add known `--scratch-root "$RH_SCRATCH_ROOT"` and `--state-root "$RH_STATE_ROOT"` options when applicable. Keep those exact roots across hosts and handoffs; configuration changes do not move an existing task. The Site selects permitted storage and confirms visibility on the execution host.

When roots/configuration are unresolved, use `"${RH[@]}" doctor --json` once. Inspect `data.workspace`, `data.scratch.path`, `data.state.path`, `data.config`, ownership, and warnings, then bind the intended paths. Keep scratch outside the source and distinct from state. Use an empty dedicated root for new work rather than adopting an unowned nonempty directory; maintenance does not initialize roots or owner markers.

## Consume successful results

| Command | `--print-path` values |
|---|---|
| `stage create` | `stage`, `tree` |
| `scratch get` | `task` |
| `scratch prepare` | `task`, `workdir` |
| `promote` | `target` |

`--print-path` emits one absolute path, with diagnostics on stderr. Check success before writing under it; unattended shell scripts should use `set -euo pipefail`. The editable children are `$RH_STAGE/tree` and `$RH_TASK/work`.

Use `--json` for a receipt with `ok`, `data`, `warnings`, and `errors`; dependent steps stop on failure. Use `--dry-run --json` for a useful preview. Neither option combines with `--print-path`. For `retry check`, inspect `data.decision` or add `--require-allowed` to make blocked/unknown decisions fail.

## Missing or incompatible CLI

Use the plugin's bundled source with an available Python 3.11+ interpreter:

```bash
RH_PLUGIN_ROOT='/absolute/path/to/runhand-distribution'
RH_PYTHON=python3.11
RH_BIN=(env "PYTHONPATH=$RH_PLUGIN_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" "$RH_PYTHON" -m runhand)
RH=("${RH_BIN[@]}" --workspace "$RH_WORKSPACE")
```

Replace the root with the actual directory containing `src/`, `skills/`, and `schemas/`, two levels above `skills/NAME/SKILL.md`; preserve any explicit root options. Probe `scratch prepare --help` only when interface compatibility is uncertain. Report a missing source/interpreter rather than silently installing another version.
