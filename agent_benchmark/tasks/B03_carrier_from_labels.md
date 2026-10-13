# B03 — Column labels read as a carrier name (extraction edge case)

| Field | Value |
|---|---|
| Starting commit | `b60e93ce5f14f1036888334f6aec6d6c207df02c` |
| Category | extraction edge case |
| Allowed scope | `core/profiles.py`, `core/extract_digital.py` (letterhead only), `tests/` |
| Max unrelated diff | 0 lines outside scope |
| Corpus expectation | `metadata.carrier` / `profile_fingerprint` may change only where a header line was taken as a carrier; explain each |
| Difficulty | medium |
| Known correct solution | partial |
| Acceptance | hidden checks + full suite + golden (carrier accuracy must not drop) |

## Prompt
When a loss run's letterhead does not print a carrier name, LossLift can
report the claims table's column-label line (e.g. "Claim # Date of Loss …")
as the carrier. That wrong carrier then feeds logical-run facts, carrier
profiles and account identity. Make carrier detection refuse a line that is
the table's header, without losing any carrier LossLift reads correctly
today. Add tests. Follow AGENTS.md and end with the handoff template.

## Invariants
INVARIANTS.md §2 (run facts), §5 (identity); spec §5 stage 3 (profiles are
structure only).
