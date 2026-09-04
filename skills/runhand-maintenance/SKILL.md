---
name: runhand-maintenance
description: Inspect and clean RunHand-owned local state. Use for RunHand doctor checks, GC previews, stale stage/scratch/cache/history cleanup, or exact orphan cleanup. Do not use to delete formal simulation Runs, scheduler jobs, or ordinary project data.
---

# RunHand maintenance

Use this skill only for RunHand's disposable state. Respond in the user's language.

RunHand-owned data includes stages, scratch tasks, context cache, and optional local history under configured RunHand roots. Formal Runs and ordinary project artifacts are outside this skill's deletion scope.

## Inspect first

Use `runhand doctor --json` to inspect the installed version, configuration, workspace, managed roots, and warnings. Resolve configuration errors before cleanup; never guess a root or adopt an unowned directory as RunHand state.

For a cleanup request, preview the exact scope:

```bash
runhand gc --kind KIND --older-than DURATION --json
```

Choose `KIND` from `scratch`, `cache`, `history`, or `all`. Report candidates, estimated size when available, protected items, and warnings. Preview is inspection, not permission to delete.

## Apply cleanup

Run the matching command with `--apply` only when the user requested deletion:

```bash
runhand gc --kind KIND --older-than DURATION --apply --json
```

Bulk GC must preserve ready stages, pinned tasks, active or nonterminal work, unresolved submission history, liveness-unknown tasks, foreign metadata, and unowned roots. Never broaden the requested kind or age threshold merely to reclaim more space.

For an exact liveness-unknown task:

1. Ask the Site capability to recheck live scheduler evidence.
2. If the task remains unknown, require the user to identify that exact task as orphan.
3. Delete only that task:

   ```bash
   runhand gc --orphan TASK --apply --json
   ```

Never use shell deletion as a fallback for a GC refusal.

## Return a maintenance receipt

Report:

- inspected RunHand roots;
- previewed or applied action;
- deleted item count and estimated/reclaimed size when available;
- protected or skipped items and reasons;
- unresolved liveness or configuration errors.

If anything material was deleted, state that it was RunHand-owned disposable data and whether recovery is possible.

Prefer an installed `runhand` executable. In a plugin/source checkout, resolve the plugin root two directories above this file and fall back to `PYTHONPATH=<plugin-root>/src python3.11 -m runhand`. Use `runhand gc --help`, `runhand doctor --help`, and `../../SPEC.md` section 7.4 when exact contract details are needed.
