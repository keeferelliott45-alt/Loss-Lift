# LossLift — Project Spec

Drop this in the repo root as `CLAUDE.md`. Claude Code reads it automatically on every session.

---

**Companion repo:** the marketing/front-end site is separate from this repo, at `keeferelliott45-alt/LostLift-Front-page` (private GitHub repo — note the name is spelled "LostLift", not "LossLift"). React + Vite + TypeScript + Tailwind, built with Bolt.new; includes a Supabase migration for pilot-signup inquiries. Different stack, not part of the Streamlit app below — access it with `add_repo` when needed.

---

## 1. What this is

A web app that ingests insurance **loss run** PDFs and returns clean, **reconciled**, underwriting-ready spreadsheets.

**Customer:** small commercial P&C brokers, wholesale brokers, and MGAs (3–50 staff). Buyer is an account manager or ops lead, not IT.

**The job:** today they re-key claims tables out of carrier PDFs by hand — 30–60 min per document — or pay an offshore VA ~$8/hr to do it. Errors are expensive: a $50k misread on total incurred moves the loss ratio ~2.5 points.

**The product's actual value is not extraction. It is reconciliation.** Anyone can paste a PDF into an LLM. Nobody trusts the output. LossLift ties every extracted number back to the totals printed on the document and flags every mismatch. That verification layer is the product. Build accordingly.

---

## 2. Non-negotiable design principles

Read these before making any architectural decision.

1. **Never let an LLM read a number it doesn't have to.** For text-layer PDFs, numbers come from deterministic parsing (`pdfplumber`). The LLM's job is *structure* — which column is "Paid Indemnity", where the table starts, what the valuation date is. Vision extraction of numbers is a fallback for scanned documents only, and it is always flagged as lower confidence.
   "Extraction into the canonical schema" therefore means **structural mapping** — deciding which printed column carries which canonical field — and never reading the values themselves off the page. A pipeline stage that says "extract" is mapping columns; the money is read by `pdfplumber` either way.
2. **Every number carries provenance.** Page number, bounding box or line index, and extraction method (`digital` | `vision` | `manual`). Without an audit trail an underwriter cannot use this.
   `core/evidence.py` turns that record into the page itself with the row
   marked on it. Two libraries describe a page differently — one measures from
   the media box and one from the crop box, and a rotated page reports its
   words turned and its text unturned — so a rectangle can be arithmetically
   perfect and name the wrong row. **Every region is read back before it is
   shown, and withdrawn if the claim is not inside it**: the page is still
   offered, and the reason is said. Marking the wrong row is worse than marking
   none. A page read by the vision model has no rectangle at all, because the
   model is asked what the page says and not where the words sit; that is
   reported as a page, never approximated into a region. Provenance is answered
   **per field**, so correcting one cell does not cost the other nine the line
   they were read from.
3. **Fail loud, never silently.** A field that can't be parsed is `null` with a reason, never `0`. `0.00` and "no data" are different facts and confusing them causes wrong loss ratios.
4. **The human is the last mile.** Every cell is editable before export. The app assists; it is never the source of truth. This is also the legal posture.
   Three kinds of fact are held, and none may ever wear another's clothes:
   **what the carrier printed**, **what LossLift made of it**, and **what a
   person decided afterwards**. The first two live on the claim — a corrected
   field keeps the extracted value beside it, and provenance is answered per
   field, so one edited cell never costs the other nine theirs. The third lives
   in `core/review.py`, and the rule engine is never told it exists: a
   resolution is recorded beside a finding, never in place of it. Reviewing is
   not reconciling. A reviewer confirming a large loss has not made the
   document tie, and a document must never read healthier than the checks that
   were run against it. Only a changed value moves a reconciliation result, and
   then only because every rule ran again over it.
5. **Learn per carrier.** Each carrier's format, once mapped and human-confirmed, is saved as a reusable profile. This is the compounding moat — the 40th document from Travelers should need zero LLM calls.
6. **Never train on or retain customer data by default.** Files are deleted after export unless the user opts in.

---

## 3. Canonical data model

Every carrier format normalizes into this. Use Pydantic v2 models in `core/schema.py`.

