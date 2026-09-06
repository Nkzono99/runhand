---
name: runhand-submit
description: Submit an existing or newly created simulation Run, retry or restart it, or recover a submission whose response was lost. Use the site's installed skills or tools to operate the scheduler and this guide to check previous attempts and report the outcome. Ordinary status or cancellation of a known Job ID belongs directly to the site's skill.
---

# Submit a simulation Run

Start with a Run directory or prepared scratch working tree and the user's requested action. Use the installed site's commands to operate the scheduler and the simulator's instructions to prepare valid inputs. Carry forward the paths, command, resources, and current checks from the preceding workflow; this handoff does not restart those checks. A requested bounded scratch test may use a scheduler allocation without becoming a production run.

| Starting situation | What to do |
|---|---|
| A Run or scratch working tree was just created and has no previous attempt | Prepare the job, validate it, and submit once. |
| An existing Run will be reused, retried, or restarted | Establish that no attempt is active or unresolved before changing execution files and submitting again. |
| A previous submit call lost its response | Reconcile that call first. Another submission also needs a retry request and evidence that no attempt remains active or unresolved. |

A request to create a Run alone does not include submission. For scientific changes that require a new Run, use [the Run creation guide](../runhand/SKILL.md) first. Keep the existing Run for continuation of the same trajectory when the simulator's restart procedure supports it.

## 1. Identify the actual working directory and command

Resolve the requested Run to an absolute path and identify its job script or execution command. Use a path supplied by the user or returned by promotion directly. Keep the working directory associated with that command: relative input and output paths often depend on it.

Use the installed site skill or tool for the actual host. A skill can be a written procedure: read it and run its documented commands yourself. For example, KUDPC's routing and Slurm skills explain host/module inspection and queue/resource discovery. Reuse that information from this workflow when it is still applicable. Do not invent a provider API, scheduler flags, queue, or cluster name.

Use the simulator's input and restart instructions to identify the actual files and executable. A command selected from workspace evidence needs trusted review before execution. A user-specified exact command can run with unavailable simulator validation, reported as unvalidated, if the site permits its execution route.

If a required path or command is still ambiguous, inspect the Run's control files and nearby instructions to resolve it. Ask only when the remaining choice changes the user's requested operation, scientific identity, or material cost.

## 2. For an existing Run, settle earlier attempts

Identify earlier attempts from the conversation, Site receipts, and relevant logs. Use current Site observations already obtained in this workflow, or query the relevant site/cluster pairs using its documented commands. Batch queries when practical. Associate jobs using known Job IDs, submission tokens, or the actual submitted working directory and script; a similar job name alone is insufficient.

Before changing a reused Run's execution files, confirm that no attempt is active or unresolved. Pending and running jobs still use the Run. A known job missing from the queue needs accounting or other positive Site evidence of its terminal state. If the query fails, its coverage is insufficient, or a previous call's acceptance is unresolved, leave the files and scheduler untouched and report what is missing.

For a lost response, search by the pre-submit token when available. Otherwise use Site evidence that positively identifies that particular call. A missing search result is not proof of rejection. For recovery alone, return the identified job and state or report unknown; do not submit again. Ordinary retry uses these observations directly and needs no RunHand-specific JSON or history file.

## 3. Prepare and validate the requested job

You make the edits; the installed skills supply the rules for making them correctly.

- For a resource-only retry, use the site's guidance to change the requested walltime, queue, account, or resource settings in the intended job script, or use supported submit-time overrides. Inspect the resulting diff. Keep the existing Run and avoid changing its scientific inputs.
- For a restart, use the simulator's procedure to select the checkpoint and update restart controls. Confirm that this is continuation of the same trajectory. If the requested change instead defines a new simulation, return to the Run creation guide.
- For a newly promoted Run, work in the new target, keeping the source Run unchanged.

Use the current validation result for the final live inputs, executable, and command. If that result is missing or an edit made it inapplicable, perform the relevant simulator check once and carry its result through submission. Apply the same rule to the selected working directory, resources, cost, and site route. Do not add a smoke or pilot payload solely because production submission was requested. Stop on invalid inputs or a prohibited route, retaining the prepared Run; report unavailable validation explicitly.

Refresh scheduler observations only when a delay or new evidence makes them stale. These observations do not reserve the Run or protect concurrent callers from duplicate submission.

## 4. Call the actual submit command once

Capture the current UTC call time. If the site supports a searchable client token, create it before submission and pass it using the site's documented mechanism. Retain the target, site/cluster, token, and call time in the Site receipt or conversation. A Job ID learned only from the response cannot help find a job when that response is lost. Without a supported token, still perform an authorized first submission and report the recovery limitation.

Run the site's submit command from the validated working directory with the reviewed script and resources. For example, **only when the selected site instructions specify Slurm `sbatch`**, and all required resource directives are already in the reviewed script:

```bash
(
    cd -- "$RH_RUN" || exit
    sbatch "$RH_JOB_SCRIPT"
)
```

Here `RH_RUN` is the absolute Run directory or scratch working tree resolved in step 1, and `RH_JOB_SCRIPT` is the reviewed script's absolute path. Set both to the actual paths before using this example. For scratch, retain the separate allocated task path as `RH_TASK` for later liveness records. Use the equivalent documented command for another scheduler. Keep the tool's stdout, stderr, and exit status. Call the submit command at most once for this action; a timeout or lost connection leaves acceptance unknown.

## 5. Report the outcome and preserve useful work

| Observed result | Next action |
|---|---|
| The scheduler returned an accepted Job ID | Retain site, cluster, Job ID, and submit time. Return for production unless the request needs bounded startup or completion observation. |
| The scheduler definitively rejected the request | Report the error and leave the prepared Run available for correction. |
| Acceptance is uncertain | Look up the pre-submit token using the site's documented query if one exists. Report the matched job or `unknown_unresolved`; do not submit again. |

Use the scheduler's submit time when available, otherwise identify the captured UTC call time as a client timestamp. Claim protection against concurrent duplicate submissions only when the site supplies idempotency and callers actually share its action key and scope.

For scratch, carry the task path through the [scratch workflow](../runhand/references/scratch-work.md) and record the observed outcome there. Reuse the same execution and completion evidence; do not poll again solely to write metadata.

Return the Run path, the requested edits, validation result, actual submission command and resources, outcome, job identity when known, and any unresolved evidence or retained scratch path. Do not delete the Run or cancel an accepted job to undo a later failure.

## Optional check for machine workflows

An integration that already produces structured Site observations can use [the retry checker](references/retry-preflight.md) as a deterministic shell guard. It checks supplied facts without querying or submitting a job. Use it when that machine interface helps the caller; manually rebuilding ordinary Site output as JSON is not a prerequisite for retry.
