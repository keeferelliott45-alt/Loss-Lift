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

### Phase 0 — Consolidate, measure, earn CLEAN (before any Phase 1 work)

**0a. Fix what the benchmark reports.** Done on this branch. It changes
tooling only.
- `benchmark/run.py` writes `canonical_status` (AGENTS rule 6) and keeps the
  engine status beside it.
- `blocking_rules` lists the trust-blocking rule ids and counts, with no
  message text.
- With `--qualification <qualification.json> --manifest <gate manifest>` it
  fills money precision and recall, and a truth-based false-CLEAN.
- Unmeasured values stay blank.

**0b. Label and measure.** Owner only.
1. Label truth for the 6 benchmarked documents with `tools.qualification`:
   skeleton → review → sign_off.
2. Run `tools.qualification run`, then `benchmark.run --qualification ...`,
   on `main`.
3. Commit only the benchmark rows.

**0c. Earn CLEAN honestly: at least 2 real documents, target 3.** There are
only two allowed routes:
- **(a) A general engine fix.** It needs a synthetic regression fixture
  reproducing the structure, a passing `scripts/agent_validate.sh`, and
  corpus-gate exit 0 with every changed document explained. Opus high.
- **(b) A mapping a person confirms and saves as a carrier profile** (spec §5
  stage 3). The columns then map as `PROFILE_MATCH`, so R-21 no longer fires.
  Source Info names the profile and records that a person confirmed it.

CLEAN is never reached by a resolution, by a special case for one document, or
by loosening a rule.

Candidates:
- **Illinois:** route (b), choosing Total vs Net incurred.
- **AIG, CNA, Liberty:** route (a). Which fixes they need depends on the 0b
  `blocking_rules`.
- **XL:** excluded. The carrier prints no incurred total, so it stays amber by
  design.

**0d. Earlier Phase 0 items.**
1. Merge #41.
2. Land #46, then gate #49, #52 and #56.
3. Fix refused rows that no rule names (gap 8). GATE-REQUIRED.
4. Triage the drafts down to 5 or fewer.
5. Commit the northstar mod.

Rule: grep `main` before planning any fix taken from CURRENT_STATE.

**Done when:**
- money accuracy is recorded per real document;
- at least 2 real documents read canonical CLEAN, each with:
  - qualification false-CLEAN of 0;
  - money accuracy of at least 99.5% (spec §10);
  - a recorded route, (a) or (b);
- 0d is complete.

If fewer than 2 can get there honestly, stop and report each document's
blocking rule. Nothing is loosened.

### Phase 1 — Account MVP and demo account (3–4 weeks)

**Demo user:** an MGA underwriter receiving a submission email for one
small-commercial insured, covering 3 carriers and 5 policy years. Each loss run
is **synthetic but built from a real carrier's structure**, with every name,
number and id invented (spec §9):

| Carrier | Line | Built like |
|---|---|---|
| Carrier A | Auto | AIG three-line boxed records |
| Carrier B | GL | XL spreadsheet export |
| Carrier C | WC | Illinois, grouped by term with subtotals |

The scenario plants:
- one run valued too long ago;
- one missing year;
- one claim that is in an older run and missing from the newer one;
- one claim whose incurred develops between valuations;
- premium entered for 4 of the 5 terms.

`scripts/demo_account.py` reuses the format-family builders (in
`tests/test_multiline_records.py`, `test_spreadsheet_export.py`,
`test_section_grouped.py` and `test_detail_block_rows.py`). It writes the
PDFs and a `.eml` outside the repository, and the result runs through the
existing email intake.

**Build:**
- Lookback and valuation-currency checks, configurable, defaulting to 5 years
  and 90 days. These are triage T-rules in `core/intelligence.py`. They never
  touch R-rules or status.
- A per-year account loss summary across carriers, plus premium entered per
  term as a `manual` fact, giving a loss ratio. **Owner-approved: a table, no
  charts.**
- A broker request list.
- An account-first entry screen.
- A one-page summary.
- **An ACORD 125 loss-history sheet** (owner-approved: a table laid out like
  the form's loss-history section, never a filled form).
  - Columns: date of occurrence, line of business, description, date of
    claim, amount paid, amount reserved, subrogation, open/closed.
  - Every value carries its page.
  - Subrogation reads Y only where the carrier printed a subrogation or
    recovery entry. Otherwise it is blank and marked "not printed", never
    inferred.
  - The column order is checked against the current edition before release.

**Done when:**
- The demo account produces the expected workbook:
  - per-year rows equal the sum of the runs;
  - a loss ratio on 4 terms and "premium not entered" on the fifth;
  - a request list naming the missing year and the stale run;
  - the ACORD sheet's rows match the account's claims one to one, with page
    references.
- A test proves no Phase 1 code changes `canonical_status`.

### Phase 2 — Account demo and discovery calls (2–3 weeks)
1. Rebuild the demo pack (#44) around the Phase 1 synthetic account, shown
   from the MGA underwriter's side.
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
