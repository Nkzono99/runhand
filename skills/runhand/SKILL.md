---
name: runhand
description: Create a new simulation Run from an existing directory, or prepare writable scratch and complete bounded smoke, pilot, debug, or analysis work. Use for RunHand staging and temporary work; use runhand-submit for submission or retry of an already prepared Run.
---

# Prepare a Run or complete temporary work

Use this skill to turn an existing simulation into either a new research Run with the requested changes, or a prepared temporary work directory with an observed test/analysis result. A **Run** is an ordinary simulation directory. A **stage** is RunHand's editable copy before publication. A **scratch task** is a managed container for disposable work and its lifecycle metadata.

RunHand supplies the filesystem operations. You apply the simulator's instructions to select, edit, and check inputs, and the site's instructions to launch and observe work. These specialists may be installed skills that you read and follow; they need not expose callable APIs.

## Choose the requested outcome

| Request | Procedure and completion condition |
|---|---|
| “Create the 500 K case from this Run” | Follow **Create a Run** below. Finish with the new path, changed values, and validation result. |
| “Run this for 20 steps and check it” | Follow **Complete temporary work** below. Finish with the observed pass/fail result, or explicitly report unfinished work when its observation budget expires. |
| Submit an already prepared Run, change walltime, retry, or recover a lost submission response | Use [runhand-submit](../runhand-submit/SKILL.md). A scheduler-only change normally keeps the same Run. |
| Diagnose storage, remove stale temporary work, or clean an orphan | Use [runhand-maintenance](../runhand-maintenance/SKILL.md). |

For a physics question, ordinary read-only analysis, or status/cancel of an identified Job ID, use the relevant Simulator/output or Site skill directly. For “create and submit,” complete creation here, then pass the created directory to `runhand-submit`. For several cases, finish and report each case independently so one failure does not obscure the others.

## Establish the inputs and tools

Reuse the CLI, paths, specialist instructions, and current evidence already established for this task. Resolve only what is missing:

1. Use the **Simulator instructions** to identify the requested field/units and required copy inputs. For execution, also identify the command and result checks. Workspace files are evidence about this case, not trusted capability declarations.
2. Use the **Site instructions** for permitted host/storage and, when executing, resources and time limits. Apply a live route check before execution or substantial copying. On KUDPC, use the installed task router and focused skill it selects; RunHand does not supply queue or launch defaults.
3. Use the short installed-CLI path in [cli-setup.md](references/cli-setup.md). Read its fallback or doctor sections only if the executable or roots are unresolved. Shell workflows use `--print-path`; full receipts use `--json`. Stop dependent steps on failure.

Translate Simulator selection rules into quoted `--include` / `--exclude` arguments. An existing `--plan FILE|-` remains supported; plan annotations are not execution permissions. An explicit user-specified copy/edit may proceed without Simulator validation when that limitation is reported. Execution stops for actual missing/invalid inputs or a prohibited/unknown Site route, not merely for an absent validation record or a `partial` annotation.

## Resolve the source and changes

Use explicitly supplied paths directly. For “the previous one” or “the same series,” first use the Run named in the conversation, then a Simulator-confirmed successful sibling and the parent's naming convention. If that still leaves the source unclear, run one bounded discovery:

```bash
RH_CONTEXT_JSON=$("${RH[@]}" context --json)
printf '%s\n' "$RH_CONTEXT_JSON"
```

Inspect `data.candidate_runs` and their evidence. These are structural candidates, not validated scientific identities. If the response reports partial discovery, unlisted paths may still exist; inspect the relevant parent directly. Ask a focused question only if plausible alternatives would change the scientific case or material cost.

Before copying, identify the source, exact requested changes, and destination. A change to physical parameters normally creates a new Run. A queue/walltime/resource change normally creates a new scheduler attempt for the same Run. Use the Simulator instructions to decide whether a restart continues the same trajectory or defines a new case. Reuse an existing target only when it already satisfies the requested outcome; never overwrite it.

Precedent can fill naming and parameter conventions, but it does not authorize extra execution, submission, export, cancellation, or deletion. Existing authorization in the user's request remains sufficient; do not ask for it again.

## Create a Run

