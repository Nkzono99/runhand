# Optional machine check for retry evidence

Use this interface when a script or integration supplies structured Site observations and needs a deterministic retry decision. Ordinary retries follow the [submission guide](../SKILL.md) using Site observations directly; they do not require this JSON. The checker is useful for machine workflows spanning several attempts or clusters, and never queries or submits a job itself.

## Gather observations before writing the JSON

The caller supplies the exact Run directory, relevant site/cluster pairs, and the current observations gathered using Site instructions. Reuse those observations from the current workflow; do not repeat queries merely to fill this format. A current queue query shows queued and active jobs; accounting may be needed to establish that a known earlier job is terminal.

Associate a job with this Run using a retained job identity, a recorded submission token, or the submitted working directory together with the known job script. A similar job name or filename alone is insufficient. Obtain job details with the site's supported inspection command when the queue summary omits these fields. Query accounting for the known earlier Job IDs and their submit times when needed. If an attempt cannot be identified well enough to populate its identity, retain that uncertainty instead of dropping the attempt.

Before declaring coverage complete, establish that the live search covers the relevant user's jobs on each known cluster and is not restricted by a job-name filter that could miss this Run. Account for the previous attempts collected from the receipts or logs; a known Job ID disappearing from the queue needs its terminal state confirmed. When the available site commands or retention window cannot establish this coverage, use `partial` or `unknown` and explain the gap. The checker has no independent way to repair that missing information.

`not_before` is the start of the observation batch, not the later time when JSON is assembled. Keep the timestamp captured by the caller before its live queries. For example:

```bash
date -u +%Y-%m-%dT%H:%M:%SZ
```

Capture the observation time after each query completes for that query's `observed_at`. Both values must be from the present preflight, with `observed_at` no earlier than `not_before` and no later than the clock when the checker runs. If the clocks disagree, resolve the discrepancy or report unknown; do not adjust timestamps to force an allowed decision.

Keep the raw query output or its tool result while constructing the JSON. Apply the site's state meanings: queued/running is `active`; a positively identified finished, failed, or cancelled job is scheduler `terminal`. Scheduler terminal state does not mean that the simulation succeeded. Unknown or contradictory job evidence remains `unknown` or `unresolved`.

Use these field meanings:

| Field | Where it comes from |
|---|---|
| `target` | The same canonical absolute Run path in the request and every query. |
| `scopes` | Every relevant `{site, cluster}` pair, including locations of known previous attempts. These are observed identifiers, not names derived from the login hostname alone. |
| `queries[].result` | `available` if that query succeeded; `unavailable` if it failed. |
| `queries[].coverage` | `complete` only when the site procedure and observations cover the target's relevant attempts, including necessary accounting. Use `partial` for an empty queue listing without sufficient history, or for an incomplete search; use `unknown` when coverage cannot be determined. |
| `queries[].attempts` | The identified jobs and their current states. Each identity includes site, cluster, Job ID as a string, and a recorded submit timestamp with timezone. |
| `pending_keys` | Previous unknown submissions with searchable keys, each recorded as `{site, cluster, key}`. Carry them into this preflight even when the new query resolves them, placing that resolution in `resolved_submission_keys`. Use an empty array when there were no such calls to reconcile. |
| `resolved_submission_keys` | Keys positively matched on this query's site/cluster to a terminal attempt or definite non-acceptance. A token absent from search results is not positive resolution. |
| `unresolved_without_key` | `true` when a known previous call is unresolved and has no usable searchable key. Clear it only after positive site evidence identifies and resolves that particular call. |

If you cannot establish the relevant scopes, or a prior record cannot be read, report the missing evidence. Do not omit the affected cluster or unknown call to make the checker pass. Each key is scoped: an identical token on another cluster cannot resolve it.

## Complete example

In this example, a previous submit response was lost. The retained key is `retry-example-001` on `cluster-a`. Fresh queue and accounting observations positively identify the earlier job as terminal, and cover this Run's other relevant attempts. The site's instructions support treating that query coverage as complete.

