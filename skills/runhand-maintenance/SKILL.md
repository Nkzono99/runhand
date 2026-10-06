---
name: runhand-maintenance
description: Inspect RunHand configuration and clean its disposable scratch, stages, or cache, including 「古いscratchを掃除して」.
---

# Maintain disposable RunHand work

Use this skill for RunHand-owned temporary work and cache. Formal Runs, ordinary project files, and submission history stay outside its cleanup scope. Apply the shared [work policy](../runhand/references/work-policy.md) once per task.

Reuse the known CLI and roots. Read [CLI setup](../runhand/references/cli-setup.md) when those are unresolved. Keep the same explicit roots when the Site routes commands to another host. Size scans can require a compute or I/O route even during a preview.

## Choose the operation

| Request | Procedure |
|---|---|
| Inspect configuration or diagnose roots | Run `doctor --json` once; inspect resolved paths, ownership, writability, and warnings. |
| Clean old scratch/stages or cache | Read [bulk cleanup](references/cleanup.md#bulk-cleanup); preview and apply the requested kind and age. |
| Resolve an item retained by GC | Read [protected items](references/cleanup.md#protected-items); use applicable Site evidence. |
| Discard one identified abandoned task/stage | Read [exact discard](references/cleanup.md#exact-discard); recheck liveness and preview that path. |

A diagnostic or preview request ends with the report. A cleanup request authorizes applying its matching preview; reuse that authority rather than asking again for `--apply`. Ask when the destructive target or authority to discard protected work remains unclear.

## Preserve and report

Bulk cleanup retains pinned, active, unresolved, fresh, or busy work. Resolve the reported condition; preserve CLI refusals rather than bypassing them with shell deletion or owner-marker edits.

Report the scope, roots, actual removals, and meaningful protected items. Size totals are estimates, not measured reclaimed space. A failed apply may have deleted some candidates; use its receipt and current state to report the outcome.
