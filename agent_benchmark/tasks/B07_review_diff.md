# B07 — Adversarial review of an existing diff (review)

| Field | Value |
|---|---|
| Starting commit | branch `bench/b07-review` (prepared by the evaluator on `b60e93c`) |
| Category | adversarial review — read-only |
| Allowed scope | none (no writes, no commits) |
| Max unrelated diff | n/a |
| Corpus expectation | n/a |
| Difficulty | medium–hard |
| Known correct solution | yes (hidden answer key) |
| Acceptance | defects caught with location and failure mode; false positives counted |

## Prompt
Review branch `bench/b07-review` against `b60e93ce5f14f1036888334f6aec6d6c207df02c`.
The author says it is a behaviour-neutral tidy-up and the full suite passes.
Using AGENTS.md and docs/agent/INVARIANTS.md, find every change that alters
behaviour or weakens an invariant. For each finding give file:line, the
failure mode, severity, and a reproduction or failing test if you can. Also
say which changes you consider safe. Do not modify anything. Verdict:
APPROVE / CHANGES REQUIRED.
