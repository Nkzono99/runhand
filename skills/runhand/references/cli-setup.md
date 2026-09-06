# Set up one RunHand command

Reuse the command and roots already established in this session. A known installation and known storage need no doctor call, plugin-path search, or JSON helper.

## Normal path: installed CLI and known roots

When `runhand` is available, bind the intended research workspace. In this example it is the current directory; replace `$PWD` with the user's selected existing absolute path if different:

```bash
RH_WORKSPACE=$PWD
RH_BIN=(runhand)
RH=("${RH_BIN[@]}" --workspace "$RH_WORKSPACE")
```

If the Site has selected a scratch root different from the configured default, bind that exact path:

```bash
RH_SCRATCH_ROOT='/actual/site-approved/scratch-root'
RH+=(--scratch-root "$RH_SCRATCH_ROOT")
```

Replace the example path with the actual one. Add `--state-root "$RH_STATE_ROOT"` only when a specific state root is relevant and known. Keep the command's roots unchanged across preparation, execution, and cleanup, including when the Site routes work to another host. For an existing task, use the roots that own it; changing configuration does not move it.

The Site decides permitted hosts and storage visibility/lifetime. Read-only discovery and substantial copies can use different routes. Ordinary temporary preparation does not require testing `renameat2`; only formal publication uses that operation.

## Read command results

For a shell step that needs one newly created path, request that path directly. A successful command prints only the selected path on stdout; warnings and errors go to stderr. Check its exit status before using the value.

| Command | Available `--print-path` values |
|---|---|
| `stage create` | `stage`, `tree` |
| `scratch get` | `task` |
| `scratch prepare` | `task`, `workdir` |
| `promote` | `target` |

The documented editable children are `"$RH_TASK/work"` and `"$RH_STAGE/tree"`. Do not combine `--print-path` with `--json` or `--dry-run`; use `--dry-run --json` when a preview is useful. Unattended Bash scripts should use `set -euo pipefail` so a failed allocation cannot fall through into later writes.

Use `--json` when you need the full receipt, multiple fields, warnings as structured data, or a complex evidence decision. Each CLI command in this mode returns one object containing `ok`, `data`, `warnings`, and `errors`. A nonzero exit or `ok=false` stops dependent steps. Inspect `errors[].code` and `errors[].message`; do not extract paths from a failed command. Read warnings even after success.

For `retry check`, inspect `data.decision`: `blocked` and `unknown` are normal read-only decisions. A script can use `--require-allowed` when it needs a nonzero exit for either result. The submission guide explains when a retry check is needed.

## Only when configuration is unclear

Inspect the resolved configuration once, or reuse a current receipt:

```bash
RH_DOCTOR_JSON=$("${RH[@]}" doctor --json)
printf '%s\n' "$RH_DOCTOR_JSON"
```

Read `data.workspace`, `data.scratch.path`, `data.state.path`, and `data.config`. Copy the intended exact paths into `RH_WORKSPACE`, `RH_SCRATCH_ROOT`, and `RH_STATE_ROOT`, then bind them:

```bash
RH=("${RH_BIN[@]}" --workspace "$RH_WORKSPACE" --scratch-root "$RH_SCRATCH_ROOT" --state-root "$RH_STATE_ROOT")
```

These paths are configuration, not a Site execution-route decision. Do not initialize or adopt roots for cleanup, and do not manufacture owner markers. For new work, choose an empty dedicated root when an existing nonempty directory is unowned. Keep scratch/state distinct and scratch outside the copy source. Use [storage-check.md](storage-check.md) only when formal destination support is uncertain or publication fails.

## Only when the CLI is missing or incompatible

If compatibility is uncertain, `runhand scratch prepare --help` is a small feature check; it is not a mandatory probe on every use. When the installed command cannot run the documented interface, use the bundled source without installation:

```bash
RH_PLUGIN_ROOT='/absolute/path/to/runhand-distribution'
RH_PYTHON=python3.11
RH_BIN=(env "PYTHONPATH=$RH_PLUGIN_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" "$RH_PYTHON" -m runhand)
RH=("${RH_BIN[@]}" --workspace "$RH_WORKSPACE")
```

`RH_PLUGIN_ROOT` is the actual directory containing `src/`, `skills/`, and `schemas/`, two levels above the directory of this distribution's `skills/NAME/SKILL.md`. Replace the placeholder and choose an available Python 3.11+ interpreter. Preserve any explicit root options from the earlier command. If the source or interpreter is unavailable, report that prerequisite; do not install another version silently.
