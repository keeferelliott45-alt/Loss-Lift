# Handoff template

Use this when pausing, handing a task to another agent, or resuming. Pair it with
the task definition (`agent_tasks/TASK_TEMPLATE.md`). Keep it factual; do not
claim verification you did not run.

## Handoff

- **Task:** <title / link to task brief>
- **Branch:** `kilo/<short-topic>`
- **Exact head SHA:** <full 40-char commit>
- **Base:** `origin/main` at <full 40-char SHA>
- **Worktree:** <path> (local, git-ignored)
- **State:** in progress | blocked | ready for review | handed off

## What changed

<Files touched and why, one line each. Reference modules/rules, not documents.>

## What was verified

| Check | Command | Result |
|---|---|---|
| Focused test | `<cmd>` | <pass/fail + exit code> |
| Full suite | `python -m pytest` | <pass/fail + exit code> |
| Ratchet | `python -m tests.golden.baseline` | <exit code> |
| Accuracy | `python -m tests.golden.report` | <threshold + nulls-as-zeros> |
| Corpus gate | `<cmd or workflow inputs>` | <exit code / verdict> |

## Not verified

<Anything assumed, skipped, or unproven. Say so plainly. Note if a required
tool was unavailable.>

## Open questions / risks

- <question or risk, with the evidence you have>

## Next step

<The single concrete next action for the receiver, and which command or slash
command to use.>

## Do not

- Do not merge or mark ready without the owner's explicit authorization.
- Do not edit golden expected outputs, baselines, tolerances or gates to pass.
- Do not touch unrelated PRs.
