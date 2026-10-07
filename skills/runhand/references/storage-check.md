# Check publication storage

The default `--publish-mode atomic` uses Linux `renameat2(RENAME_NOREPLACE)` on the **destination filesystem**. `promote` probes that parent before copying the Run and returns `publication_unavailable` with the original errno if support is missing. Internal stages and `scratch prepare` do not require that operation. A writable destination, successful `doctor`, or `promote --dry-run` does not establish publication support.

Use an explicit check before substantial preparation when destination support is unknown, or after `publication_unavailable`, `atomic_noreplace_unavailable`, or `atomic_publish_failed`. The automatic promotion preflight makes a separate check unnecessary on routine runs. `Invalid argument` can indicate an unsupported mount; it does not necessarily mean the copy selection is malformed.

Using the existing `RH` command from [CLI setup](cli-setup.md), set `RH_TARGET_PARENT` to the actual existing parent of the intended formal Run. Probe that mount on the Site-permitted host:

```bash
"${RH[@]}" storage check "$RH_TARGET_PARENT" --json
```

The probe creates and removes a tiny unique directory, testing publication and refusal to replace an existing empty directory. It creates no owner markers or Runs. Exit 0 and `ok=true` report results in `data.checks`; failure exits 5 with `errors[].code=storage_check_failed` and observations in `errors[].details.checks`. Multiple destination parents can be checked in one call.

Choose the next action from the result:

- **The atomic mode is incompatible:** changing scratch alone cannot fix it. Use another destination within the user's scope, or the explicit symlink mode below when the Site and Run consumers support directory links. Report the chosen mode and its storage requirements.
- **The probe passes but the operation failed:** use the original CLI error and current Site evidence to check permissions, quota/free space, and mount changes. Do not reinterpret every publication error as unsupported storage or loop on the same failed call.

Do not replace formal publication with ordinary `mv`, `cp`, or “check then rename.” No automatic fallback changes the target type. Keep any existing stage and report which later actions were not reached.

## Shared storage without no-replace rename

Lustre mounts that reject `RENAME_NOREPLACE` can use an explicitly selected `--publish-mode symlink`. Check the actual existing destination parent on the Site-permitted host:

```bash
"${RH[@]}" storage check "$RH_TARGET_PARENT" --publish-mode symlink --json
"${RH[@]}" promote "$RH_STAGE" "$RH_DESTINATION" --publish-mode symlink --json
```

This copies into a unique `.runhand-run-*/tree` under that parent. Only after the copy is complete does exclusive `symlink()` create the formal name as a relative directory link. An existing file, directory, or symlink, including a dangling link or a path created during the copy, causes a collision. Both modes expose a complete tree and refuse replacement; symlink mode retains the physical tree under its sibling name instead of renaming it to the formal name. It does not depend on `renameat2`.

The JSON receipt and stage promotion history include `publish_mode` and `backing_path`. The sibling is **durable Run data**, not scratch: it must be outside managed scratch and stages, and RunHand GC does not collect it. Run inputs, executables, outputs, and restart files remain usable after the original source, stage, plugin, or state cache is removed. The formal link and its backing directory must both be retained for as long as the Run is needed. Moving or archiving the destination parent with all its hidden children preserves the relative link; copying only the formal link does not preserve the Run. Backup tools must include the hidden backing directory or deliberately dereference the formal link. Site scripts that forbid symlink working directories require atomic mode on compatible storage.

## Failure and recovery

Each durable sibling has a `publication.json` outside its Run tree with the target, stage, backing path, and state:

- `incomplete`: copying has not finished; never execute this tree as a formal Run.
- `ready`: copying finished; the link may be absent, or already present if the process stopped before recording publication.
- `published`: the formal link was created. This describes publication, not simulation completion.

Caught failures remove only this invocation's unreferenced sibling when cleanup succeeds. The original stage remains available for retry. If cleanup fails or the formal link already references the complete tree, error details identify `retained_backing`, `publication_record`, and `target`. A killed process can leave a sibling without an updated record, or without a record if allocation was interrupted.

Inspect the actual target and relative link before recovering. A complete link pointing to the recorded backing is already published even if the record says `ready` or the stage still says `ready`; retain it and validate the live Run before any authorized execution. Do not retry over that target. If the target is absent, use the original ready stage to promote again to an unused name. Treat unreferenced partial siblings as abandoned copies, and remove an exact sibling only after the Site confirms no publisher or job is using it; do not bulk-delete `.runhand-run-*`. An unreadable record or uncertain link remains unresolved.

`publication_state_update_failed` and `stage_state_update_failed` are warnings after successful publication. Keep the Run; do not roll it back or submit it again because a receipt update failed. These modes guarantee exclusive, complete namespace publication during ordinary operation; they do not add power-loss durability beyond the filesystem's guarantees.
