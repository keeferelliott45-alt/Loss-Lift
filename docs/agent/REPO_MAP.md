# REPO_MAP — where things are

Navigation only. Rules: `AGENTS.md`. Invariants: `INVARIANTS.md`. Why things
are as they are: `Remember.md`. Product spec: `CLAUDE.md`.

## Data flow

```
PDF ─ ingest ─ classify ─┬─ extract_digital (text layer) ─┐
                         └─ extract_vision (scans; live /  ├─ RawTable per page
                              recorded / replayed)         ┘  + PageEvidence
   → runs.plan_packet (logical runs from page furniture)
   → pipeline: mapping (profiles), locale/date/recovery inference,
     vote_plan + build_claims (claims, refused rows, unplaced rows),
     per-run facts (letterhead, totals, counts, borrowed conventions)
   → LossRunDocument (+ runs, refused_claim_rows, unplaced_rows)
   → reconcile (per run via run_view for packets; R-01…R-29)
   → review.canonical_status / trust_class
   → app (review, edits, resolutions) → export (xlsx / JSON) → account rollup
   → telemetry (local JSONL)            corpus gate (tools/corpus_gate)
```

Orchestration is `core/pipeline.py:run_pipeline` → `_run_pipeline`. Every stage
is callable without Streamlit.

## Modules (core/)

| Module | Responsibility | Key names |
|---|---|---|
| `schema.py` | Pydantic models; the canonical data model | `Claim`, `LossRunDocument`, `LogicalRun`, `RunBoundary`, `RefusedClaimRow`, `UnplacedRow`, `Finding`, `finding_key`, `ReconciliationResult`, `ReviewLog`/`Resolution`, `RawTable`/`RawRow`, enums (`DocumentStatus`, `Severity`, `FindingCategory`, `RunConfidence`, …), `REDACTED_FIELDS` |
| `ingest.py` | hash, stage a private copy, verify source unchanged | `ingest`, `ingest_path`, `verify_source_unchanged`, `discard` |
| `classify.py` | per-page digital vs scanned | `classify_pdf`, `DocumentClassification` |
| `extract_digital.py` | text-layer tables, word clustering, letterhead, page furniture | `extract_pdf`, `extract_page_table`, `extract_metadata`, `pages_metadata`, `page_evidence`, `document_claim_count` |
| `records.py` | multi-line records, identifier shapes | `identifier_shape`, `consensus_shapes`, `group_records` |
| `extract_vision.py` | scans via Gemini; parse; record/replay | `extract_scanned_pages`, `parse_vision_response`, `recording_extractor`, `replay_extractor`, `recording_key` |
| `runs.py` | logical-run planner from page numbering + heading words | `plan_packet`, `plan_runs`, `_Planner`, `same_heading`, `confirms`, `vote_plan`, `vision_evidence`, `unsettled_runs` |
| `normalize.py` | money, dates, status, locale/date-order/recovery inference | `parse_money`, `parse_date`, `infer_locale`, `infer_date_order`, `infer_recovery_sign` |
| `profiles.py` | carrier profiles (structure only), header mapping, LLM mapping | `map_headers`, `fingerprint`, `detect_carrier`, `CarrierProfile`, `load_profile`/`save_profile`, `llm_map_columns` |
| `pipeline.py` | orchestration; claim building; per-run facts; edits | `run_pipeline`, `ExtractionResult`, `ColumnMapping`, `build_mapping`, `build_claims`, `identifier_shapes_by_run`, `collect_printed_totals`/`sections`, `edit_claims`, `apply_edits`, `resolve_finding`, `_borrowed_conventions` |
| `reconcile.py` | rule engine R-01…R-29; packets per run | `reconcile`, `run_view`, `_reconcile_one`, `r28_unsettled_run_boundary`, `_claims_in_two_runs`, `ReconcileConfig`, `registered_rule_ids` |
| `review.py` | the one status policy; review buckets | `canonical_status`, `canonical_run_status`, `blocks_trust`, `trust_class`, `summarise_review` |
| `accounting.py` | per-run claim accounting (read / refused / unplaced / ties) | `claim_accounting`, `RunAccount` |
| `evidence.py` | page evidence rendering, region read-back | `claim_evidence`, `confirm_region` |
| `summary.py` | loss summary by policy term | `summarise_by_period`, `summarise_periods` |
| `export.py` | workbook (Claim Detail, Loss Summary, Runs, Large Loss, Exceptions, Review History, Source Info), account workbook, JSON | `build_workbook`, `to_bytes`, `build_account_workbook`, `build_json`, `to_json_bytes` |
| `account.py` | one insured's history across runs; claim identity | `build_accounts`, `sources_of`, `same_claim`, `AccountRollup` |
| `telemetry.py` | local privacy-safe JSONL events; rates | `document_facts`, `processed_event`, `review_events`, `export_event`, `emit`, `summarize` |

