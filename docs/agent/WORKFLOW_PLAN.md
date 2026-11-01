# LossLift — workflow plan v2 (approved 2026-11-01, after peer review)

**Direction (2026-11-01):** LossLift becomes an account loss-history tool —
see `docs/agent/DISCOVERY.md` for the hypothesis, the gap matrix and the
interview kit. Phases below follow it.

v1 was reviewed by an independent agent; its three blockers (R-18 misread as
trust-blocking, a vacuous "zero false-CLEAN" claim, a Phase 2 that could break
CLAUDE.md §2.4) were verified against the code and are fixed here.

Inputs: AGENTS.md, CLAUDE.md, CURRENT_STATE.md (stale, 2026-09-27; rewrite in
PR #41), Remember.md, `benchmark/`, open draft PRs. The real corpus is not in
the repository; real-document statements come from `benchmark/results.csv`,
`benchmark/failures.csv` and `benchmark/corpus_manifest.csv`, **last run at
`bbf1062` (2026-09-01), about 20 merges behind `main`**. Phase 0 reruns it.

---

## 0. Market position (verified 2026-11-01)

Tie-out to printed totals is **reported by competitors**:
[FurtherAI](https://furtherai.com/engineering-blogs/hardest-document-extraction-problem-in-insurance)
validates claims against summary totals and count;
[Outmarket AI](https://outmarket.ai/blog/outmarket-ai-launches-ai-powered-loss-run-extraction-and-analysis)
and [Heron](https://www.herondata.io/blog/heron-launches-100-coverage-for-parsing-loss-runs)
describe flagging variance against stated totals;
[Sensible](https://www.sensible.so/extract/loss-runs) extracts and computes
totals. Submission intake for MGAs is shipping:
[Vertafore Velocity](https://www.vertafore.com/resources/press-releases/vertafore-introduces-velocity-ai-submission-processing-agent-help-mgas)
(July 2026, completeness checks) and Applied/Cytora
([acquisition](https://www.insurancebusinessmag.com/us/news/mergers-acquisitions/applied-systems-acquires-digital-risk-processing-platform-549107.aspx),
[Autopilot, Octave MGA deal](https://iireporter.com/?p=58657)).

Pitch: **verifiable, fail-closed** — no LLM reads a number on a digital page;
per-field provenance with the row read back and drawn on the page
(`core/evidence.py`); no silent claim disappearance; foreign totals refused
(F07); a real-corpus A/B gate.

Not claimable yet: **false-CLEAN rate.** No benchmarked document reads CLEAN at
`bbf1062`, so false-CLEAN is untested. History holds one real false-CLEAN
(F19, Illinois, recorded `false_clean=no` at `8815244`/`7c257ad` — the column
was wrong) and one more (F17, AIG, zero claims read CLEAN). Money accuracy
(`money_fields_exact`) has never been measured.

---

## 1. What reads NEEDS_REVIEW today, and why (n = 6 benchmarked)

`corpus_manifest.csv` holds 6 documents; corpus-v1 in the gate holds 10, and
the other 4 have no benchmark row. All 6 extract the expected claim count and
read NEEDS_REVIEW at `bbf1062`.

Trust-blocking causes (`core/review.py` `blocks_trust`: any EXTRACTION or
FINANCIAL finding, any ERROR, or `UNACCOUNTED_RULES`):

| # | Gap | Evidence | Reach (est.) | Kind |
|---|---|---|---|---|
| 1 | ~~False-CLEAN path: `Claim No:` rows dropped as furniture~~ — **already fixed on `main`** (`7d7a0c4`, PR #24; `tests/test_labelled_identifiers.py`). CURRENT_STATE still lists it (stale) | — | — | Closed |
| 2 | **Fact not printed or printed ambiguously**: no incurred total (XL, R-07 ×84), Total vs Net Incurred (Illinois, R-21), no valuation date (CNA, R-06) | 3/6 | Most loss runs | Correct NEEDS_REVIEW; no fast human decision path |
| 3 | **Vocabulary**: `Receipt Date` (AIG), `Expenses Total` (CNA), `Receivable` with sign (Italian, false R-01) | 3/6 | Every new carrier | Engine gap |
| 4 | **Packets**: board/broker packets (Liberty, Italian); loss-run section finding (F10 open) | 2/6 | Public entities, brokers | Engine gap, partly closed |
| 5 | **Carrier read from a column label or the insured's cover sheet** (blind spot 4, F13) | 2/6 | Breaks account identity | Engine gap |
| 6 | **Scan count not adopted → R-27 (unresolved printed count)**; fix in #49; no real-corpus cover for the scanned path (blind spot 3) | 1/6 | Older/faxed runs | Engine gap |
| 7 | **Two printed columns merged** at sub-glyph gutters (XL status+claimant) | 1/6 | Spreadsheet exports | Schema gap |
| 8 | **Refused rows on a bounded single report are named by no rule** | known gap | Unknown | Accounting gap |
| 9 | Non-English runs | 1/6 | — | Out of scope (§13) |

Known-gap tests (strict xfail): `test_packet_adversarial.py:348`,
`test_run_view_pinning.py:172`, `test_raster_page_accountability.py:1168,1195`,
`test_liberty_gap_margin.py:254,315`.

Non-blocking flags: **R-18** (deductible/ALAE basis unknown) fires on every
document but is UNDERWRITING/WARN (`core/reconcile.py:799`) and does **not**
cause NEEDS_REVIEW.

---

## 2. The underwriter workflow and what LossLift could own

Submission → clearance → intake → completeness → **loss analysis** → exposure →
risk assessment → rating → referral/authority → quote/decline → bind.

| Order | Step | Depends on | Built today |
|---|---|---|---|
| 1 | Loss-run reading and tie-out | — | Yes |
| 2 | Email intake, attachment inventory | 1 | Pilot (`core/eml_intake.py`, `core/submission.py`) |
| 3 | Account loss history across years/carriers without double counting | 1, gap 5 | Partly (`core/account.py`) |
| 4 | Completeness: years covered, valuation age, missing terms | 2, 3 | Partly |
| 5 | Loss analysis pack: frequency, severity, large losses, development | 3; premium for loss ratio | Loss Summary sheet only |
| 6 | Underwriter memo: narrative with every figure linked to its page | 5 | No |
| 7 | Exposure intake (SOV, payroll, revenue) | new readers | No — §13 |
| 8 | Appetite/referral, quote support | 5, 7 | No — §13 |

Own 1–6; 7–8 is where Applied/Cytora and Vertafore already sell.

---

## 3. Flagship demo

Moments: (1) a packet in → per-run badges with pages; (2) click a finding → the
row drawn on its page; (3) a planted $50k misread caught by R-04 against the
carrier's own total; (4) fix the cell → every rule re-runs → green, original
kept beside the edit.

**Minimum version:** the synthetic demo pack (#44) driven locally with
`scripts/demo_submission.py`, a 3-minute recording of moments 1, 2, 4 (3 too),
and a write-up whose every number traces to a committed benchmark row.
**No hosting** in the minimum: §8 cost, LLM mapping key spend, and §2.6/§9
retention on a public upload box. No "a general LLM misses it" comparison
unless that run is reproducible and published.

---

## 4. §13 conflicts (flag only; spec unchanged)

| §13 item | Conflicting step | Effect |
|---|---|---|
| Database persistence | Account history across valuations (3, 5); submissions across days (2, 4) | Blocks 3–5 beyond one session |
| Analytics, charts, trending | Frequency, severity, loss ratio, development (5) | Blocks 5 |
| User accounts, roles | A named reviewer on an audited export (Phase 3); referral/authority (8). Today only `LOCAL_REVIEWER` | Limits Phase 3; blocks 8 |
| ACORD auto-fill (+ AGENTS rule 9 bans ACORD/SOV) | Reading apps and SOVs (2, 7) | Blocks 7 |
| AMS/CRM integrations | Live inbox intake, AMS push | Limits 2 |
| Automated loss-run retrieval | Requesting a missing year (4) | Limits 4 |
| Background queues | Large packets, multi-attachment emails | Limits 2 at scale |
| Non-English | Italian packet | Enforced |
| §2.6 / §9 data handling | Memo (6) sending loss data to an LLM | Requires placeholder tokens: the model never sees figures or names |
| §8 free tiers, §15 kill criteria | Hosting; #61 proposes restating kill criteria | Owner decision |

Steps 5 and 6 depend on 3, so they inherit its persistence/analytics blockers.

---

## 5. Agent and effort routing

| Work | Model / effort |
|---|---|
| `core/reconcile.py`, `core/records.py`, `core/runs.py`, `core/review.py`, `core/account.py`, `core/pipeline.py` (`is_structural_row`), schema, anything GATE-REQUIRED, phase specs | Opus, high |
| Vocabulary additions with fixtures, carrier detection | Sonnet, medium |
| Streamlit UX, export sheets, demo pack, write-up | Sonnet, medium |
| Pinning existing behaviour, mutation checks, fixture generation | Sonnet, low–medium |
| Lookups, benchmark tabulation, PR triage, doc drift, research | Haiku, low (standard search; extended only if thin) |
| Review before merge | `/code-review` medium; high for the Opus paths above |
| Corpus gate, corpus labelling | Owner only |

Lower tiers may find and pin; only the top tier changes what a rule concludes
or what status a document reads.

---

## 6. Claude Code mods

**losslift-northstar:** zip not in the repository or container; not reviewed.
On commit, check: band text read from one source file; scope-drift toast keys
on §13 terms **and** the Opus paths in §5; `/northstar` prints the current
phase and its "done when" only; no hook reads files under the corpus or `data/`.

Build, in order:
1. **Gate guard hook** (PreToolUse Edit/Write): editing a §5 Opus path marks
   the branch GATE-REQUIRED, reminds about `scripts/agent_validate.sh`, and
   warns when the session model is not the top tier (routing check merged in).
2. **Private-data guard**: blocks writes and reads under the corpus path,
   `data/`, manifests, salts, labels, telemetry, `.env`; blocks long
   document-like text in commit messages. Not a blanket `*.pdf` block —
   synthetic fixtures and the demo pack are PDFs.
3. **Status line**: branch, golden ratchet, open GATE-REQUIRED PRs.
4. **`/handoff`**: fills `HANDOFF_TEMPLATE.md` from diff and test output.
5. `/archmap` (REPO_MAP regeneration, layer-crossing check) — deferred until
   after Phase 3.

---

## 7. Phases

### Phase 0 — Consolidate and close the false-CLEAN path (2 weeks)
1. Owner reruns benchmark and corpus gate on `main`; §1 is rebased on it.
2. Merge #41 (CURRENT_STATE describes `main`).
3. Land #46 (vision in gate), then gate #49, #52, #56.
4. Confirm gap 1 is closed on `main` (PR #24) and drop it from CURRENT_STATE
   in #41. The first open fail-closed item is gap 8 (refused rows named by no
   rule): fix with a synthetic fixture. GATE-REQUIRED, Opus high. Rule: grep
   `main` before planning any fix taken from CURRENT_STATE.
5. Triage remaining drafts; commit and review the northstar mod.

**Done when:** a gate run on `main` is recorded; the gap-8 test fails before
the fix and passes after; ≤5 open drafts, each with a next step;
CURRENT_STATE matches `main`.

### Phase 1 — Account MVP gaps (3–4 weeks)
Build the missing items 7–11 and 14 from `docs/agent/DISCOVERY.md`:
- lookback check (default 5 years) and currency check (default 90 days),
  both configurable;
- account loss summary by policy year across carriers;
- premium entry per term, carrying `manual` provenance, giving a loss ratio;
- broker request list;
- account-first entry;
- one-page summary.

All of it sits after reconciliation. It reads `canonical_status` and never
writes it. Routing: Opus high for `core/account.py` and
`core/intelligence.py`, Sonnet medium for UI and export. **Owner decision:** a
per-year loss ratio table extends the spec §5 Loss Summary. It is not a
dashboard, but confirm against §13 analytics first.

**Done when** a synthetic account produces the expected workbook. The account:
3 carriers, 5 years, one stale valuation, one missing year, one dropped claim,
one claim that develops between valuations, premium entered for 4 of 5 terms.
The workbook must have:
- per-year rows that equal the sum of the runs;
- a loss ratio on 4 terms and "premium not entered" on the fifth;
- a request list naming the missing year and the stale run.

A test proves no new code changes `canonical_status`.

### Phase 2 — Account demo and discovery calls (2–3 weeks)
1. Rebuild the demo pack (#44) around the Phase 1 synthetic account.
2. Record the 3-minute demo and write the write-up (section 3).
3. Run 8–12 calls with the interview kit in `DISCOVERY.md` and apply its
   decision gate.

**Done when:**
- the gate decision is recorded with counts;
- a top-10 carrier list exists;
- at least 3 blank loss-summary templates are collected.

If the gate fails, stop and re-plan before Phase 3.

### Phase 3 — Measure honestly, read the target carriers (6–8 weeks)
1. Grow the corpus to at least 15 documents, owner only, prioritising the
   Phase 2 top-10 carriers.
2. Label each document with R1-labeller truth (`19eb0d9`).
3. Fill in `money_fields_exact` and `claim_precision`, and derive
   `false_clean` from truth.
4. Fix gaps 3 and 5 with synthetic fixtures.
5. Add loss-summary layouts matching the collected templates.

**Done when:**
- every document has labelled truth;
- money accuracy is reported per carrier;
- `false_clean` is computed against truth and is 0;
- every NEEDS_REVIEW document has a root-caused reason;
- the golden ratchet holds or rises.

### Phase 4 — Reviewer decisions (3 weeks; GATE-REQUIRED; Opus high)
There are exactly two mechanisms:
- **(i) A person-supplied value or mapping** (valuation date, choice of
  incurred column, deductible/ALAE basis) is an edit with method `manual`. The
  extracted or absent value stays beside it, every rule runs again, and status
  comes only from `canonical_status`.
- **(ii) Everything else**, including accepting that no incurred total was
  printed, is a ReviewLog resolution. It never moves status or `trust_class`.

A mapping chosen for one document reaches the carrier profile only on explicit
confirmation.

**Done when** all four hold:
- a test proves no resolution changes `canonical_status`;
- every supplied fact carries `manual` provenance and appears in the export;
- a test proves the profile is not written without confirmation;
- the gate shows no document reaching CLEAN through resolutions alone.

### Deferred
- Renewal comparison across sessions: needs persistence, which §13 rules out.
- Hosting: §8 and §2.6/§9 constrain it.
- Underwriter memo.
- Exposure intake and appetite rules.
- Loss-run retrieval from carriers: an adjacent market we are choosing not to
  enter.

Every phase: `scripts/agent_validate.sh` and the golden ratchet. Every
GATE-REQUIRED change needs corpus-gate exit 0, run by the owner.
