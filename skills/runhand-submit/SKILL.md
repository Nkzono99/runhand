---
name: runhand-submit
description: Submit, retry, or restart prepared simulation Runs, including 「流して」「再投入して」 and lost submission responses.
---

# Submit a prepared Run

Start with the actual Run or scratch working directory, command, resources, and current evidence. Apply the shared [work policy](../runhand/references/work-policy.md) once per task. Scientific changes needing a new case use [runhand](../runhand/SKILL.md); known-job status/cancellation uses Site instructions.

## Choose the action

- For a new prepared Run, use current Simulator checks and the Site's live execution route.
- For a reused Run, retry, restart, or lost submission response, read [reconcile attempts](references/reconcile-attempts.md) before editing execution files or dispatching work. Active or unresolved attempts must be settled first.
- A recovery-only request ends with the identified attempt or its unresolved status.

## Prepare and dispatch

Apply requested scheduler edits through Site instructions and restart edits through Simulator instructions. Reuse applicable validation; refresh it after relevant changes. Invalid inputs or a prohibited/unknown Site route stop execution. Report unavailable Simulator validation; an explicit command can still proceed when the Site permits it.

Record the call time and, when the Site supports it, a searchable client token before submission. Retain the target and site/cluster with that identity. Use the Site's actual submit command from the validated working directory and capture stdout, stderr, and exit status. RunHand's CLI does not operate the scheduler.

Dispatch once for this action. A timeout or lost connection leaves acceptance unknown; follow the reconciliation guide instead of submitting again. Production submission completes with the accepted Job ID unless startup or completion observation was requested.

## Complete the handoff

For requested completion, use [scheduled observation](../runhand/references/observe-work.md). For scratch, retain the task path and record the outcome through [scratch work](../runhand/references/scratch-work.md), reusing the same observations and deadline.

Return the path, edits, checks, command/resources, submission outcome, and known job identity. Retain useful prepared work after a later failure.

For an integration that already supplies structured Site evidence, [retry-preflight.md](references/retry-preflight.md) provides an optional machine check; ordinary retries use Site observations directly.
