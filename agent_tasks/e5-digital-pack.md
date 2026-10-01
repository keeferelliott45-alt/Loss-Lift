# E5 — independent digital-format qualification pack

Start from full Q1 SHA supplied by lead; reject an unresolved base.
Exclusive scope `tests/qualification/packs/digital_formats/**`.
Branch `agent/sol/digital-formats`; draft PR target lead integration branch.
Read repository instructions, E1 and `docs/qualification.md` first.
Export exact `build_cases(root: Path) -> list[QualificationCase]` interface.

Build 12 source-defined cases spanning labeled/mixed identifiers, furniture
controls, absent carrier, A4/Letter, landscape, wrapped headers, multiline rows,
signed amounts, recovery, and blank-versus-zero. Independently author truth
from intended printed construction, NEVER current output. All claims/fields,
pages/run anchors and printed facts must be explicit under the frozen schema.
Generate PDF binaries only in temporary directories; commit source/truth only.
Test schema validity, anchors and reproducible printed content/builders.

No production edits, shared registries, other folders, baselines or corpus-gate
changes. Report pipeline disagreements, never fix truth or add xfails to hide
them. No corpus gate needed for fixture construction. Required standard
validation and repository handoff; draft PR only, no merge/force push.
