# Check storage after a publication failure

Formal Run publication uses Linux `renameat2(RENAME_NOREPLACE)` on the **destination filesystem**. Internal stages and `scratch prepare` are private mutable work areas and do not require that operation. A writable destination, successful `doctor`, or `promote --dry-run` does not establish publication support.

Use this check when formal destination support is unknown before a substantial promotion, or `promote` returns `atomic_noreplace_unavailable` or `atomic_publish_failed`. Do not probe on every routine run. `Invalid argument` can indicate an unsupported mount; it does not necessarily mean the copy selection is malformed.

Using the existing `RH` command from [CLI setup](cli-setup.md), set `RH_TARGET_PARENT` to the actual existing parent of the intended formal Run. Probe that mount on the Site-permitted host:

```bash
"${RH[@]}" storage check "$RH_TARGET_PARENT" --json
```

The probe creates and removes a tiny unique directory, testing publication and refusal to replace an existing empty directory. It creates no owner markers or Runs. Exit 0 and `ok=true` report results in `data.checks`; failure exits 5 with `errors[].code=storage_check_failed` and observations in `errors[].details.checks`. Multiple destination parents can be checked in one call.

Choose the next action from the result:

- **The requested formal destination is incompatible:** changing scratch alone cannot fix publication there. Report that the requested Run cannot be created with the current publication contract. Use another destination only when the user's scope permits it; otherwise leave the intended target uncreated and identify the needed storage support.
- **The probe passes but the operation failed:** use the original CLI error and current Site evidence to check permissions, quota/free space, and mount changes. Do not reinterpret every publication error as unsupported storage or loop on the same failed call.

Do not replace formal publication with ordinary `mv`, `cp`, or “check then rename.” Keep any existing stage and report which later actions were not reached. Temporary work can still use `scratch prepare` when the Site permits it; that does not satisfy a request to publish a formal Run at an unsupported destination.