`app.py` — Streamlit screens only (queue, mapping, review, export, accounts);
no business logic. `prompts/` — LLM prompts (`extract_vision.md`,
`map_columns.md`).

## Tests (`tests/`, synthetic only)

| Area | Files |
|---|---|
| Parsing | `test_normalize.py`, `test_currency_marked_amounts.py`, `test_signed_currency_amounts.py` |
| Digital extraction | `test_extract_digital.py`, `test_multiline_records.py`, `test_detail_block_rows.py`, `test_header_reconstruction.py`, `test_page_scale.py`, `test_column_split_tables.py`, carrier-shaped `test_*_regressions.py`/`test_liberty_*` |
| Vision | `test_extract_vision.py`, `test_vision_replay.py`, `test_raster_page_accountability.py` |
| Runs / packets | `test_packet_runs.py`, `test_packet_claim_series.py`, `test_packet_p1_regressions.py`, `test_packet_adversarial.py`, `test_run_metadata.py` |
| Reconciliation | `test_reconcile.py`, `test_section_*`, `test_counted_section_totals.py`, `test_unplaced_money.py`, `test_document_claim_count_scope.py`, `test_numeric_evidence_*` |
| Status / review | `test_status_policy.py`, `test_review*.py`, `test_adversarial_handoff.py` |
| Export / account | `test_export.py`, `test_spreadsheet_export.py`, `test_accounting_and_json.py`, `test_account.py`, `test_account_safety.py` |
| Gate / telemetry | `test_corpus_gate.py`, `test_gate_runs.py`, `test_cloud_gate*.py`, `test_corpus_intake.py`, `test_telemetry.py` |
| Tooling | `test_agent_tooling.py` |
| Golden accuracy | `tests/golden/` — `fixtures.py` (definitions), `generate.py` (PDFs), `expected/`, `baseline.py` + `accuracy_baseline.json` (per-carrier ratchet) |

Shared PDF builders for packet tests: `tests/test_packet_claim_series.py`
(`LARGE_*`/`SMALL_*`, `_large_run`) and `tests/test_packet_adversarial.py`
(`_read`, `_sheet`, `_total`, `LETTER`).

## Corpus gate (`tools/corpus_gate/`)

A/B of two commits over the private real corpus; output is counts, tokens and
keyed digests only.

| File | Role |
|---|---|
| `collect.py` | runs inside each revision; `measure()` groups: claim_count, status, review_status, runs, refused, pages, unplaced, printed, findings, summary, claims, metadata, warnings. Stdlib only; must work on old revisions (normalises absent fields). |
| `seal.py` | strict schema for collector output; published paths; public result |
| `compare.py` | field-by-field comparison, allowlist, exit codes (0 pass, 1 changed, 3 setup, 4 execution) |
| `runner.py`, `__main__.py` | local command (`run`, `init-manifest`, `init-labels`; `--vision-replay`, `--sandbox-image`) |
| `manifest.py`, `labels.py` | corpus manifest (hash-verified, salted) and manual labels — both live outside the repo |
| `cloud.py`, `intake.py`, `sandbox.py` | browser-only cloud workflow support (`.github/workflows/corpus-gate.yml`, `corpus-update.yml`) |

The cloud workflow always runs **main's** gate code. Docs:
`docs/corpus-gate.md`, `docs/cloud-corpus-gate.md`.

## Where to look for common tasks

| Task | Start in |
|---|---|
| A value parsed wrong | `normalize.py`, then `pipeline.build_claim` |
| Columns mis-assigned / header not recognised | `extract_digital.py` (column bounds), `profiles.map_headers`, `pipeline.build_mapping`/`mapping_for` |
| Claims missing or extra | `pipeline.build_claims`, `identifier_shapes_by_run`, `records.py`; check `refused_claim_rows`/`unplaced_rows` |
| Packet split wrong | `runs._Planner.step`, `plan_runs`, `extract_digital.page_evidence` |
| A finding wrong / missing | `reconcile.py` rule fn; packets: `run_view`, `reconcile()` packet branch |
| Status disagreement | `review.py` only |
| Workbook / JSON content | `export.py` |
| Account merging | `account.py` (`same_claim`) |
| Scans | `extract_vision.py`, `prompts/extract_vision.md`, `runs.vision_evidence` |
| Gate measurement | `tools/corpus_gate/collect.py` + `seal.py` (schema) + tests |

## Agent workflow and benchmark

`docs/agent/TOPOLOGY.md` (roles, lifecycle, conflicts, escalation, modes);
`agent_benchmark/` (stack comparison protocol and development tasks; answer
keys are not in the repository).

## Commands

```
python -m pytest -q                         # full suite (~2 min)
python -m pytest -q tests/test_x.py -k name # targeted
python -m tests.golden.baseline             # golden accuracy ratchet (exit 0 = pass)
scripts/agent_validate.sh                   # all pre-handoff checks
scripts/agent_worktree.sh create|check|list|remove
python -m core.telemetry summarize [file]   # local trust rates
streamlit run app.py
```
