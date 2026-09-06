---
name: runhand-maintenance
description: Inspect RunHand configuration and clean disposable stages, scratch tasks, and context cache. Use for GC previews, cleanup, and exact orphan removal. Do not use to delete formal Runs, scheduler jobs, or ordinary project data.
---

# RunHand maintenance

Use this procedure for disposable stages, scratch tasks, and context cache. Formal Runs, ordinary project artifacts, and existing submission-history files are outside this cleanup scope. Respond in the user's language.

Follow the user's requested effect: a doctor check or preview ends with a report; a cleanup request includes applying cleanup within that scope. Do not ask again merely because the next command includes `--apply`. Ask only when a destructive target or the authority to discard protected work remains unclear.

## 1. Identify the installation and managed roots

Reuse the known `RH` command and roots, and proceed to the preview. Use [CLI setup](../runhand/references/cli-setup.md) only for unresolved setup. For a doctor request or uncertainty about the configured roots, inspect once or reuse a current receipt:

```bash
RH_DOCTOR_JSON=$("${RH[@]}" doctor --json)
printf '%s\n' "$RH_DOCTOR_JSON"
```

Check `ok`, then read these fields from that single JSON object:

| Field | What to do with it |
|---|---|
| `data.workspace` | Confirm the research workspace the request refers to. |
| `data.scratch.path`, `data.state.path` | Use these exact managed roots; do not infer roots from directory names. |
| `data.scratch.exists`, `data.state.exists` | An absent root has nothing to clean; do not initialize it for cleanup. |
| `data.scratch.owned`, `data.state.owned` | Ownership diagnostic cues. `false` or `null` does not authorize adoption or deletion; GC performs its own stricter ownership checks. |
| `data.scratch.writable`, `data.state.writable` | Whether writes appear possible from this host. This is not a Site execution-route decision. |
| `data.config.scratch_ttl_days` | Default age threshold for both scratch and cache. |
| `warnings` | Configuration, ownership, and access problems to retain or investigate. |

If doctor was needed, read its intended exact paths into `RH_WORKSPACE`, `RH_SCRATCH_ROOT`, and `RH_STATE_ROOT`, then fix them for subsequent commands:

```bash
RH=("${RH_BIN[@]}" --workspace "$RH_WORKSPACE" --scratch-root "$RH_SCRATCH_ROOT" --state-root "$RH_STATE_ROOT")
```

If configuration is invalid, correct the identified configuration source or explicit path arguments, then rerun doctor. Never manufacture an owner marker, adopt an unowned directory, or switch to another root just to make GC succeed.

For a Site decision, open the relevant installed Site skill and follow its instructions using the tools available in the session. “Site capability” names that source of instructions and evidence; it is not a `Site(...)` function. GC estimates directory sizes recursively, so large scratch trees can require the Site's compute or I/O route even for a preview. When routing a command to another host, carry the same explicit roots and confirm they are visible there. Use the Site skill's execution procedure; do not invent a replacement route.

## 2. Choose one cleanup scope and preview it

Choose the scope from the request:

| `--kind` | Included data |
|---|---|
| `scratch` | Both working stages and scratch tasks. There is no separate bulk `stage` kind. |
| `cache` | Disposable context-cache records. |
| `all` | Scratch and cache; use when the request covers both. |

For “clean old RunHand state” with no narrower category, `all` with configured ages is appropriate. For an explicitly named task or abandoned stage, use the exact-path procedure in section 5 instead of a bulk command.

Use the user's age threshold if supplied; otherwise omit `--older-than` to use `scratch_ttl_days`. Durations are nonnegative integers followed by `s`, `m`, `h`, `d`, or `w`, such as `30d`.

Set these two values from that decision; this example selects scratch using the configured age:

```bash
RH_GC_KIND=scratch
RH_GC_AGE=''
RH_GC_ARGS=(gc --kind "$RH_GC_KIND")
if [[ -n "$RH_GC_AGE" ]]; then
  RH_GC_ARGS+=(--older-than "$RH_GC_AGE")
fi
"${RH[@]}" "${RH_GC_ARGS[@]}" --json
```

Interpret the result before applying it:

- `data.candidates` lists eligible items, each with an exact `path`, `kind`, and estimated `bytes`.
- `data.protected` lists retained items and a `reason`. A protected item is not a failed deletion to retry with shell commands.
- `data.cutoffs` gives the age cutoff used for each category. Confirm that it matches the user's intent and effective configuration.
- `data.estimated_bytes` estimates the selected data size. It does not measure physical filesystem space that will be reclaimed.
- `data.deleted` is empty during a preview. Review top-level `warnings` for skipped roots and unavailable state.

If the user requested only inspection, report this result and stop. If deletion is already authorized and the preview matches that scope, continue directly to section 3.

## 3. Apply the same scope

Use the same `RH` and `RH_GC_ARGS` arrays, adding only `--apply`:

```bash
"${RH[@]}" "${RH_GC_ARGS[@]}" --apply --json
```

GC scans again and checks each object's current state under its mutation lock before deletion. The preview is not a frozen list: concurrent activity can change the candidates. If the workspace, root arguments, configuration, selected kind, or age threshold changes, preview the revised scope first. Never reduce an age threshold or expand a kind merely to reclaim more space.

Report actual removals from `data.deleted`. Items that became pinned, active, unresolved, fresh, or busy can appear in `data.protected` instead. An empty deletion list can be a successful cleanup with everything protected.

