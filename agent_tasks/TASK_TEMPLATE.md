# Task: <short imperative title>

Copy to `agent_tasks/<slug>.md`, fill in, and hand to one agent. One task,
one worktree, one branch (`scripts/agent_worktree.sh create <slug> --agent <name>`).

## Objective
<what must be true when done, in one or two sentences>

## Why it matters
<the risk or user outcome; cite the audit / CURRENT_STATE item>

## Allowed scope
- <modules/behaviour the agent may change>

## Likely files
- `<path>` — <why>

## Invariants that must hold
<list INVARIANTS.md sections; name any the task is allowed to strengthen>

## Required tests
- New/changed tests: <what they must prove; red before the fix, green after>
- Targeted: `python -m pytest -q <paths>`
- Standard: `scripts/agent_validate.sh --tests "<paths>"`

## Corpus expectations
<"no change expected" | "changes expected in <kind of document>, each to be
explained" | "not applicable: tooling/docs only">

## Definition of done
- [ ] Objective met; tests prove it (fail on the base commit)
- [ ] `scripts/agent_validate.sh` passes
- [ ] Corpus gate run or explicitly not required (above)
- [ ] `docs/agent/CURRENT_STATE.md` updated if a milestone/gap changed
- [ ] Handoff written (`docs/agent/HANDOFF_TEMPLATE.md`)

## Prohibited scope expansion
- No changes outside Allowed scope; no renamed rule ids/categories/fields
- No new product features; no refactors "while here"
- Adjacent problems go under DEFERRED ITEMS in the handoff

## Handoff
Use `docs/agent/HANDOFF_TEMPLATE.md` exactly.