### Document level
| Field | Type | Notes |
|---|---|---|
| `document_id` | str | uuid |
| `source_filename` | str | |
| `file_sha256` | str | dedupe key |
| `carrier` | str \| None | |
| `named_insured` | str \| None | |
| `policy_number` | str \| None | |
| `policy_period_start` | date \| None | |
| `policy_period_end` | date \| None | |
| `line_of_business` | str \| None | WC / GL / AUTO / PROP / UMB / OTHER |
| `valuation_date` | date \| None | **ERROR if missing** — a loss run without one is unusable |
| `currency` | str | ISO 4217, default USD |
| `locale_hint` | str | `us` or `eu` — drives number parsing |
| `page_count` | int | |
| `extraction_method` | enum | `digital` \| `vision` \| `mixed` |
| `printed_totals` | dict | totals scraped from the document footer, per column |
| `printed_claim_count` | int \| None | |

Every document-level field above is also written onto **every row** of the
Claim Detail export — carrier, policy number, policy term, line of business,
insured and valuation date. One row then describes itself without its header,
which is what makes a merged multi-carrier sheet usable. They are stored once
on the document and denormalised at export, not duplicated in memory.

### Claim level
| Field | Type | Notes |
|---|---|---|
| `claim_number` | str | required |
| `date_of_loss` | date | required |
| `date_reported` | date \| None | |
| `claim_status` | enum | `OPEN` \| `CLOSED` \| `CLOSED_PAID` \| `REOPENED` \| `REPORT_ONLY` \| `UNKNOWN` |
| `claimant_name` | str \| None | personal data — see §9 |
| `loss_description` | str \| None | |
| `cause_of_loss` | str \| None | |
| `paid_indemnity` | Decimal \| None | |
| `paid_medical` | Decimal \| None | WC only |
| `paid_expense` | Decimal \| None | ALAE |
| `paid_total` | Decimal \| None | |
| `reserve_indemnity` | Decimal \| None | |
| `reserve_medical` | Decimal \| None | |
| `reserve_expense` | Decimal \| None | |
| `reserve_total` | Decimal \| None | |
| `recovery_total` | Decimal \| None | subro / salvage / deductible reimbursement |
| `incurred_total` | Decimal \| None | |
| `litigation_flag` | bool \| None | |
| `claimant_ref` | str \| None | carrier's own claimant id, when printed |
| `close_date` | date \| None | |
| `loss_state` | str \| None | two-letter state of loss |
| `deductible_basis` | enum | `gross` \| `net` \| `unknown` — **never inferred** |
| `alae_treatment` | enum | `included` \| `separate` \| `unknown` — **never inferred** |
| `source_page` | int | provenance |
| `source_row` | int \| None | provenance |
| `source_method` | enum | `digital` \| `vision` \| `manual` |
| `confidence` | float | 0–1, lowest of the row's field confidences |
| `field_confidence` | dict[str, float] | 0–1 per field |

`paid_expense` / `reserve_expense` are ALAE. "Expense", "ALAE", "LAE" and
"defense cost" all normalise into them; they are labelled **Paid ALAE** and
**Reserve ALAE** in the export. There is deliberately one field per concept,
not one per carrier's word for it.

`deductible_basis` and `alae_treatment` are the two fields a carrier usually
does not state. They default to `unknown`, are never guessed from context, and
every `unknown` is surfaced on the Exceptions tab — a loss run read as gross
when it is net is a wrong answer that looks right.

**Workers' comp extension.** Present only on WC documents; null elsewhere.

| Field | Type | Notes |
|---|---|---|
| `body_part` | str \| None | |
| `nature_of_injury` | str \| None | |
| `ncci_class_code` | str \| None | |
| `medical_only_flag` | bool \| None | true when the claim has medical and no indemnity |

**Use `Decimal`, never `float`.** Money in floats will produce reconciliation failures that aren't real.

---

## 4. Number parsing — get this right or nothing works

Put this in `core/normalize.py` and unit-test it exhaustively. It is the single highest-bug-density area.

Formats that appear in real loss runs:

```
1,234.56      US thousands + decimal
1.234,56      EU thousands + decimal
1 234,56      French / non-breaking space
(1,234.56)    negative, accounting style
1,234.56-     trailing minus, mainframe reports
-1,234.56     leading minus
$1,234        currency prefix
1.234 €       currency suffix
-0-           zero, mainframe convention
--            zero or null depending on carrier
N/A, NA, n/a  NULL — not zero
(blank)       NULL — not zero
1,234.56 CR   credit, i.e. negative
```

