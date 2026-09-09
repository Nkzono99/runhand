---
name: research-loop
description: Resolve a bounded computational research question through comparison, validity checks, and an evidence-based decision. Use for model validation or assessing whether a change matters; ordinary Run creation, submission, plotting, and explanations use the existing specialist skills.
---

# Reach one research decision

Use this skill when the requested outcome is a scientific judgment, such as “Does the incident VDF materially change the surface potential?” Keep the user's question and explicit scope authoritative.

## Define the decision before new work

Reuse the conversation and existing evidence. Briefly state the following in the response or an existing research note; no dedicated file or approval round is required:

- **Decision:** the question and conditions under which the answer will apply.
- **Comparison:** the baseline and smallest comparison that can answer it.
- **Observables:** measured quantities, meaningful differences, and how uncertainty affects interpretation.
- **Validity:** the implementation, numerical, and physical/model checks needed to trust this comparison. Select relevant checks with the Simulator instructions; reuse applicable evidence.
- **Limits:** excluded work and a bounded execution/observation budget, using the user's limits and Site constraints. If unspecified, choose a modest initial bound appropriate to the task.
- **Stop:** what evidence is sufficient for the decision, and what missing or invalid evidence would prevent it.

Do not choose interpretation thresholds after seeing results merely to obtain a preferred conclusion.

## Keep work within the question

By default, allow one primary path and at most two diagnostic branches across the whole task, unless the user specifies otherwise. A branch is a distinct added investigation, not a Run or tool call. Planned comparisons, required validation, and routine fixes belong to the primary path and still consume the overall budget. Closing a branch does not replenish the allowance.

Open a branch only if it can change the current conclusion, establish whether the evidence is valid, or unblock the primary comparison. Briefly state that reason before proceeding. Generalization, unrelated parameter sweeps, and architectural cleanup do not justify a branch by themselves. Keep only useful deferred ideas in a short parked note; do not execute them or turn them into follow-up tasks automatically.

Use Simulator/output instructions for scientific choices and interpretation. Use [runhand](../runhand/SKILL.md) when a new Run or writable scratch is needed, [runhand-submit](../runhand-submit/SKILL.md) for submission/retry, and Site instructions for execution and observation. Existing read-only evidence can answer the question without staging. Reuse current checks across handoffs.

Proceed autonomously with implementation, execution, debugging, and validation already authorized by the request. This skill does not expand that authorization. Ask only when missing information or a needed change to the question, major physical assumption, or material resource scope requires the user's choice; do not seek approval for each routine step.

## Stop and report

Stop adding work when the decision criteria and required validity checks are met. If further necessary work would exceed the branch or execution budget, report what was established and what prevents a decision; do not silently extend the budget or ask for an extension by default.

Lead with the scoped scientific conclusion. Use supported/rejected/inconclusive when evaluating an explicit hypothesis. Do not claim support or rejection from evidence that fails a required validity check; failure to detect a difference does not establish equivalence. Separate scientific interpretation from execution status: pending/running jobs or unresolved observations mean the requested work is unfinished, not a completed inconclusive study. Follow the existing observation limits and retain Job IDs and paths for resumption.

Include the decisive measurements and uncertainty, checks actually performed and their limits, and changed inputs or methods with source/output references. When saving a requested research artifact, preserve the evidence needed to interpret or reproduce it in the existing research tree; a GC-managed scratch path alone is not durable evidence.

Mention unresolved limitations and useful parked ideas only when present. No mandatory parking-lot file, state YAML, or new task list is needed.
