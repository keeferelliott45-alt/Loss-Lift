# E1 — absolute qualification contract and evaluator

Base: B0 `0f13c5c90a66621774000c6eb368df82b10e95cd`.
Branch: `agent/sol/qualification`. PR target: `agent/lead/reliability-phase-1`.
Own only `tools/qualification/**`, `tests/qualification/**` except pack
folders, and `docs/qualification.md`. No production/golden/corpus-gate edits.

## Outcome and invariants

Implement `score_result(result: ExtractionResult, truth: DocumentTruth)
-> QualificationMetrics`, private truth validator, current committed checkout
runner, and frozen `build_cases(root: Path) -> list[QualificationCase]` interface.
Read AGENTS, REPO_MAP, CURRENT_STATE and invariants 1,2,3,4,7,8,9,10.

Truth version 1 verifies document identity/hash, format family, adjudication,
all page/run membership, distinct claim occurrences with source anchors,
explicit expected fields, document/run/section printed facts and canonical
document/run status. Money is finite Decimal strings, not floats. Known,
absent, ambiguous and unscorable labels are distinct. Complete truth labels
every critical field explicitly; partial/unknown truth is not qualification.

Match one-to-one using anchors and identifiers, never values/money. Duplicate
identifiers must not collapse. Ambiguous components stay visible/unscored.
Report claim and money/critical-field precision AND recall, page/run/claim
accounting, false CLEAN, status agreement, review rate, scored coverage,
overall and automatically accepted subsets. Automatic acceptance requires
canonical document/run CLEAN with no pending mapping. Extra populated fields
reduce precision; missing values reduce recall; null-as-zero is separate.
Zero denominators yield null rates with counts, never 100%.

CLI: `python -m tools.qualification validate --manifest ... --truth ...`;
`run --corpus ... --manifest ... --truth ... --out ... [--vision-replay ...]`.
Private inputs/output stay outside Git. Verify complete labels and byte identity.
Run only the current clean committed checkout; disable live model calls,
isolate profiles/temp sources and treat missing replay as unresolved. Public
outputs include only fixed categories, counts/rates, SHA and sealed identifiers:
no claim text/values, filenames, paths or exception representations.

Frozen case exports generated PDF path, DocumentTruth, optional offline
synthetic replay/extractor, and independently declared supported/review
expectations. Packs do not edit discovery registries. Printed content/truth
must be reproducible; PDF bytes need not be identical across builds.

## Tests first / acceptance

Replacement with tied count, duplicate occurrences, wrong page/run, swapped
money with tied totals, signed recovery, null/zero, ambiguous identity, CLEAN
engine with pending mapping, zero denominator, missing labels, changed bytes,
sensitive values in error/output paths, synthetic runner and replay failure.
Run targeted, full, golden and agent_validate. Production behavior unchanged.
No new behavioral corpus gate required. Exact contract head becomes Q1.
Stop for ownership/interface conflicts; use repository handoff. Never merge.
