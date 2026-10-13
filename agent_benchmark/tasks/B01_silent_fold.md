# B01 — A claim row disappears into another claim (localized bug fix)

| Field | Value |
|---|---|
| Starting commit | `b60e93ce5f14f1036888334f6aec6d6c207df02c` |
| Category | localized bug fix |
| Allowed scope | `core/pipeline.py`, `tests/` |
| Max unrelated diff | 0 lines outside scope; ≤ 10 changed lines in scope that the fix does not need |
| Corpus expectation | no change expected; any change must be explained per document |
| Difficulty | medium |
| Known correct solution | yes (more than one acceptable) |
| Acceptance | hidden checks + full suite + golden ratchet |

## Prompt
On a single loss run whose claim numbers mostly share one format, one row
has a differently shaped claim number, a loss date like `03/04/2022`, a blank
status and no amounts. That claim is not in the output: its text ends up in
the previous claim's loss description, nothing records it, and the document
reads as reconciled. Reproduce this with a synthetic document, fix it so such
a row is never folded into another claim and the document cannot read as
reconciled while the row is unaccounted for, and add tests. Do not change
rule categories or severities. Follow AGENTS.md and end with the handoff
template.

## Invariants
INVARIANTS.md §1 (claim accountability), §4 (status). Rows that are genuine
continuation text must still fold as before.
