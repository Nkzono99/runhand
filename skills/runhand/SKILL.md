---
name: runhand
description: Create simulation case variants or isolate bounded tests and analyses. Use for parameter sweeps, 「条件を変えて流して」「少し試して」.
---

# Prepare a Run or temporary work

Use this skill for a new simulation case or writable isolation of a bounded check. The user need not name RunHand or scratch. Apply the shared [work policy](references/work-policy.md) once per task.

## Choose the workflow

| Requested outcome | Read and complete |
|---|---|
| New case or parameter variants | [Create a Run](references/create-work.md); return the published path and actual changes. |
| Smoke, pilot, debug, or analysis with temporary writes | [Scratch work](references/scratch-work.md); return the observed result and output paths. |
| Submit, retry, or restart a prepared Run | [runhand-submit](../runhand-submit/SKILL.md). |
| Inspect storage or clean disposable work | [runhand-maintenance](../runhand-maintenance/SKILL.md). |

A scientific comparison uses [research-loop](../research-loop/SKILL.md). Physics explanations, read-only plots, and known-job status/cancellation use Simulator/output or Site instructions directly. Ordinary code edits and library unit tests use the coding workflow.

## Carry the relevant context forward

Use Simulator instructions for fields, units, required inputs, restart semantics, and result checks; use Site instructions for host, storage, resources, and execution. On KUDPC, enter through its installed task router. A live route check precedes execution or substantial copying.

Reuse the known command, roots, paths, and applicable checks. Read [CLI setup](references/cli-setup.md) only for unresolved setup. Use the named source first; bounded `context --json` discovery is useful when the source remains unclear. Ask when remaining choices materially change the case or cost.

Keep the source intact and edit the prepared tree. Physical changes normally create a new Run; scheduler-only changes keep the Run. Select actual dependencies rather than copying everything. Copy-plan annotations describe selection; missing dependencies and invalid inputs determine execution readiness.

## Finish the requested outcome

Creation ends with the requested target and changes. Execution includes the requested result check: an accepted Job ID alone does not complete it. Use [scheduled observation](references/observe-work.md) for long work, retaining the same deadline across handoffs.

Report the result, paths, performed checks, and unfinished work when present. Preserve created Runs and useful scratch after a later failure; report each case independently in a batch.