**Separator disambiguation algorithm:**
1. If both `,` and `.` present → the **last** separator encountered is the decimal separator.
2. If only one separator present and exactly 3 digits follow it → ambiguous. Resolve using `locale_hint`.
3. Derive `locale_hint` at document level: scan all numeric tokens; if any token unambiguously uses one convention (e.g. `1,234.56`), apply that convention to the whole document. Default `us`.
4. If still ambiguous after document-level inference → return `null` with reason `AMBIGUOUS_SEPARATOR` and flag for human review. Never guess.

**Date parsing:** same approach. `03/04/2024` is ambiguous. Infer document-level date order by finding any date in the document where the first component is >12. If none exists, use `locale_hint`. Flag if unresolved.

---

## 5. Extraction pipeline

Seven stages. Each is a separate module and separately testable.

**Stage 0 — Ingest** (`core/ingest.py`)
Hash the file, check for a prior extraction of the same hash, store bytes in temp storage.

**Stage 1 — Classify** (`core/classify.py`)
Per page, count extractable characters via PyMuPDF. `< 50 chars/page` → scanned. Documents can be mixed; classify per page, not per document.

**Stage 2a — Digital extraction** (`core/extract_digital.py`)
`pdfplumber.extract_tables()` first. Where table detection fails (very common on loss runs, which are often positioned text rather than ruled tables), fall back to word-position clustering: extract words with x/y coordinates, cluster by y to form rows, cluster by x to form columns, using the header row's x-positions as column boundaries.

Not every carrier gives a claim one line. Some spend three: the claimant on
one, the identifier and status on the next, the dates and money on a third,
under a header block with one line of labels per record line. `core/records.py`
reconstructs those, and the rule it works to is that **incorrect association is
worse than incomplete association**. The header block proposes a shape — how
many lines, and which of them names the claim — and the body confirms it: every
claim number found anchors one record, which is the fixed span of lines around
that anchor and nothing else. A span is taken only when it holds exactly one
identifier, contains no line that ends a record, sits closer together than
records sit apart, and overlaps no span already taken. An anchor failing any of
those keeps its lines to itself, and the claim keeps its null fields.

Nothing outside a span is ever read into a claim, so a section total printed
hard against the last claim cannot be absorbed by it — not because the total is
recognised, but because the record's extent was settled before the total was
reached. A merged row records every printed line it came from.

**Stage 2b — Vision extraction** (`core/extract_vision.py`)
Render pages at 300 DPI with PyMuPDF, send to Gemini with the JSON schema. Only for scanned pages. Mark every field `source_method="vision"` and cap confidence at 0.85 regardless of model output.

A loss run need not print its document-level facts in a letterhead. A
spreadsheet export repeats the company, the insured, the policy and its term as
**columns on every claim** — the same facts the export denormalises back onto
every row (section 3). Those columns are read once onto the document, and only
where the rows agree: a policy column saying three different things describes
three policies, and each term it names is kept rather than one of them chosen.

**Stage 3 — Column mapping** (`core/profiles.py`)
1. Fingerprint the document: normalized text of the top 15% of page 1 + any carrier-name regex hits. Hash it.
2. If a saved profile matches the fingerprint → apply it. **Zero LLM calls.**
3. If no match → send only the header row(s) and 3 sample data rows to the LLM, asking it to map source column labels to canonical field names. Never send the whole table for mapping.
4. After the human confirms or corrects the mapping in the UI, save the profile to `data/profiles/{fingerprint}.json`.

A profile stores: column-label → canonical-field map, date format, number locale, negative convention, header row index, footer-total row detection pattern, carrier name.

Every measurement of the page is stated in the page's own units, never in
points. A wide carrier sheet is routinely fitted onto A4 before it is sent, and
everything comes down with it: two-point glyphs, gutters under a point, lines
two points apart. Nothing about the table has changed, so nothing about the
reading may. A threshold in absolute points is several characters wide on such
a page and swallows every column on it — including the tolerance the word
extractor itself uses, which at its default joins one column to the next and
one line to the line below.

