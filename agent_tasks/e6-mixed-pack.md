# E6 — independent mixed-PDF/accounting qualification pack

Start from full Q1 SHA supplied by lead; reject an unresolved base.
Exclusive scope `tests/qualification/packs/mixed_accounting/**`.
Branch `agent/sol/mixed-accounting`; draft PR target lead integration branch.
Read repository instructions, E1 and `docs/qualification.md` first.
Export exact `build_cases(root: Path) -> list[QualificationCase]` interface.

Build 12 cases for bounded reader transitions, distinct report series,
uncertain boundaries, claim-free/foreign pages, missing replay, repeated
identifiers and document/run/section printed-count differences. Truth comes
from source construction. Synthetic replay must transcribe generated pages
including absent printed counts and numbering; expected claim count must not
be invented as printed fact. Commit builders/truth/replay source only, generate
binaries and recordings in temporary paths. Preserve real field/missing-claim
metrics in review-required cases; NEEDS_REVIEW is not perfect extraction.

Test source/truth/replay consistency and deterministic builds. No production,
shared contract/registry, other pack or baseline edits. Report disagreements;
do not change truth/add xfails. Standard validation and repository handoff,
draft PR no merge/force push. Corpus gate not required for construction.
