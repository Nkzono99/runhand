# Observe long-running work

Use this guide when the request needs completion observation. Submission-only production work completes on acceptance; startup-only observation takes one snapshot and reports pending or unavailable startup without polling.

Estimate execution time separately from queue delay. Retain the Job ID/session handle, paths, launch time, original observation deadline, and next check time in the existing task context. Ensure the execution method returns control and preserves completion/exit evidence.

Take one startup snapshot when useful for detecting an early failure, then check near expected completion or a meaningful milestone. With unknown timing, an interval around one minute, lengthening to two and five minutes when little changes, is a useful starting point. Respect user intervals and the remaining budget.

Between checks, advance independent work within the request. Preserve files the running job still uses, and treat incomplete outputs as unfinished. Check at the planned time, on an available completion notification, or when concrete failure evidence warrants it. Use bounded snapshots and batch jobs by Site/cluster; avoid `watch`, `tail -f`, and repeated short wait/status calls.

When no independent work remains, use an available notification or timed wait/yield within the host's responsiveness limit. Brief updates should describe the next check and useful work, rather than repeat unchanged status.

At termination, inspect the requested Simulator/output result and reuse that evidence for scratch recording. Queue disappearance alone does not establish termination or success. At the original deadline, report unfinished work with its last observed state, identity, and paths; retain protected work without automatically cancelling, resubmitting, or extending the budget.
