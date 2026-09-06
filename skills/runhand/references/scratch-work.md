# Prepare, run, and finish temporary work

Use this procedure for a bounded smoke, pilot, debug, or analysis request that needs writable temporary files. The worked example is “run this source for 20 steps and check the result.” Its success condition is completion of those 20 steps with the Simulator's required outputs and no fatal evidence. Receiving a Job ID alone does not complete that request.

## 1. Define the small run before copying

Use the Simulator facts already established for this source, reading its instructions only as needed. Identify the actual step-limit field, restart implications, required inputs, executable/job script, and normal/fatal indicators. Record the original value and intended value 20. For analysis, identify the selected inputs, analysis command, expected artifact, and runtime bound instead.

Use current Site evidence for the route, resources, runtime limit, and scratch location; obtain missing facts using the installed Site instructions or exposed tools. Keep the launcher, environment, arguments, working directory, and outputs explicit. A written Site skill is a procedure to follow, not a `Site.submit()` API.

Use [CLI setup](cli-setup.md) to keep the chosen workspace and roots fixed. Keep required inputs and the work directory visible to the eventual execution host.

## 2. Allocate a task and put the inputs inside it

Use `scratch prepare` when the temporary payload needs copied inputs. It allocates one fresh protected task and copies the selected source files once into its `work/` child. It performs no scientific edits, validation, execution, or formal Run creation.

Set `RH_SOURCE` to the absolute source Run identified in step 1. Select the actual required files using the Simulator's instructions, or an explicit selection supplied by the user when no Simulator is available. Include required relative-symlink targets and omit generated outputs. Do not use `--include '**'` as an automatic fallback.

The following example assumes the required source files are `input.toml` and `job.sh`. Replace those relative patterns with the actual files established above:

```bash
RH_TASK=$("${RH[@]}" scratch prepare \
    --source "$RH_SOURCE" \
    --include 'input.toml' --include 'job.sh' \
    --kind smoke \
    --print-path task)
RH_WORKDIR="$RH_TASK/work"
```

Check that preparation succeeded before using either path. Choose `analysis`, `pilot`, `debug`, or `preview` when appropriate. Add repeated `--exclude PATTERN` flags for known disposable files. `--key` is optional descriptive metadata; allocation remains fresh and performs no automatic reuse search.

If a Simulator integration already supplied a versioned copy-plan JSON file, replace the selection flags with `--plan "$RH_PLAN_FILE"`; do not combine `--plan` with the inline selection flags. For a full receipt, use `--json` instead of `--print-path task` and read `data.task`, `data.workdir`, `data.copy_plan`, `data.selection`, and `data.summary`. Use the same options with `--dry-run --json` when a preview resolves a real uncertainty. A dry-run creates neither task nor work directory.

Edit the identified step-limit field inside `RH_WORKDIR` to 20, apply the Simulator's restart rules, and inspect the actual diff. Keep the source and the task-root `.runhand-task.json` intact. All copied inputs and generated work belong under `RH_WORKDIR`. Perform execution validation once on this final working tree in step 3.

This mutable scratch flow copies directly into a unique task. Only formal Run publication through `promote` requires atomic `renameat2`; internal stages and scratch preparation do not. The CLI owns the task before writing its files. Preparation failure does not produce a usable work path; inspect the error and report any retained temporary path before proceeding.

For large analysis inputs, follow the Simulator/output capability's supported read method. It may be better to read stable original data by absolute path and put only the analysis script and writable intermediates under `RH_WORKDIR`. Identify those paths explicitly; do not silently read a file still being written. Avoid a second large copy merely to fill the work directory.

When no source files need copying, allocate an empty task and create its working directory for the analysis script and writable outputs:

```bash
RH_TASK=$("${RH[@]}" scratch get --kind analysis --print-path task)
RH_WORKDIR="$RH_TASK/work"
mkdir -- "$RH_WORKDIR"
```

`scratch get` itself creates no `work/` directory. Check allocation before creating or writing to the working directory.

## 3. Execute the prepared work and observe its result

Apply the needed Simulator checks to the final live inputs and launcher, reusing current applicable results. Plan annotations such as `partial` are not execution gates; actual missing or invalid dependencies are. Hand the Site execution operation these values:

| Value | Where it comes from |
|---|---|
| Working directory | `RH_WORKDIR`, not the source or the task metadata directory |
| Payload command and arguments | Simulator-checked executable/script in the copy, or a confirmed shared executable |
| Environment and resources | Current Site instructions and selected queue/host |
| Runtime/output bound | The user's 20-step request plus the Site time limit |
| Observation | Wait for this bounded result; use the configured completion budget unless the user gave one |
| Result check | Actual step count, required outputs, and normal/fatal markers identified in step 1 |

