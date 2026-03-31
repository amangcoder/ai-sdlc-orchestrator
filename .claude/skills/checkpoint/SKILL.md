---
name: checkpoint
description: Create a WIP git commit to save current progress before risky changes
---

Quickly save current work-in-progress as a git commit with a conventional name. Useful before attempting risky refactors, experimental changes, or when you want a restore point.

## When to Activate

- User says "checkpoint", "save progress", "wip commit", "save my work"
- Before starting a risky refactor or experimental change
- When switching context and wanting to preserve current state

## Steps

1. **Check status** -- Run `git status` to see what has changed. If there are no changes (working tree clean), report "Nothing to checkpoint" and stop.
2. **Summarize changes** -- Briefly describe what the current changes contain based on `git diff --stat`.
3. **Stage all changes** -- Run `git add -A` to stage everything (including untracked files).
4. **Create WIP commit** -- Commit with the message format:
   ```
   wip: <brief description of current state>
   ```
   The description should be a short (under 50 chars) summary of what was in progress.
   Examples:
   - `wip: add speed classifier tests`
   - `wip: refactor engine phase loop`
   - `wip: partial migration to async agents`
5. **Confirm** -- Print the commit hash and message. Remind the user they can undo with `git reset HEAD~1` to get back to unstaged state.

## Options

- `--message <msg>` -- Override the auto-generated description
- `--staged-only` -- Only commit what is already staged, do not `git add -A`

## Examples

```
/checkpoint
/checkpoint --message "before switching to async engine"
/checkpoint --staged-only
```
