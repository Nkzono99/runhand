# From a source Run to a new Run

Use this procedure for a new formal Run. For bounded temporary execution, use [scratch-work.md](scratch-work.md): `scratch prepare` combines allocation and one input copy, so that flow needs neither a separate stage nor publication.

The worked example assumes the Simulator instructions establish that `input.toml`, `model.dat`, and `run.sh` are required, generated `output/` is disposable, and one `temperature_K = 300.0` setting must become `500.0`. Obtain the real files, field, and unit from this case's Simulator instructions before adapting the example.

## 1. Bind the source and select the inputs

After [CLI setup](cli-setup.md), set `RH_SOURCE` to the source Run's absolute resolved path. For example, when the user selected `base` under the workspace:

```bash
RH_SOURCE=$(cd -- "$RH_WORKSPACE/base" && pwd -P)
RH_COPY_ARGS=(
    --source "$RH_SOURCE"
    --include 'input.toml' --include 'model.dat' --include 'run.sh'
)
```

Build this selection by reading the input and executable/job script. Include auxiliary data, the executable or confirmed shared executable, and restart files when this is a continuation. Check that omitted outputs are not required initial conditions. A Simulator skill provides the rules; it need not implement a copy-plan service.

Repeat `--include` and `--exclude` for the patterns actually needed. Quote patterns so the shell does not expand them in the wrong directory. They are relative POSIX paths: `*` matches within a path component and `**` matches whole components recursively. For example, `--include 'inputs/**'` selects a known input directory. An unexamined `--include '**'` is not a fallback.

The CLI builds its copy selection from these arguments. No basis, reason, or completeness declaration is required. Existing JSON plans can retain those annotations, but they do not authorize execution or prevent it by themselves. Resolve actual missing dependencies and invalid inputs before execution; do not demand a completeness label merely to proceed.

When an integration already supplies a [copy-plan JSON](../../../schemas/v1/copy-plan.schema.json), set `RH_COPY_ARGS=(--source "$RH_SOURCE" --plan "$RH_PLAN_FILE")` instead. `--plan -` reads it from stdin. Do not mix `--plan` with inline selection flags. The CLI checks both forms with the same parser and source/copy rules.

An internal relative symlink is usable only when its entire target path remains resolvable in the copy. An excluded target, external link, or special file causes rejection. Correct the selection with specialist guidance instead of substituting an unrestricted recursive copy.

## 2. Create the editable stage

Run substantial copies through the Site's permitted route:

```bash
RH_STAGE=$("${RH[@]}" stage create "${RH_COPY_ARGS[@]}" --print-path stage)
RH_TREE="$RH_STAGE/tree"
```

Check the command's exit status before using these paths. `RH_STAGE` is the managed container; `RH_TREE` is its documented editable child. Pass `RH_STAGE` to `stage inspect` and `promote`. A successful `--print-path` writes only the requested path to stdout and sends warnings to stderr; a failure writes no stdout path and includes diagnostics on stderr.

When you need a full receipt or want to inspect the selected files before copying, replace `--print-path stage` with `--json` or `--dry-run --json`. The JSON includes `data.copy_plan`, `data.selection`, and `data.summary`; actual creation also returns `data.stage` and `data.tree`. A preview creates no paths. Inspect an existing stage with `"${RH[@]}" stage inspect "$RH_STAGE" --json` if its selection or warnings need review.

## 3. Make and check the requested change

For the example, open `$RH_TREE/input.toml` and change its single `temperature_K` value from `300.0` to `500.0`. Leave `$RH_SOURCE/input.toml` unchanged. Use the exact file, field, units, species, and region identified by the Simulator instructions; a similarly named field is insufficient.

Re-read the value and compare the edited files with the source. The example's intended diff is:

```diff
-temperature_K = 300.0
+temperature_K = 500.0
```

For create-only work, apply parser/static checks when they resolve a meaningful uncertainty; creation can finish as unvalidated. For create-and-execute, leave full execution validation to the final live target below. Reuse an applicable recent check when the relevant inputs are unchanged; do not run the same parser again solely because the copy is being handed to another skill. Actual invalid inputs, missing dependencies, and prohibited execution remain reasons to stop execution.

## 4. Publish the copy

Set `RH_DESTINATION` to the requested new formal Run, typically a sibling of the source. Its parent must exist; create a requested new series parent if needed. Keep it outside the source and managed scratch.

```bash
RH_WORKDIR=$("${RH[@]}" promote "$RH_STAGE" "$RH_DESTINATION" --print-path target)
```

On success, `RH_WORKDIR` is the complete published target. For a full receipt, use `--json` instead; it reports `data.target` and `data.published=true`. Publication never replaces an existing destination.

For creation only, return the path, actual changes, and checks performed. For requested execution, pass the path, resolved command/job script, resources, observation request, and current evidence to [runhand-submit](../../runhand-submit/SKILL.md) or the Site's synchronous launcher. The execution owner verifies the live target and obtains only missing or stale checks; it does not restart source discovery or repeat applicable validation.

## If a step fails

| Observed result | Next action |
|---|---|
| `scratch_overlaps_source` | Choose an external scratch root, rebuild `RH`, and prepare a fresh copy. |
| `source_changed` | Establish a stable source or suitable restart snapshot through Simulator/Site instructions before another copy. |
| `target_exists` | Reuse that exact target only if it already satisfies the request; otherwise resolve a new name. |
| `managed_root_busy` | Inspect the stage/target after the current operation finishes before retrying publication. |
| `atomic_noreplace_unavailable` or `atomic_publish_failed` during promotion | Follow [storage-check.md](storage-check.md) for the formal destination. Internal stage creation does not require `renameat2`. |
| Success with `stage_state_update_failed` | The target exists. Keep and use it; report the metadata warning rather than publishing again. |

A failed creation may have removed its incomplete temporary stage. Report what actually remains. Keep a successfully published Run if validation or submission later fails; that later failure does not authorize deleting it or canceling an accepted job.
