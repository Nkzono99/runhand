# Create a Run

Use the known command and roots, or [CLI setup](cli-setup.md) when unresolved. Temporary checks use [scratch work](scratch-work.md) instead of stage/publication.

## Select the inputs

Resolve the named source and requested destination to actual paths. The destination parent must exist and the new Run must be outside the source and managed scratch. Use Simulator instructions for required inputs, auxiliary data, executable/script dependencies, restart files, and relative-symlink targets. Retain outputs needed as initial conditions.

The example assumes these three files are required; adapt the selection to the case:

```bash
RH_COPY_ARGS=(
    --source "$RH_SOURCE"
    --include 'input.toml' --include 'model.dat' --include 'run.sh'
)
```

Quote relative POSIX patterns; `*` matches within a component and `**` matches whole components recursively. Repeat include/exclude flags for known dependencies, rather than using an unrestricted recursive copy. External links, unresolved excluded targets, and special files are rejected.

An existing [copy plan](../../../schemas/v1/copy-plan.schema.json) can replace the inline selection with `--source "$RH_SOURCE" --plan "$RH_PLAN_FILE"`; these forms are exclusive. Plan annotations are not validation or execution authority.

## Prepare and edit

Route substantial copies through the Site's permitted host:

```bash
RH_STAGE=$("${RH[@]}" stage create "${RH_COPY_ARGS[@]}" --print-path stage)
RH_TREE="$RH_STAGE/tree"
```

After successful creation, edit the Simulator-identified fields and units under `RH_TREE`, then inspect the requested diff against the source. Creation-only work uses static checks that resolve meaningful uncertainty. Execution validation belongs at the final live target and reuses applicable checks.

Use `--json` for selection/summary details or `--dry-run --json` for a useful preview. `stage inspect "$RH_STAGE" --json` resolves questions about an existing stage.

## Publish and hand off

```bash
RH_WORKDIR=$("${RH[@]}" promote "$RH_STAGE" "$RH_DESTINATION" --print-path target)
```

Promotion checks destination support before copying and never replaces an existing destination. The default uses atomic directory rename. For shared storage without `RENAME_NOREPLACE`, [storage check](storage-check.md) describes explicit `--publish-mode symlink`, its durable backing tree, and recovery rules. Pass the same mode to `storage check` and `promote`; dry-run reports the mode without probing storage.

Return the target, changes, publication mode, any backing path, and performed checks for creation alone. For requested execution, pass the path, command, resources, observation request, and current evidence to [runhand-submit](../../runhand-submit/SKILL.md) or the Site's synchronous launcher.

## Handle a failed step

| Result | Action |
|---|---|
| `scratch_overlaps_source` | Bind an external scratch root and prepare again. |
| `source_changed` | Establish stable inputs or a suitable restart snapshot. |
| `target_exists` | Reuse only if it already satisfies the request; otherwise resolve the destination. |
| `managed_root_busy` | Inspect after the current operation finishes before retrying. |
| `publication_unavailable` / `atomic_noreplace_unavailable` / `atomic_publish_failed` / `symlink_publish_failed` | Use [storage check](storage-check.md) for the selected mode at the formal destination. |
| `publication_in_scratch` | Choose durable storage outside stages and managed scratch for symlink publication. |
| `retained_backing` in error details, or an interrupted publication | Follow [publication recovery](storage-check.md#failure-and-recovery); inspect the formal link before retrying. |
| Published target with `publication_state_update_failed` | Keep the target and backing; the recovery record could not be updated. |
| Published target with `stage_state_update_failed` | Keep the target and report the warning; publication already succeeded. |

Report what remains after failure. Retain an already published Run after a later validation/submission failure.