Finding the columns is two questions, not one, and they have different answers.
**Where** the columns are comes from the rows: a gutter is space they leave
blank. **What** each one is comes from the header block, every line of it, with
a label following its own words rather than the nearest column edge — money
prints right-aligned under a left-aligned label and need not overlap its own
column at all. Where the two disagree the reading that names more distinct
fields wins, and a boundary falling inside a run of text the rows never break
loses.

One overlong cell must not close a boundary the rest of the page keeps. Where
the header names a column twice and most of that column's rows leave a channel
wider than the space they set between their own words, the column is cut there.
Both sources have to agree: a two-word heading set wide over running prose is
one column, and cutting it would halve every description.

**Stage 4 — Normalize** (`core/normalize.py`)
Numbers, dates, and status vocabulary (`O`/`OP`/`Open`/`OPEN` → `OPEN`; `C`/`CL`/`Closed` → `CLOSED`).

**Stage 5 — Reconcile** (`core/reconcile.py`) — see §6.

**Stage 6 — Review** — Streamlit editable dataframe, exceptions surfaced first.

**Stage 7 — Export** (`core/export.py`) — one `.xlsx`:

1. **Claim Detail** — one row per claim, the full canonical schema, with the
   document-level fields denormalised onto every row.
2. **Loss Summary** — by policy term: claim count, open count, paid, reserve,
   incurred, frequency and severity, each term checked against the subtotal the
   carrier printed for it.
3. **Large Loss** — claims at or above a configurable threshold, default
   $25,000.
4. **Exceptions** — every hard fail, soft flag and `unknown`, with its page.
5. **Source Info** — filename, hash, valuation date, extraction method,
   timestamp, reconciliation status. A fifth sheet beyond the four specified,
   kept because dropping it would leave the workbook unauditable back to the
   file it came from.

Every row on sheets 1–3 carries `source_page`, so any figure on any sheet can
be traced to the page it was read from.

---

## 6. Reconciliation engine — the moat

Rule engine in `core/reconcile.py`. Each rule returns zero or more `Finding(rule_id, severity, claim_number|None, field|None, message, expected, actual, delta)`.

Severity: `ERROR` (blocks clean-export badge), `WARN` (amber, exportable), `INFO`.

Default money tolerance: `Decimal("0.01")`. Make it configurable per profile — some carriers round to whole units.

| ID | Rule | Severity |
|---|---|---|
| R-01 | `paid_total + reserve_total - recovery_total == incurred_total` per row | ERROR |
| | Recoveries stay in the identity: a carrier printing a $138.26 third-party recovery makes `paid + reserve == incurred` wrong by exactly that. | |
| | Where a carrier prints the components of paid or reserve and no total of them, **all** of that group must be on the row before their sum stands in for it. Half a group is not a total: the rest may be in a column that was never mapped, and adding up what happens to be present reports that column as the carrier's arithmetic error. | |
| R-02 | `paid_indemnity + paid_medical + paid_expense == paid_total` where components exist | ERROR |
| R-03 | `reserve_indemnity + reserve_medical + reserve_expense == reserve_total` | ERROR |
| R-04 | Column sum equals the printed footer total, per money column | ERROR |
| | The total has to be a total *of these claims*. Board packets bind several loss runs together, and a totals row naming a claim count that is not the count extracted belongs to one of the others — checking against it reports a discrepancy nobody made, on the one rule that verifies against something the carrier printed. | |
| R-05 | Extracted row count equals printed claim count | ERROR |
| R-06 | `valuation_date` present | ERROR |
| R-07 | `claim_number` / `date_of_loss` / `incurred_total` non-null | ERROR |
| R-08 | Closed claim with non-zero reserve | WARN |
| R-09 | `date_of_loss` outside policy period | WARN |
| R-10 | Date ordering `date_of_loss <= date_reported <= valuation_date` | WARN |
| R-11 | Duplicate `claim_number` within one carrier + policy | ERROR |
| R-12 | Same claim appearing on two pages (cross-page continuation artifact) | WARN |
| R-13 | Value exceeds 100× the column median | WARN |
| R-14 | Negative `paid_total` | INFO (legitimate recovery) |
| R-15 | Any field with `AMBIGUOUS_SEPARATOR` or null-with-reason | WARN |
| R-16 | Mixed currency symbols within one document | WARN |
| R-17 | Claim with zero paid **and** zero reserve | WARN |
| R-18 | `deductible_basis` or `alae_treatment` is `unknown` | WARN |
| R-19 | Rows recovered after page stitching ≠ rows seen per page | WARN |
| R-20 | No claims extracted at all | ERROR |
| | A document that produced nothing must never read as reconciled. The account may truly be loss-free, or the table may not have been read; the rule states both and leaves the call to a reviewer. | |

