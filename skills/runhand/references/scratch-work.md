# Complete temporary work

Use this guide for bounded smoke, pilot, debug, analysis, or preview work needing writable isolation. Resolve the workload limit, actual input fields, command, expected outputs, and failure indicators through Simulator instructions; obtain host, resources, storage, and runtime limits from Site instructions. Use the known CLI/roots or [CLI setup](cli-setup.md) when missing.

## Prepare the working directory

Select required input/script dependencies and relative-symlink targets. The example assumes `input.toml` and `job.sh` are required:

```bash
RH_TASK=$("${RH[@]}" scratch prepare \
    --source "$RH_SOURCE" \
    --include 'input.toml' --include 'job.sh' \
    --kind smoke --print-path task)
RH_WORKDIR="$RH_TASK/work"
```

Check success before using the paths. Preparation allocates fresh protected scratch and copies once; it performs no scientific edits or execution. Choose `pilot`, `debug`, `analysis`, or `preview` as appropriate. `--key` is optional metadata, not a reuse lookup. An existing `--plan "$RH_PLAN_FILE"` replaces inline selection flags.

Edit the requested bounded settings under `RH_WORKDIR`, inspect the diff, and keep task metadata intact. Scratch needs no stage, promotion, or `renameat2` probe.

For analysis with no source files to copy:

```bash
RH_TASK=$("${RH[@]}" scratch get --kind analysis --print-path task)
RH_WORKDIR="$RH_TASK/work"
mkdir -- "$RH_WORKDIR"
```

`scratch get` creates no `work/` child. Large analysis may read stable source data by absolute path and keep only scripts/intermediates here, avoiding an unnecessary data copy.

## Execute and inspect the result

Check the final inputs once, then use the Site launcher with working directory `RH_WORKDIR`, the actual command/environment/resources, workload bound, and result check. For scheduler dispatch, hand these and current checks to [runhand-submit](../../runhand-submit/SKILL.md). For synchronous work, retain exit status and stdout/stderr.

Use [scheduled observation](observe-work.md) for long work. The deadline is the user's budget or `data.config.completion_budget_seconds` from the existing configuration receipt (built-in 600 seconds); obtain missing configuration when needed. A Job ID alone does not complete a result check. At termination, inspect the Simulator's completion indicators and requested outputs, distinguishing scheduler/process termination from scientific success. At the deadline, retain pending/running/unknown work and report it as unfinished.

## Record observed state

Record terminal state once after the result check. Fresh unknown scratch is already protected; active/unknown records are useful for detachment or a handoff, not a reason for extra polling.

| Observation | `liveness` | `attempt_state` | Capability |
|---|---|---|---|
| Accepted job pending/running | `active` | `active` | `site` |
| All associated scheduler work confirmed ended | `terminal` | `terminal` | `site` |
| Submission acceptance unresolved | `unknown` | `unresolved` | `site` |
| Synchronous execution ended with no scheduler attempt | `terminal` | `none` | `site` or `simulator` |
| Current state cannot be established | `unknown` | `unknown` | `site` for scheduler work |

Capture the actual timezone-aware observation time in `RH_OBSERVED_AT` and actual provider identity in `RH_PROVIDER_LABEL`. For Site-confirmed termination of all associated work:

```bash
"${RH[@]}" scratch record "$RH_TASK" \
    --liveness terminal --attempt-state terminal \
    --observed-at "$RH_OBSERVED_AT" \
    --capability site --identity "$RH_PROVIDER_LABEL" --json
```

Use the table for other states. Queue absence does not establish termination; an intended launch does not establish active work. A payload ending inside an active scheduler allocation is not terminal scheduler evidence. Existing [structured evidence](../../../schemas/v1/scratch-evidence.schema.json) can use `--evidence FILE|-` instead of scalar flags, without mixing the forms.

Keep paths and observations if recording fails. Use actual timestamps; recording terminal state does not reset retention age. If node-local scratch disappeared, retain the result receipt rather than recreating the task.

## Reuse and cleanup when requested

Normal preparation is fresh. For an explicitly identified existing task, check applicability and absence of a current writer, then `scratch pin TASK --json` before reuse. Record observed active use after startup/acceptance, then completion; retain the pin until needed results are secured. Remove only a temporary handoff pin, preserving a user's existing pin.

Return task/output paths, reduced settings, command/Job ID, and the observed result. Save requested durable artifacts with sources and methods in the research tree. Cleanup uses [runhand-maintenance](../../runhand-maintenance/SKILL.md).
