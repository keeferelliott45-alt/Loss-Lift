# Repository map

Where things live. Derived from the current tree; update it when the layout
changes. `CLAUDE.md` §7 gives the spec's intended layout.

## Application

| Path | Holds |
|---|---|
| `app.py` | Streamlit entry point and routing. No business logic. |
| `.streamlit/config.toml` | Streamlit configuration. |

## Pipeline (`core/`)

Each stage is importable and testable without Streamlit (`core/__init__.py`).

| Module | Stage / role |
|---|---|
| `core/ingest.py` | Stage 0 — hash, validate, stage to temp storage. |
| `core/classify.py` | Stage 1 — per-page digital/scanned classification. |
| `core/extract_digital.py` | Stage 2a — pdfplumber tables and word-position clustering. |
| `core/extract_vision.py` | Stage 2b — scanned pages only, confidence-capped. |
| `core/profiles.py` | Stage 3 — carrier fingerprinting, column mapping, profile library. |
| `core/normalize.py` | Stage 4 — numbers, dates, status vocabulary. |
| `core/reconcile.py` | Stage 5 — the rule engine (R-01…R-27). |
| `core/review.py` | Review decisions and finding identity; review never replaces reconciliation. |
| `core/pipeline.py` | Orchestration — ingest through reconcile; edit/rebuild boundaries. |
| `core/evidence.py` | Provenance records and page regions, read back before shown. |
| `core/records.py` | Reconstructing logical claims printed across physical lines. |
| `core/account.py` | One insured's loss history assembled from several logical runs. |
| `core/summary.py` | Loss summary by policy term. |
| `core/export.py` | Stage 7 — the `.xlsx` workbook (Claim Detail, Loss Summary, Large Loss, Exceptions, Source Info). |
| `core/schema.py` | Canonical Pydantic v2 models: `DocumentStatus`, `NullReason`, `FindingCategory`, claims. |

## Prompts and generated data

| Path | Holds |
|---|---|
| `prompts/map_columns.md`, `prompts/extract_vision.md` | LLM prompt text. |
| `data/profiles/` | Saved carrier profiles — structure only, **git-ignored**. |

## Tests

| Path | Holds |
|---|---|
| `tests/test_*.py` | Unit, regression and adversarial-handoff suites. |
| `tests/accuracy.py` | Golden-file accuracy harness and the committed baseline loader. |
| `tests/golden/fixtures.py` | Synthetic loss-run definitions (spec §10). |
| `tests/golden/generate.py` | Renders fixtures to PDF and writes expected CSVs. |
| `tests/golden/baseline.py` | Per-carrier accuracy report and the committed ratchet (`--update`). |
| `tests/golden/report.py` | Accuracy table; money threshold and nulls-as-zeros. |
| `tests/golden/expected/` | Hand-written expected CSVs and meta JSON (committed). |
| `tests/golden/accuracy_baseline.json` | The committed accuracy floor. |

Fixture PDFs are generated at test time and never committed.

## Real-corpus gate (`tools/corpus_gate/`)

| Module | Role |
|---|---|
| `__main__.py` | CLI: `init-manifest` and `run`. |
| `manifest.py` | The local manifest: expected documents, byte for byte, plus the digest salt. |
| `runner.py` | Runs the collector once per revision in a worktree of its own. |
| `collect.py` | Measures one revision privately, emitting counts/enums/digest slots only. |
| `compare.py` | Compares the two revisions and classifies each change. |
| `seal.py` | Trust boundary: keys digests outside the revision's process against a strict schema. |
| `sandbox.py` | Runs a collector in a container with no network or credentials. |
| `cloud.py` | Trusted half of the cloud workflow (download, unpack, scrub, stage, summary). |
| `intake.py` | Browser-only corpus growth: propose, approve, publish. |

Procedures: `docs/corpus-gate.md` (local), `docs/cloud-corpus-gate.md` (cloud).

## Benchmark

| Path | Holds |
|---|---|
| `benchmark/run.py` | Runs the real corpus and appends results. |
| `benchmark/corpus_manifest.csv` | One row per real document: what it is and known truth. |
| `benchmark/results.csv` | Append-only per-run measurements. |
| `benchmark/failures.csv` | Every failure, its class and root cause. |
| `benchmark/README.md` | Method, failure taxonomy, false-clean definition. |

## Automation

| Path | Holds |
|---|---|
| `.github/workflows/corpus-gate.yml` | Cloud **Corpus gate**, dispatch from `main` only. |
| `.github/workflows/corpus-update.yml` | Cloud **Corpus update** (propose/publish). |

There is no separate CI test workflow; the full suite and golden ratchet are run
locally or by the agent before a PR.

## Agent configuration

| Path | Holds |
|---|---|
| `kilo.jsonc` | Kilo project config; loads `.kilo/rules/*.md`. |
| `.kilo/rules/` | LossLift guardrails. |
| `.kilo/commands/` | `/start-feature`, `/verify-pr`, `/adversarial-review`, `/corpus-gate`. |
| `.kilo/worktrees/`, `.kilo/agent-manager.json` | Local Kilo state and worktrees — **git-ignored**. |
| `AGENTS.md` | Agent entry point. |
| `docs/kilo-code.md` | How Kilo runs here. |
| `docs/agent/` | Invariants, this map, current state, handoff template. |
| `agent_tasks/TASK_TEMPLATE.md` | Task definition template. |