**Hard fails (ERROR) block a clean export; soft flags (WARN/INFO) never do.**
The date rules R-09 and R-10 are deliberately soft: real loss runs report a
claim before its own date of loss, and list claims outside the term printed at
the top, often enough that hard-failing them would stop documents that are
simply repeating what the carrier printed. They are shown, not enforced.

**R-04 and R-05 are the ones that sell the product.** They are the only rules that verify against something the carrier printed rather than something the app computed. Prioritise footer-total detection accordingly — it is worth real effort.

Document-level status: `CLEAN` (no ERROR), `NEEDS_REVIEW` (any ERROR), shown as a single green/amber badge. That badge is the emotional core of the UI.

---

## 7. Repo structure

```
losslift/
  app.py                  # Streamlit entry, routing only — no logic
  core/
    schema.py             # Pydantic models
    ingest.py
    classify.py
    extract_digital.py
    extract_vision.py
    profiles.py           # carrier format library
    normalize.py          # numbers, dates, status vocab
    reconcile.py          # rule engine
    export.py
  prompts/
    map_columns.md
    extract_vision.md
  data/
    profiles/             # saved carrier profiles, gitignored
  tests/
    golden/               # synthetic PDF + expected CSV pairs
    test_normalize.py
    test_reconcile.py
    test_profiles.py
    test_end_to_end.py
  requirements.txt
  .env.example
  CLAUDE.md
```

`app.py` contains no business logic. Every stage must be callable and testable without Streamlit.

---

## 8. Stack

Free tiers only. No paid infrastructure until there are paying customers.

- Python 3.11+
- `streamlit` — UI, deploy to Streamlit Community Cloud (free)
- `pymupdf` — page rendering, text detection
- `pdfplumber` — table and word-position extraction
- `pydantic>=2` — schema validation
- `pandas`, `openpyxl` — export
- `google-genai` — Gemini, free tier
- `python-dotenv`
- `pytest`

Deliberately deferred until there are paying users: Supabase (auth/DB/storage), Stripe, background job queue, any framework beyond Streamlit.

Secrets via `.env` locally and Streamlit secrets in deployment. Never commit keys.

---

## 9. Data handling

Loss runs contain claimant names and injury descriptions — personal data under GDPR, and the developer is a processor.

- Process in memory; delete uploaded files immediately after export. No retention by default.
- `data/profiles/` stores **structure only** — column labels, formats, carrier names. Never claim data. Enforce this in code with an explicit whitelist of what a profile may contain.
- Never commit real customer documents. `tests/golden/` uses **synthetic** loss runs only. Generate them.
- Add a redaction toggle that drops `claimant_name` and `loss_description` from export.
- `.gitignore`: `.env`, `data/profiles/`, `*.pdf`, `uploads/`.

---

## 10. Test strategy

This is an accuracy product. Testing is not optional, and Claude Code should build the harness in Milestone 1, not last.

**Golden-file harness:** each fixture is a synthetic loss run PDF plus a hand-written expected CSV. Runner loads the PDF through the full pipeline and compares field-by-field.

**Metric:** field-level accuracy = correct non-null fields / total expected non-null fields. Report separately for money fields and text fields — money accuracy is the number that matters, and **per carrier format**, since one carrier regressing is invisible in a blended average.

Each fixture is a synthetic source PDF plus hand-verified expected output. The
full set runs before any deploy and a regression fails the build. Fixtures are
**never** real customer documents: when a real loss run exposes a bug, its
*structure* is reproduced synthetically and that is what gets committed (spec
section 9). A verified mapping is cached as a carrier profile — structure only,
never the file.

**Per-carrier accuracy and the ratchet.** `python -m tests.golden.baseline`
prints accuracy per carrier; `--update` records it to
`tests/golden/accuracy_baseline.json`, which is committed. The thresholds below
are the floor; the baseline is the ratchet. A change that drops any carrier
below its recorded accuracy fails the build even when it stays above 99.5%,
because extraction that used to read a carrier perfectly and now does not is a
regression whatever the absolute number says. An improvement never fails —
refresh the baseline so the better number is the one being defended. A new
carrier format has to be recorded before it can be defended, so adding a
fixture means refreshing the file.

