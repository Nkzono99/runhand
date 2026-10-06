# Clean disposable work

Use the known RunHand command and exact managed roots. Read [CLI setup](../../runhand/references/cli-setup.md) only for missing setup. Follow the Site's route for large scans and confirm the same roots are visible on the execution host.

## Bulk cleanup

Choose `--kind scratch` for stages and tasks, `cache` for context cache, or `all` when the request covers both. Use the configured ages unless the user supplied an age; supported durations are nonnegative integers with `s`, `m`, `h`, `d`, or `w`.

For scratch with configured ages:

```bash
RH_GC_ARGS=(gc --kind scratch)
"${RH[@]}" "${RH_GC_ARGS[@]}" --json
```

Set the kind from the request. For an explicit threshold, append `--older-than "$RH_GC_AGE"` to the array before previewing.

Inspect `data.candidates`, `data.protected`, `data.cutoffs`, `data.estimated_bytes`, and warnings. An inspection-only request ends here. When cleanup is authorized and the preview matches its scope, apply the same arguments:

```bash
"${RH[@]}" "${RH_GC_ARGS[@]}" --apply --json
```

Changing roots, kind, configuration, or age requires a new preview. GC rechecks candidates under locks, so activity can change the actual deletions. Report `data.deleted` and retained items. A failed apply may be partial; inspect its receipt and the same scope again. Preserve the requested age and scope.

## Protected items

| Condition | Action |
|---|---|
| Fresh | Retain under the selected age policy. |
| Pinned | Retain unless the request authorizes removing that pin. An identified disposable scratch task can be unpinned with `scratch unpin TASK --json`; stage pins have no public unpin command. |
| Nonterminal stage | Use `stage inspect STAGE --json`. A ready stage may contain unpublished work; discard only when that exact work is identified as disposable. |
| Active/unknown/unresolved task | Reconcile associated Site work. Confirm all attempts ended before recording terminal evidence; a failed calculation may still be terminal. |
| Busy/`managed_root_busy` | Let the current operation finish, then preview the scope again; preserve lock files. |
| Unsafe/unowned roots or incompatible/unreadable metadata | Retain the item and report the exact path and condition. Resolve setup rather than manufacturing owner markers. |

For an existing task with new Site evidence, use [scratch state recording](../../runhand/references/scratch-work.md#record-observed-state), keeping its actual observation time and provider identity. Reuse the existing task; recording terminal state does not restart its retention age. Then preview the original scope again.

## Exact discard

Use the exact path of the identified disposable task or abandoned stage: the task root with `.runhand-task.json`, or the stage root with `stage.json` and `tree/`. The editable child and a published formal Run are different targets.

Recheck associated process/scheduler work through Site instructions. Retain known active work. If liveness stays unknown, proceed only when the user identified this exact object as disposable/abandoned; broad stale-data cleanup is insufficient. Resolve any pin under the user's authority before proceeding; pinned stages remain protected.

```bash
"${RH[@]}" gc --orphan "$RH_ORPHAN" --json
"${RH[@]}" gc --orphan "$RH_ORPHAN" --apply --json
```

Set `RH_ORPHAN` to that exact task/stage path. Use no bulk kind/age flags. Inspect `data.orphan`, estimated `data.bytes`, and boolean `data.deleted`. Preserve a refusal rather than using shell deletion as a fallback. Report actual removals and unresolved protection; RunHand provides no undo for applied deletion.
