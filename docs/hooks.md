# Situational checks and hooks

RunHand's [situational-check](../skills/situational-check/SKILL.md) reconnects the next action to the user's outcome when feedback, stalled progress, cost, or restored context warrants a change. It applies to coding and research without requiring simulation operations. The existing RunHand skills also link to it through their shared work policy.

## Enable

Install or refresh the plugin, then start a new Codex session. In the Codex CLI, open `/hooks`, inspect RunHand's `SessionStart` and `UserPromptSubmit` command definitions, and trust them. Installation alone does not trust plugin hooks; edits to a hook definition require renewed review. Individual hooks can also be disabled there. These are Codex's [hook trust rules](https://learn.chatgpt.com/docs/hooks).

The plugin ships its [hook configuration](../hooks/hooks.json) and [Python helper](../hooks/situational_context.py) through the [plugin manifest](../.codex-plugin/plugin.json). The helper uses Python 3 from `PATH` and resolves the skill from its installed location. It needs no local `AGENTS.md`, transcript parser, persistent state, or configuration edits. Codex provides `PLUGIN_ROOT` for [plugin hook commands](https://developers.openai.com/plugins/build/plugins); the helper also supports the compatibility alias `CLAUDE_PLUGIN_ROOT`.

## Events and decisions

| Event | Context supplied |
|---|---|
| `SessionStart`: `startup`, `clear` | Discover the skill and keep the requested outcome ahead of a self-chosen method. |
| `SessionStart`: `resume`, `compact` | Also recover the goal and accepted corrections, carrying pending work, authorization, budgets, and observation deadlines forward. |
| `UserPromptSubmit` | Incorporate the latest message and reconsider the next action when it reveals a change or friction. |

Each handler returns a small `additionalContext` message. The model interprets whether a check is useful; the helper only selects by event and nonempty prompt. It does not classify emotions or parse conversation history. Smooth progress continues directly, and the skill is read only when needed, reusing it once loaded.

These handlers add context and return success. They do not make permission decisions, block prompts, call tools, poll jobs, or force continued turns. There are no per-tool or `Stop` handlers. Repeated failures within a turn are addressed by the skill's instructions and the shared work policy, rather than a hook counter. Hooks run synchronously with a five-second timeout; `additionalContextLimit` is 400 approximate tokens per handler.

If hooks are disabled, unsupported, or untrusted, the skill remains explicitly available as `$situational-check` and through RunHand's work policy. A timeout or runtime failure is handled by Codex; malformed or unsupported input produces no helper output and exits successfully.

## Verification

Run `PYTHONPATH=src python -m unittest discover -s tests -v` using the Site's compute-node launcher on HPC. The hook tests execute both configured commands from a different working directory with plugin paths containing spaces and shell metacharacters, and check context-only outputs, event selection, malformed input, and absence of transcript/state access.

[Behavior cases](../evals/situational-check.json) exercise changes to the next action: accepting corrections, changing an unproductive method, reducing user effort, using waiting time, retaining deadlines, and continuing smooth work. For a comparative evaluation, run the same cases with and without the skill/hooks on the same model and available tools. Judge actions against the criteria in each case; do not infer improved EQ from the hook tests alone.