The following is a **worked example**, using fictional site, cluster, path, Job ID, and historical timestamps. A real caller supplies its current observations in a file or on stdin. Do not execute a retry based on these example values. If using a file, keep it outside the Run's execution inputs and visible on the host running the check.

```json
{
  "target": "/project/runs/0d7",
  "scopes": [
    {"site": "example-site", "cluster": "cluster-a"}
  ],
  "not_before": "2026-09-05T02:00:00+00:00",
  "queries": [
    {
      "schema": 2,
      "target": "/project/runs/0d7",
      "site": "example-site",
      "cluster": "cluster-a",
      "observed_at": "2026-09-05T02:00:01+00:00",
      "result": "available",
      "coverage": "complete",
      "attempts": [
        {
          "job_identity": {
            "site": "example-site",
            "cluster": "cluster-a",
            "job_id": "1234",
            "submit_time": "2026-09-05T01:00:00+00:00"
          },
          "state": "terminal"
        }
      ],
      "resolved_submission_keys": ["retry-example-001"]
    }
  ],
  "pending_keys": [
    {
      "site": "example-site",
      "cluster": "cluster-a",
      "key": "retry-example-001"
    }
  ],
  "unresolved_without_key": false
}
```

For an ordinary retry without a previous lost-response key, use `pending_keys: []` and `resolved_submission_keys: []`, retaining the observed jobs and coverage. For more than one relevant cluster, add each pair to `scopes` and supply its own query object. For a failed query, use `result: "unavailable"` and `coverage: "unknown"`; do not invent terminal jobs to fill the array.

The exact machine contracts are [retry preflight v2](../../../schemas/v2/retry-preflight.schema.json) and [query evidence v2](../../../schemas/v2/query-evidence.schema.json). They describe this optional checker's input, not a generic Site-provider API.

## Run the checker and use its result

Follow [CLI setup](../../runhand/references/cli-setup.md). Run the check in the environment allowed by the site's instructions, with the actual `preflight.json` you prepared:

```bash
"${RH[@]}" retry check --evidence preflight.json --json
```

This reads the supplied evidence, contacts no scheduler, and returns the ordinary CLI envelope with `data.decision` and `data.reason`. The example's decision data with complete, positively resolved evidence is:

```json
{"decision": "allowed", "reason": "no_active_or_unresolved_attempt"}
```

Continue with the already authorized retry only for `data.decision: "allowed"`. Normal checking also exits 0 for `blocked` or `unknown`. In a shell script, use `--require-allowed` to make those decisions fail with exit 3 before the following dispatch step:

```bash
"${RH[@]}" retry check --evidence preflight.json --require-allowed --json
```

Stop dependent steps on that failure. The error is `retry_not_allowed`, with the decision and reason in `errors[].details`. Invalid JSON or an unreadable file exits 4. Use `--evidence -` to supply the same input on stdin without creating a file.

| Decision or reason | What to do |
|---|---|
| `blocked` / `active_attempt` | Report the existing active job. Do not edit its live execution files or submit another attempt. |
| `query_coverage_incomplete` | Obtain the missing site query or accounting evidence; if unavailable, report unknown. |
| `submission_key_unresolved` or `unkeyed_submission_unresolved` | Continue read-only reconciliation of that earlier call. Do not substitute “not found” for definite non-acceptance. |
| `stale_query`, `future_query`, or `future_preflight` | Capture a new preflight with consistent current timestamps. |
| `invalid_evidence`, `query_target_mismatch`, `query_scope_mismatch`, or `job_scope_mismatch` | Correct the data using the original observations, then run the checker again. Do not drop a job or scope to suppress an error. |

The checker cannot prevent another caller from submitting after your query. Refresh the observations if the preparation is delayed or new evidence appears, and report only the site's actual idempotency guarantee.
