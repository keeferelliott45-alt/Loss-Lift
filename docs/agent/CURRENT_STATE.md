# Current state

What exists in this repository now, derived from the tree. Counts that change
(test totals, accuracy figures) are intentionally not copied here — run the
command to get the current number.

## Pipeline

All seven stages exist as separate modules in `core/` and are importable without
Streamlit: ingest, classify, digital extraction, vision extraction, column
mapping/profiles, normalisation, reconciliation, review, export
(`docs/agent/REPO_MAP.md`). Orchestration lives in `core/pipeline.py`.

- `core/schema.py` defines the canonical Pydantic v2 models, `DocumentStatus`
  (`CLEAN` | `NEEDS_REVIEW`), `NullReason` and `FindingCategory`.
- `core/reconcile.py` implements the rule engine, currently **R-01…R-27**, each
  with an explicit severity (`ERROR` blocks a clean export; `WARN`/`INFO` do
  not). R-04 and R-05 verify against the carrier's printed totals.
- `core/records.py` reconstructs logical claims printed across physical lines;
  `core/account.py` assembles one insured's loss history from several logical
  runs; `core/summary.py` builds the per-term loss summary.
- `core/review.py` records reviewer decisions beside immutable findings;
  `core/evidence.py` carries per-field provenance and page regions.
- `core/export.py` writes Claim Detail, Loss Summary, Large Loss, Exceptions and
  Source Info sheets.
- `core/profiles.py` fingerprints a carrier and reuses a saved profile (zero LLM
  calls on a match); profiles are structure only.

## Tests and measurement

- `tests/` holds the unit, carrier-regression and adversarial-handoff suites.
- `tests/golden/` generates synthetic fixtures at test time, compares
  field-by-field, and keeps a committed per-carrier accuracy baseline
  (`tests/golden/accuracy_baseline.json`). Expected CSVs are committed;
  fixtures themselves are not.
- `benchmark/` records real-document measurements and a failure taxonomy, with
  false-clean as the defect that matters. The real PDFs it describes are not in
  the repository.

Commands: `python -m pytest`, `python -m tests.golden.baseline [--update]`,
`python -m tests.golden.report`, `python -m benchmark.run --docs <dir> --label
<label>`.

## Corpus gate

- The local gate is implemented under `tools/corpus_gate/` and invoked with
  `python -m tools.corpus_gate run ...`; exit 0 is the only pass
  (`docs/corpus-gate.md`).
- The cloud, browser-only path is the **Corpus gate** workflow and the **Corpus
  update** workflow, both dispatched from `main`
  (`.github/workflows/`, `docs/cloud-corpus-gate.md`).
- The corpus and its manifest live outside the repository; the manifest holds
  the digest salt.

## Automation

`.github/workflows/` contains the two corpus workflows. There is no general CI
workflow that runs the test suite; tests and the golden ratchet are run locally
or by the agent before a pull request.

## Agent configuration

`kilo.jsonc` loads `.kilo/rules/*.md`. `.kilo/commands/` provides
`/start-feature`, `/verify-pr`, `/adversarial-review` and `/corpus-gate`.
`AGENTS.md` is the entry point; `docs/agent/` holds the invariants, repository
map, this state file and the handoff template. Generated Kilo state and
worktrees under `.kilo/` are git-ignored.

## Open direction

The product direction is fixed: a buyer-neutral trusted loss-history engine with
explicit accountability for every logical run, claim and amount. Do not plan
ACORD/SOV ingestion, carrier portals, BMS integrations or underwriting rules
without customer evidence. The measurement objective is the AUTO-SAFE
(`CLEAN`) vs NEEDS_REVIEW vs unresolved rate by document class.