**Thresholds before charging anyone:**
- Money fields: ≥ 99.5% on digital PDFs
- Money fields: ≥ 97% on scanned PDFs
- R-04 footer-total tie: must pass on 100% of clean fixtures
- Zero silent nulls-as-zeros

Build at least 8 synthetic fixtures spanning: US format, EU format, scanned, multi-page with repeated headers, WC with medical columns, accounting-negative parentheses, mainframe trailing-minus, and a document with a deliberate arithmetic error in it (R-01 must catch it).

---

## 11. UI spec

Four screens. Streamlit, minimal, no decoration.

**Upload** — drag-and-drop, multi-file, list with per-file status.

**Mapping** (only when no saved profile matches) — two-column view: detected source headers on the left, canonical field dropdowns on the right, three sample rows shown below for context. One button: "Save mapping and continue". Saving creates the profile.

**Review** — the main screen.
- Top: document badge — green "Reconciled" or amber "Needs review", plus valuation date, claim count, total incurred.
- Exceptions panel directly beneath, collapsed if empty, listing every Finding with rule, claim number, expected vs actual, and delta. Clicking a finding scrolls to that row.
- Editable claims table below. Cells with findings highlighted amber. Vision-extracted cells subtly marked.
- Edits re-run reconciliation live.

**Export** — column-order template selector, redaction toggle, "Download Excel".

Copy rules: active voice, sentence case, name things by what the user controls. Errors say what happened and how to fix it. Empty states invite action.

---

## 12. Build milestones

Build in this order. Each milestone must be independently runnable and tested before moving on. Do not build ahead.

**M1 — Schema, normalize, tests.** Pydantic models, number/date parsers, golden-file harness scaffolding, full unit tests on `normalize.py`. No UI, no PDFs. Done when every parsing case in §4 passes.

**M2 — Reconciliation engine.** All 16 rules against hand-constructed claim lists in tests. No PDFs yet. Done when a deliberately-wrong fixture produces exactly the expected findings.

**M3 — Digital extraction.** pdfplumber tables + word-position fallback, on synthetic fixtures. Done when money-field accuracy ≥ 99.5% on digital fixtures.

**M4 — Profiles + LLM column mapping.** Fingerprinting, profile save/load, LLM mapping for unknown formats. Done when a second document from the same carrier requires zero LLM calls.

**M5 — Streamlit UI.** All four screens, live re-reconciliation on edit, Excel export.

**M6 — Vision fallback.** Scanned-page rendering and Gemini extraction, confidence capping. Done last because most loss runs are digital and this is the lowest-value, highest-cost path.

---

## 13. Explicitly out of scope

Do not build these. If they seem necessary, stop and ask.

- User accounts, auth, teams, roles
- Database persistence of claims (session-only until paying users exist)
- Stripe / billing
- AMS or CRM integrations
- Automated loss-run retrieval from carriers
- ACORD form auto-fill
- Analytics dashboards, charts, trending
- Non-English documents
- Mobile layouts
- Background job queues, async workers
- Any LLM call that reads numbers off a digital PDF

---

## 14. Kickoff prompt

Paste this into Claude Code after saving the spec:

> Read CLAUDE.md in full before writing any code. Build Milestone 1 only: `core/schema.py`, `core/normalize.py`, and the test harness. Write the tests first, covering every number and date format listed in §4 including the ambiguous cases. Do not create the Streamlit app, the extraction modules, or anything from later milestones. When M1 passes, stop and report accuracy results before continuing.

---

## 15. Before you write code

The spec is worthless if the premise is wrong. Two things first:

1. **Get 10 real loss runs.** Ask your Marsh / Zurich / Munich Re contacts for anonymised or expired samples. Without real documents you'll build for imaginary formats and every assumption in §5 will be wrong.
2. **Run the discovery calls.** Eight to twelve conversations asking only "walk me through how you handle loss runs today". If fewer than four describe it as a real recurring pain, the build doesn't start.

Kill criteria: no pilot commitments by Week 6, or fewer than three paying customers by Week 10 — stop.
