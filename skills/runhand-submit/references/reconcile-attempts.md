# Reconcile previous attempts

Use this guide for a reused Run, retry/restart, or lost submission response. Carry forward known paths and Site receipts.

## Establish the current state

Identify attempts using Job IDs, client tokens, or the actual submitted directory/script across the relevant site/cluster pairs. A similar job name alone is insufficient. Reuse fresh, complete Site observations; otherwise query through the Site's documented commands, batching when practical.

Before changing reused execution files or submitting again, establish that every relevant attempt is terminal and no prior submission remains unresolved. A known job absent from the queue needs positive accounting or equivalent Site evidence. A failed query or incomplete coverage leaves the decision unresolved and the Run unchanged.

These snapshots do not reserve the Run or provide submission idempotence.

## Recover a lost response

Search the pre-submit token when available; otherwise use evidence that identifies that particular call. A missing search result does not prove rejection. Return the identified job/state for recovery alone. Another dispatch requires the user's retry request and completed reconciliation.

For each new submit call, retain its time, target, site/cluster, and any supported searchable token before dispatch. Without such a token, an authorized first submission may still proceed; report the recovery limitation if it matters.

## Prepare the settled Run

A scheduler-only retry keeps the scientific inputs and changes only requested resources, queue, or walltime. A restart follows the Simulator's checkpoint procedure for continuation of the same trajectory. A new scientific case returns to [runhand](../../runhand/SKILL.md).

Resume [submission](../SKILL.md) with current checks and dispatch once. Report acceptance uncertainty and retain the prepared Run if reconciliation remains unresolved.
