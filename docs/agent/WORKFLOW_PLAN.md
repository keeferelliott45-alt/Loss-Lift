# LossLift — workflow plan (proposal, 2026-11-01)

Status: **proposal for owner approval. Nothing here is built or authorised.**
Inputs: AGENTS.md, CLAUDE.md, CURRENT_STATE.md (stale: dated 2026-09-27,
rewrite pending in PR #41), Remember.md, `benchmark/`, 18 open draft PRs.
The real corpus is not in this container; every real-document statement below
comes from `benchmark/results.csv`, `benchmark/failures.csv` and the manifest.

---

## 0. Correction to the planning input

"Nobody advertises tie-out to printed carrier totals" is **false**.
FurtherAI publishes a validation step that checks extracted claims against the
document's summary totals and claim count; Outmarket AI and Heron describe
flagging variance against stated totals. Applied/Cytora (Autopilot, Octave MGA
deal) and Vertafore Velocity (submission agent for MGAs, July 2026, completeness
checks) are confirmed.

Consequence: "we reconcile to printed totals" is not a moat on its own. What
is still defensible from what is built:

- numbers never read by an LLM on digital pages (§2.1);
- per-field provenance with the source row **read back and drawn on the page**,
  withdrawn when it does not match (`core/evidence.py`);
- fail-closed accounting: no silent claim disappearance, logical runs in
  packets, foreign totals refused (F07), zero false-CLEAN on the corpus;
- a real-corpus A/B gate that blocks any behaviour change nobody approved.

The pitch should be **"verifiable, fail-closed"**, not "reconciled".

---

## 1. What fails or reads NEEDS_REVIEW today, and why

All 6 benchmarked real documents (latest engine `bbf1062`) extract the right
claim count and read **NEEDS_REVIEW**, with no false-CLEAN. corpus-v1 holds 10.
That sample is too small to rank by real frequency, so the ranking below
combines corpus evidence with how common each mechanism is across carrier
formats generally (marked *est.*).

| # | Gap | Corpus evidence | Reach (est.) | Kind |
|---|---|---|---|---|
| 1 | **A fact the carrier never printed or printed ambiguously**: no incurred total (XL, R-07 ×84), Total vs Net Incurred (Illinois, R-21), no valuation date (CNA, R-06), deductible/ALAE basis unknown (R-18, every doc) | 4/6 | Most loss runs | Correct NEEDS_REVIEW; the gap is **no fast way for a person to decide** |
| 2 | **Vocabulary**: `Receipt Date` (AIG), `Expenses Total` (CNA), `Receivable` with a sign (Italian, false R-01) | 3/6 | High: every new carrier brings labels | Engine gap (mapping + sign convention) |
| 3 | **Compound documents / packets**: board packets, broker packets binding several runs (Liberty 99 pp., Italian 23 pp.); loss-run section finding (F10 open) | 2/10 corpus are packets | Medium–high for public entities and brokers | Engine gap, partly closed |
| 4 | **Scanned pages**: vision off in cloud; a clean scan always reads NEEDS_REVIEW (R-27); fix in draft PR #49 (gate-required) | 1/6 | Medium (older/faxed runs) | Engine gap |
| 5 | **Carrier read from a column label or the insured's cover sheet** (blind spot 4, Italian F13) | 2/6 | Medium; breaks account identity | Engine gap |
| 6 | **Two printed columns merged into one** at sub-glyph gutters (XL status+claimant): mapping cannot give one source column two fields | 1/6 | Low–medium (spreadsheet exports) | Schema gap |
| 7 | **`Claim No:`-labelled rows dropped as furniture**; can read CLEAN with no printed total | synthetic probe only | Low, but a false-CLEAN path | Fail-closed bug |
| 8 | Non-English runs (Italian) | 1/6 | Out of scope (§13) | Spec |

Takeaway: the biggest lever is **not extraction** — it is turning gap 1 into a
two-click reviewer decision so a correct NEEDS_REVIEW becomes an exportable,
audited result. Gaps 2, 5 and 7 are the next engine work. A 10-document corpus
is the main measurement limitation; growing it is Phase 1.

---

## 2. The underwriter workflow and what LossLift could own

Submission arrives → clearance → intake → completeness → **loss analysis** →
exposure analysis → risk assessment → rating → referral/authority → quote or
decline → bind.

| Order | Step LossLift could own | Depends on | Built today |
|---|---|---|---|
| 1 | Loss-run reading and tie-out | — | Yes (core) |
| 2 | Submission intake from email, attachment inventory | 1 | Pilot (`core/eml_intake.py`, `submission.py`) |
| 3 | Account loss history: several years, carriers, policies merged without double counting | 1, carrier/policy identity (gap 5) | Partly (`core/account.py`) |
| 4 | Completeness check: "5 years of loss runs, valued within 90 days, every term covered" | 2, 3 | Partly (submission "Outstanding") |
| 5 | Loss analysis pack: frequency, severity, large losses, open-reserve adequacy, development across valuations | 3; premium/exposure for loss ratio | Loss Summary sheet only |
| 6 | Underwriter memo draft: loss narrative with every figure linked to its page | 5; LLM writes words, never numbers | No |
| 7 | Exposure intake (SOV, payroll, revenue) for loss ratio and rating | New readers | No — §13 conflict |
| 8 | Appetite / referral rules, quote decision support | 5, 7, per-MGA guidelines | No — §13 conflict |

LossLift should own 1–6. Steps 7–8 are where Applied/Cytora and Vertafore are
already selling; enter them only on top of a trusted loss analysis.

---

## 3. What makes it presentable as a flagship

Strongest moments, all built or nearly built:
1. Drop a messy real-shaped packet in → per-run badges, each with its page.
2. Click a finding → the page with the exact row drawn on it.
3. A planted $50k misread → R-04 catches it against the carrier's own total;
   the same document pasted into a general LLM does not.
4. Fix the cell → every rule re-runs → badge turns green, the original value
   is kept beside the edit.
5. A saved email with 4 loss runs → one account loss summary traceable to the
   page.

**Minimum version:** the synthetic demo pack (draft PR #44) + a hosted
Streamlit instance + a 3-minute recording of moments 1–4 + a 1,500-word
write-up ("why reconciliation, not extraction": fail-closed design, the corpus
gate, the false-CLEAN count of zero, the F-code failure registry as honest
engineering). Gap 1 must be solved first or every demo ends on amber.

---

## 4. §13 conflicts with the workflow vision (flag only; spec unchanged)

| §13 item | Conflicting workflow step | Severity |
|---|---|---|
| Database persistence of claims (session-only) | Account history across valuations (3, 5); submission tracking across days (2, 4) | **Blocks 3–5 beyond one session** |
| Analytics dashboards, charts, trending | Loss analysis pack: development and trending are core underwriting (5) | Blocks 5 as underwriters expect it |
| ACORD form auto-fill | Reading ACORD apps in a submission (2, 7). Reading ≠ filling, but AGENTS rule 9 bans ACORD/SOV outright | Blocks 7 |
| AMS/CRM integrations | Live inbox intake (2 beyond `.eml`), pushing results to AMS | Limits 2 |
| Automated loss-run retrieval from carriers | Completeness: requesting the missing year (4) | Limits 4 |
| User accounts, auth, teams, roles | Referral/authority (8); any multi-user submission | Blocks 8 |
| Background job queues | 99-page packets and 4-attachment emails in a request | Limits 2 at scale |
| Non-English documents | Italian packet (gap 8) | Already enforced |
| Also §8 "free tiers only" and §15 kill criteria | Hosting a flagship demo; PR #61 already proposes restating kill criteria | Owner decision |

---

## 5. Agent and effort routing

| Work | Model / effort | Why |
|---|---|---|
| Reconciliation rules, `records.py`, `runs.py`, `review.py` status policy, schema changes, any GATE-REQUIRED change | Opus, high | Fail-closed correctness; a wrong guess here is a false-CLEAN |
| Architecture decisions, phase specs, §13 change proposals | Opus, high, once per phase | Low volume, high leverage |
| Vocabulary additions with a regression fixture, carrier-detection fixes | Sonnet, medium | Pattern work against existing tests |
| Streamlit review UX, export sheets, demo pack, write-up draft | Sonnet, medium | Volume work, low correctness risk |
| Pinning existing behaviour with tests, mutation checks, fixture generation | Sonnet, low–medium | Mechanical |
| Lookups, grep, benchmark tabulation, PR triage summaries, doc drift checks | Haiku, low | Cheap and read-only |
| Market/competitor research | Haiku with standard search; escalate to extended only if thin | Cost |
| Code review before merge | `/code-review` medium; high for core/ | Catches what the author missed |
| Corpus gate runs | Owner (local) or the cloud workflow | Private data; no agent touches the corpus |

Rule of thumb: an agent at a lower tier may **find and pin**; only the top
tier may **change what a rule concludes**.

---

## 6. Claude Code mods

**losslift-northstar:** the zip is not in this repository or container, so it
could not be reviewed. Commit it (or upload it) and the review will check:
- the north-star band text is read from one file (this plan or CLAUDE.md §1),
  not hard-coded;
- the scope-drift toast keys on §13 terms *and* on paths (`core/reconcile.py`,
  `core/records.py`, `core/runs.py` → "GATE-REQUIRED"), not on prose alone;
- `/northstar` prints the current phase and its "done when", not the whole spec;
- no hook reads or logs file contents under the corpus or `data/`.

**Others worth building, in order:**
1. **Gate guard hook** (PreToolUse on Edit/Write): editing extraction or
   reconciliation modules marks the branch GATE-REQUIRED and reminds about
   `scripts/agent_validate.sh`.
2. **Private-data guard hook**: blocks writing `*.pdf`, corpus manifests,
   salts, `.env`, telemetry, and blocks pasting long document-like text into
   commit messages.
3. **Architecture builder** (`/archmap`): regenerates `docs/agent/REPO_MAP.md`
   from imports, flags a new dependency that crosses layers (e.g. `app.py`
   importing logic, export computing status instead of `core.review`).
4. **Status line**: branch, golden ratchet result, open GATE-REQUIRED PRs.
5. **`/handoff`**: fills `HANDOFF_TEMPLATE.md` from the diff and test output.
6. **Routing hook**: when a task file names `core/reconcile.py` and the session
   model is not the top tier, warn before the first edit.

---

## 7. Phased plan

### Phase 0 — Consolidate (1–2 weeks)
Triage the 18 open drafts: merge or close; run the corpus gate on #49, #52,
#56; merge #41 so CURRENT_STATE matches main; commit the northstar mod.
**Done when:** ≤5 open drafts, each with a stated next step; every
GATE-REQUIRED PR has a gate result; CURRENT_STATE describes `main`.

### Phase 1 — Read more loss runs (3–4 weeks)
Grow the corpus to ≥25 documents from public sources (procurement records,
board packets). Tag every NEEDS_REVIEW cause as *decision needed* or *engine
gap*. Fix gaps 2, 5, 7 with synthetic regression fixtures.
**Done when:** every corpus document has a root-caused reason; zero false-CLEAN;
no corpus document has an untriaged *engine gap*; golden ratchet unchanged or up.

### Phase 2 — Decisions, not dead ends (2–3 weeks)
Reviewer resolves *decision needed* findings in place: choose the incurred
column, enter a valuation date, set deductible/ALAE basis, accept a nil
incurred. Each resolution recorded beside the finding (§2.4); only a changed
value re-runs rules.
**Done when:** each benchmark document goes from upload to an audited export in
under 5 minutes, and the export says which facts a person supplied.

### Phase 3 — Flagship demo (1–2 weeks, can overlap Phase 2's end)
Demo pack, hosted instance, recording, write-up (section 3).
**Done when:** someone who has never seen LossLift runs moments 1–4 from the
hosted link without help, and the write-up states the corpus numbers.

### Phase 4 — Account loss analysis (4 weeks) — needs §13 decisions
Multi-year, multi-carrier history; development across valuations; frequency,
severity, large losses; loss ratio when premium is entered by hand.
**Done when:** a 5-year, 3-carrier synthetic account produces a loss pack where
every figure traces to a page and no claim is counted twice.
**Blocked by:** persistence and trending decisions (section 4).

### Phase 5 — Submission completeness (3 weeks)
Extend the `.eml` pilot: expected years, valuation age, missing terms, request
list for the broker.
**Done when:** a synthetic submission missing one year and one stale valuation
is reported as incomplete, naming both.

### Phase 6 — Underwriter memo draft (2–3 weeks)
LLM writes the narrative; every number is inserted from the loss pack with its
page link; the model never produces a figure.
**Done when:** a test proves no number in the memo is absent from the loss pack.

Steps 7–8 (exposure, appetite/referral) are not planned until Phase 4 ships
and §13 is revised.

---

Sources checked 2026-11-01: vertafore.com press release (Velocity AI Submission
Processing Agent), insurancebusinessmag.com (Applied acquires Cytora),
iireporter.com, furtherai.com engineering blog, sensible.so loss-run pages,
outmarket.ai, herondata.io.