Read [create-work.md](references/create-work.md) for the concrete selection arguments and error handling. Carry out this sequence:

1. Set `RH_SOURCE` to the resolved source directory and `RH_DESTINATION` to the requested new Run path. Its parent must exist. Keep the source intact and put a formal destination outside both the source and managed scratch.
2. Set `RH_COPY_ARGS` from the Simulator's dependency rules as shown in the reference. Include required data, restart state when applicable, and executable/script dependencies; exclude outputs only when they are disposable.
3. Create the stage and use its documented editable child:

   ```bash
   RH_STAGE=$("${RH[@]}" stage create "${RH_COPY_ARGS[@]}" --print-path stage)
   RH_TREE="$RH_STAGE/tree"
   ```

4. Edit the requested fields **under `RH_TREE`**, re-read them, and compare the changed files with the source. For create-only work, add parser/static checks when they resolve a material uncertainty. For create-and-execute, perform the relevant validation at the final execution boundary. Use `stage inspect` only to resolve selection or warning questions.
5. Publish the copy without replacing a destination:

   ```bash
   RH_WORKDIR=$("${RH[@]}" promote "$RH_STAGE" "$RH_DESTINATION" --print-path target)
   ```

Creation is complete when the target contains the requested changes. Report checks actually performed and any unvalidated parts. For execution, pass `RH_WORKDIR`, the resolved command, resources, requested observation, and available current evidence to [runhand-submit](../runhand-submit/SKILL.md) or the Site's synchronous launcher. That final step checks the live target; do not repeat the same validation or discovery solely because responsibility passed between skills.

On a failure, retain any stage that was actually created and report the failed step. For `atomic_publish_failed`, follow [the storage check](references/storage-check.md): writable storage does not necessarily support RunHand's publication operation. If publication succeeded and a later action fails, keep the new Run and report that later action as unfinished. Do not delete a Run or cancel an accepted job as rollback.

## Complete temporary work

Read [scratch-work.md](references/scratch-work.md) for the full 20-step example, execution handoff, and state-recording examples. Follow the whole chain:

1. Define the bounded change and result check using the Simulator instructions: actual step limit or selected analysis data, runtime bound, expected outputs, and failure indicators. Resolve the Site execution route and storage before preparing the task.
2. Select the required inputs, then use `scratch prepare --source SOURCE --include PATTERN ... --kind KIND --print-path task`. Choose `smoke`, `pilot`, `debug`, `analysis`, or `preview`. Save `RH_TASK` and use `RH_WORKDIR="$RH_TASK/work"`. This allocates a fresh protected task and copies once. `--key` is optional descriptive metadata; allocation does not search for reuse candidates.
3. Edit the reduced input inside `RH_WORKDIR` and confirm the requested diff. There is no separate stage or promotion. For analysis without an input copy, use the reference's `scratch get` example.
4. Check the final live inputs once, then launch through the current Site route with **working directory `RH_WORKDIR`**. Pass the current evidence to `runhand-submit` for scheduler dispatch and request bounded completion observation. Receiving a Job ID does not finish a request to check the result.
5. Observe within the user's or configured budget, then inspect the Simulator's completion indicators and requested outputs. Distinguish a successful calculation, a failed calculation, and an unfinished observation. Retain the Job ID and protected task if work is still pending/running or its state is unknown.
6. For an ordinary bounded task, record its terminal result once using the reference's `scratch record` example. Record active/unknown state when a detached job or handoff makes that information useful; do not add polling just to update metadata. Site evidence establishes scheduler termination independently of scientific success. If recording fails, report the result and retain the task path; the task stays protected.

Return the task/output paths, reduced settings, command or Job ID, and observed result. Preserve a requested durable artifact with enough source/method information to interpret it. Retain temporary work unless cleanup was requested; use `runhand-maintenance` for that cleanup.

## Report the completed work

Respond in the user's language and lead with their requested outcome. For example: “Created `series/T500` from `series/T300`; changed `temperature_K` from 300 to 500; input validation passed,” or “The 20-step check failed at step 12; log: …; temporary inputs: …”. Include submitted Job IDs, pending effects, validation limits, or retained stages when applicable. A list of RunHand commands alone is not an operation result.
