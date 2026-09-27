# B06 — Make per-run fact assembly a testable unit (small architecture change)

| Field | Value |
|---|---|
| Starting commit | `b60e93ce5f14f1036888334f6aec6d6c207df02c` |
| Category | small architecture change (behaviour-preserving) |
| Allowed scope | `core/pipeline.py`, `core/runs.py` or one new `core/` module, `tests/`, `docs/agent/REPO_MAP.md` |
| Max unrelated diff | 0 lines outside scope; no renamed public names |
| Corpus expectation | gate must pass (exit 0) |
| Difficulty | medium–hard |
| Known correct solution | yes (any behaviour-equivalent extraction) |
| Acceptance | hidden behaviour-equivalence measurement identical; full suite + golden unchanged; a direct unit test of the new unit |

## Prompt
`core/pipeline.py:_run_pipeline` assembles each logical run's own facts
(letterhead, valuation date, policy term, printed totals and count, and the
conventions it borrows) inline, which makes that logic testable only through
whole PDFs. Extract it into a named unit that can be tested directly, with
exactly the same behaviour, and add direct unit tests for it. No behaviour
change of any kind. Follow AGENTS.md and end with the handoff template.

## Invariants
All; this task must change none.
