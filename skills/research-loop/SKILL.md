---
name: research-loop
description: Compare simulation models or parameters to judge whether a change matters, including 「比較して検証して」「差が有意か判断して」.
---

# Reach one research decision

Use this skill for a scientific judgment about a simulation change, including a decision from completed results. Explanations and plots without such a judgment use Simulator/output instructions. Apply the shared [work policy](../runhand/references/work-policy.md) once per task.

## Define and resolve the question

Before new work, state the question, smallest useful comparison, decisive observables, required validity checks, stopping condition, and execution/observation budget in the conversation or an existing note. Set interpretation thresholds before inspecting new comparison results. Use the user's limits and Site constraints; choose a modest initial bound when none is supplied.

Use existing valid evidence first. Add an investigation when it can change the conclusion, establish validity, or unblock the comparison. Concentrate on the supported working hypothesis and revise it with new evidence. Follow explicit user limits; there is no fixed default count of diagnostic branches.

Use Simulator/output instructions for scientific methods and interpretation. Use [runhand](../runhand/SKILL.md) for new cases or writable scratch, [runhand-submit](../runhand-submit/SKILL.md) for prepared submissions, and Site instructions for execution. Reuse current checks across handoffs. For long work, use [scheduled observation](../runhand/references/observe-work.md).

Continue authorized implementation, execution, debugging, and validation until the decision and required checks are complete or the agreed budget expires. Ask when a necessary change to the question, major physical assumption, or material resource scope needs the user's choice.

## Report the decision

Lead with the scoped conclusion and decisive measurements, uncertainty, and validity checks. Use supported/rejected/inconclusive for an explicit hypothesis; evidence that fails a required check cannot support or reject it. Pending work is unfinished research, with Job IDs and paths retained for resumption.

Save requested durable artifacts in the research tree with sources and methods; managed scratch alone is not durable evidence. Include limitations and next actions that affect the judgment.