Apply the Site's launcher instructions. For scheduler dispatch, pass these values and current validation/route evidence to [runhand-submit](../../runhand-submit/SKILL.md) with bounded completion observation. That handoff does not require repeating checks just completed on this same working tree. For synchronous execution, retain exit status and stdout/stderr. A copied script's mere presence is insufficient evidence to execute it.

While a scheduler job is pending/running, retain its Job ID and task path. If the observation budget expires, report that the bounded check remains pending/running and leave the task protected. Do not report successful completion or cancel it automatically. The current default completion budget is `data.config.completion_budget_seconds` from `doctor` (built-in 600 seconds).

Once it terminates, read the relevant logs/output using the Simulator instructions. Report both the scheduler/process result and whether the intended 20-step check passed. A failed calculation can be terminal and safe to clean up later.

## 4. Record observed task state

For an ordinary bounded task, record terminal state once after observing its result. A fresh task is protected as unknown while it runs, so intermediate writes are unnecessary. Record active/unknown state when leaving a detached task or when a handoff benefits from that evidence; do not query a scheduler solely to update metadata. Use this [scratch evidence](../../../schemas/v1/scratch-evidence.schema.json) mapping:

| Observed situation | `liveness` | `attempt_state` | `source.capability` |
|---|---|---|---|
| Scheduler accepted; work is pending or running | `active` | `active` | `site` |
| Scheduler confirms all associated work has terminated | `terminal` | `terminal` | `site` |
| Submission response lost, acceptance unresolved | `unknown` | `unresolved` | `site` |
| Synchronous execution finished without a scheduler attempt | `terminal` | `none` | `site` or `simulator`, according to who checked it |
| Observation cannot settle the current state | `unknown` | `unknown` | `site` for scheduler-associated work |

Do not select `terminal` from an empty queue listing. Do not select `active` from an intention to execute. `observed_at` is the timezone-aware time the evidence was obtained; `source.identity` names the actual skill/tool/provider used to establish it. Neither field is a permission token.

Immediately after obtaining the observation, capture its time if the Site tool did not already provide an appropriate timestamp:

```bash
RH_OBSERVED_AT=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
```

Keep that timestamp with the observation. Set `RH_PROVIDER_LABEL` to the actual skill/tool/provider and version used. For **Site-confirmed termination of all work associated with this task**, record the state directly:

```bash
"${RH[@]}" scratch record "$RH_TASK" \
    --liveness terminal --attempt-state terminal \
    --observed-at "$RH_OBSERVED_AT" \
    --capability site --identity "$RH_PROVIDER_LABEL" --json
```

For another situation, select the explicit `--liveness`, `--attempt-state`, and `--capability` values from the table. A synchronous payload that ended while its scheduler allocation is still active does not establish terminal scheduler state. Record `terminal`/`none` only when there was no associated scheduler attempt.

The CLI constructs and validates the existing evidence schema from these scalar fields; it does not perform a live query or infer success. When an integration already provides a full evidence object, or additional structured `details` are needed, retain `scratch record "$RH_TASK" --evidence FILE|- --json`. Supply either that object or the complete scalar flags, without mixing the two forms.

Keep the task path and observed result even if recording fails. Check stale/conflicting evidence against current facts rather than fabricating a later timestamp. Unknown tasks remain protected from bulk GC. Recording terminal evidence does not restart the task's usage-age retention period. If node-local scratch has disappeared after its allocation ended, keep the observation in the receipt; do not create a replacement task or treat archived metadata as the original task.

## Optional reuse and cleanup

The normal path allocates fresh scratch. When the user explicitly identifies an existing task for reuse, check applicability and absence of a current writer, then pin that exact path with `"${RH[@]}" scratch pin "$RH_TASK" --json` before opening a writer. After observing actual startup or scheduler acceptance, record active evidence using the mapping above; this advances its usage time. Pinning and a later terminal observation alone do not record new use. If startup cannot be observed separately, use a fresh task or keep the existing task pinned until its needed results are secured. Remove a temporary handoff pin after saving completion evidence; preserve a user's existing pin. Fresh tasks need no extra handoff pin.

Return the task path, the reduced settings, the command/Job ID, and the observed pass/fail/pending result with its supporting output paths. Save a durable result to the research tree when the user requested that artifact, including sources and method. Retain scratch otherwise; perform cleanup through [runhand-maintenance](../../runhand-maintenance/SKILL.md) when cleanup is requested.