If an apply command returns `ok=false`, do not assume earlier deletions were rolled back. Read `errors`, inspect or preview the same scope again, and report what can be established. Do not restore or delete other research data to compensate.

## 4. Handle protected items using evidence

Use the reason to choose the next action:

| Reason or condition | Next action |
|---|---|
| `fresh` | Keep it under the selected age policy. |
| `pinned` | Preserve the pin unless the user's request covers removing it. An explicit request to delete this exact pinned task can provide that authority; a general stale-state cleanup does not. |
| `nonterminal` on a stage | Set `RH_STAGE` to the protected stage path and run `"${RH[@]}" stage inspect "$RH_STAGE" --json`. A `ready` stage may contain unpublished inputs; use section 5 only if that exact stage is to be discarded. |
| `liveness_unknown`, `nonterminal`, or `attempt_unresolved` on a task | Retain it. When available Site receipts/logs let you resolve completion as part of this cleanup, use the Site's live query; batch related tasks. Report unresolved items instead of conducting an open-ended search. |
| `busy`, or an error with code `managed_root_busy` | Another operation holds this object's lock. Let that operation finish, then preview the same scope again if cleanup is still requested. Do not remove lock files or run a retry loop. Other independent items can still be cleaned. |
| Incompatible metadata, unsafe containers, unowned roots, or unreadable state | Leave the item in place and report the exact path and warning/error. Resolve the underlying condition before trying that item again. |

When a task's completion can be confirmed, use the evidence-recording steps in [scratch work](../runhand/references/scratch-work.md) for this existing task. Do not allocate a new task for maintenance. Set `RH_TASK` to the exact protected task path, retain the observation time in `RH_OBSERVED_AT`, and identify the actual Site skill/tool in `RH_PROVIDER_LABEL`. After Site evidence confirms that all associated work has ended, record it directly:

```bash
"${RH[@]}" scratch record "$RH_TASK" \
    --liveness terminal --attempt-state terminal \
    --observed-at "$RH_OBSERVED_AT" \
    --capability site --identity "$RH_PROVIDER_LABEL" --json
```

Scheduler-associated tasks require Site evidence. Identify the attempt using available site/cluster, Job ID, submit time, and local evidence before checking its state. An empty queue listing alone does not prove completion: use the Site's accounting or other applicable evidence. Scheduler termination and simulator success are separate facts; a failed job can be terminal without being scientifically successful. Use `--attempt-state none` only for a task with no scheduler attempt, following the source rules in the scratch guide. Existing structured evidence can instead be supplied with `--evidence FILE|-`; do not mix JSON evidence with scalar flags.

Recording terminal evidence preserves the task's usage age; it does not restart retention just because maintenance observed it. Preview the original scope again after recording. Actual active use updates usage time. Do not reduce the age threshold merely to delete more; an explicit request to discard an exact task uses section 5.

If removing a task's pin is authorized, use `"${RH[@]}" scratch unpin "$RH_TASK" --json`, then preview again. Keep pins unrelated to this cleanup request. There is no public stage-unpin command; report a pinned stage as protected rather than editing its metadata.

## 5. Delete one explicitly disposable task or abandoned stage

Use this path only when the user has identified the exact object to discard. The earlier request can supply that authority; do not repeat the question after it is clear. If an unknown task was merely encountered during a broad cleanup, first recheck through the Site instructions, then ask the user to identify it as an orphan only if its status still cannot be resolved.

Copy the exact path returned by GC or stage inspection into `RH_ORPHAN`. Distinguish the two supported objects:

- A scratch task is the task directory under the configured scratch root's `tasks/KIND/` container. It has `.runhand-task.json`. Save newly established completion evidence with `scratch record` as described above.
- A stage is the stage directory under `stages/`, containing `stage.json` and `tree/`. Use the stage directory itself, not its `tree/` child or a promoted formal Run. `scratch record` does not accept stages. Inspect its state and promotions, and establish that its unpublished work is being discarded and no agent or payload is still using it.

For either object, follow the Site instructions to recheck any process or scheduler work that may still refer to it. Preserve known active work. If liveness remains unknown, the user's exact orphan designation is required; a general “clean old scratch” request is insufficient. Do not claim that the CLI performs this Site check: `data.requires_site_recheck` is a reminder to the caller.

Preview exactly that path, then apply when the check and existing user intent support deletion:

```bash
"${RH[@]}" gc --orphan "$RH_ORPHAN" --json
"${RH[@]}" gc --orphan "$RH_ORPHAN" --apply --json
```

These are exact-path operations. Do not append the bulk kind or age arguments: the orphan path uses its own checks and is not an age-filtered bulk cleanup. The result uses `data.orphan`, estimated `data.bytes`, and boolean `data.deleted`; it does not use the bulk `deleted` list. Pinned or known-active objects are refused. If the CLI refuses, retain the object and resolve the stated reason; never use shell deletion as a fallback.

## 6. Return a maintenance receipt

State the workspace and inspected roots, the selected scope and age policy, whether deletion was applied, and the actual deleted paths or count. Report estimated bytes only as an estimate, with protected/skipped paths and their material reasons. Do not call estimated bytes physically reclaimed space.

For applied deletions, say that RunHand removed disposable data and provides no undo. Do not assume site backups exist or that a failed operation left everything untouched. Include unresolved liveness, busy objects, or configuration problems and the next concrete action, if any.
