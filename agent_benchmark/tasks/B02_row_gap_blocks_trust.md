# B02 — A row-count gap must block trust (cross-module correctness)

| Field | Value |
|---|---|
| Starting commit | `b60e93ce5f14f1036888334f6aec6d6c207df02c` |
| Category | cross-module correctness |
| Allowed scope | `core/reconcile.py`, `core/review.py`, `core/telemetry.py`, `tools/corpus_gate/collect.py` (only if needed), `docs/agent/*.md`, `tests/` |
| Max unrelated diff | 0 lines outside scope |
| Corpus expectation | `review_status` may change on documents where R-19 fires; each change must be listed and explained |
| Difficulty | medium |
| Known correct solution | yes |
| Acceptance | hidden checks + full suite + golden + corpus explanation |

## Prompt
Product decision (already made): when the rows carrying claim numbers on a
document's pages outnumber the claims read, the document must not be
trusted without a person — every layer (app, workbook, JSON, telemetry,
corpus gate) must show NEEDS_REVIEW for it. R-19 detects this today but
never affects trust. Its severity must stay WARN (spec §6). Make the change
consistently across the layers that derive trust, update tests that encoded
the old behaviour (justify each), and state which corpus measurements you
expect to change. Follow AGENTS.md and end with the handoff template.

## Invariants
INVARIANTS.md §4 (one status policy; engine status stays ERROR-only), §9.
