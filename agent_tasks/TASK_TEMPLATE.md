# Task template

Copy this into a task brief (or `agent_tasks/`) before starting work. Replace
every placeholder. Keep it short and falsifiable.

## Task

- **Title:** <short, specific>
- **Owner:** <agent/session>
- **Base:** current `origin/main` at <full 40-char SHA> (record it when work
  starts)
- **Branch:** `kilo/<short-topic>`
- **Isolation:** must not touch unrelated open PRs (name any, e.g. PR #9)

## Goal

<One paragraph: the observable outcome. Not "improve X" — what is true
afterwards that is not true now.>

## Why now

<Evidence: corpus frequency/severity, a failure class from
`benchmark/failures.csv`, a false-clean, or a named bug. No speculative edge
cases.>

## Invariants at risk

<List the applicable `docs/agent/INVARIANTS.md` numbers and how the change keeps
them. State the failure the change must not introduce — especially false-clean,
silent loss/merge/reassignment, or null-as-zero.>

## Scope

- In scope: <files/modules/rules>
- Out of scope: <explicitly excluded>
- Do not: edit golden expected outputs, accuracy baselines, tolerances or gates
  to pass; change production extraction unless the task is an extraction task.

## Plan

1. <step>
2. <step>

## Verification

- Focused regression test: `<command / test name>`
- Full suite: `python -m pytest`
- Ratchet: `python -m tests.golden.baseline` (non-zero = regression)
- Accuracy: `python -m tests.golden.report`
- Corpus gate (if extraction/reconciliation changed): `<local command or cloud
  baseline_sha/candidate_sha/pr_number>`
- Adversarial review: `/adversarial-review`

## Definition of done

- <criteria>
- Draft PR opened targeting `main` (not marked ready, not merged).
- Report: branch, exact commit SHA, files changed, validations with exit codes,
  and any unresolved concern.
