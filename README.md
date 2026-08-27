# LossLift

Turns insurance loss run PDFs into clean, reconciled spreadsheets.

Small commercial brokers and MGAs re-key claims tables out of carrier PDFs by hand — 30–60 minutes per document — or pay an offshore VA to do it. Errors are expensive: a $50k misread on total incurred moves the loss ratio ~2.5 points.

**The value is not extraction. It is reconciliation.** Anyone can paste a PDF into an LLM; nobody trusts the output. LossLift ties every extracted number back to the totals printed on the document and flags every mismatch.

## Status

All six milestones built and tested. 400 tests pass.

Not yet validated against real carrier documents — see [Before this ships](#before-this-ships).

## Quick start

Python 3.11 or newer.

```bash
git clone https://github.com/keeferelliott45-alt/Loss-Lift.git
cd Loss-Lift
pip install -r requirements.txt
streamlit run app.py
```

That opens the app at http://localhost:8501.

No API key is needed for digital PDFs, which is most loss runs. A Gemini key
is used only for two things: mapping column headers the built-in vocabulary
does not recognise, and reading scanned pages. Copy `.env.example` to `.env`
to configure it.

## Try it

A fresh clone has no PDFs to open — fixture documents are generated rather
than committed, so no loss run ever lands in this repository. Make yourself a
set of realistic ones:

```bash
python -m tests.golden.generate --pdf-dir samples
```

That writes eleven synthetic loss runs to `samples/` (git-ignored). Drag any
of them onto the upload screen. Each one is built to exercise something
different:

| File | What it shows |
|---|---|
| `us_basic.pdf` | The ordinary case. Should come back green. |
| `arithmetic_error.pdf` | One row's incurred is overstated by 10,000. R-01 catches it, the badge goes amber, and editing the cell turns it green. |
| `eu_format.pdf` | `1.234,56` and `dd/mm/yyyy`. Same claims as `us_basic`, same answers. |
| `mainframe_trailing_minus.pdf` | `3,500.00-` negatives and `-0-` zeros. |
| `accounting_negatives.pdf` | `(1,234.56)` negatives, and a claim where subrogation recovered more than was paid. |
| `wc_medical.pdf` | Workers comp, fourteen columns including medical. |
| `multipage_repeat_header.pdf` | Three pages with the header repeated on each. |
| `nulls_not_zeros.pdf` | `N/A`, a blank cell and a true `-0-` in the same column. They must not come back the same. |
| `unknown_format.pdf` | Headers the vocabulary cannot place, so the mapping screen appears. Map them once and re-upload: it maps itself. |
| `ruled_table.pdf` | A ruled grid, which takes the other extraction path. |
| `scanned.pdf` | No text layer. Needs a Gemini key, or it reports the pages it skipped. |

A quick tour: upload `arithmetic_error.pdf`, look at the amber badge and the
exception underneath it, change that row's total incurred from `41400.00` to
`31400.00`, and watch the badge turn green as the checks re-run. Then export
and open the Exceptions and Source Info sheets.

## How it works

Seven stages, each a separate module, each testable without Streamlit.

| Stage | Module | What it does |
|---|---|---|
| 0 Ingest | `core/ingest.py` | Hash, validate, stage to temp storage |
| 1 Classify | `core/classify.py` | Per page: digital or scanned (<50 extractable chars) |
| 2a Digital | `core/extract_digital.py` | pdfplumber tables, then word-position clustering |
| 2b Vision | `core/extract_vision.py` | Scanned pages only, 300 DPI, confidence capped at 0.85 |
| 3 Map | `core/profiles.py` | Column labels → canonical fields; saved per carrier |
| 4 Normalise | `core/normalize.py` | Numbers, dates, status vocabulary |
| 5 Reconcile | `core/reconcile.py` | The 16 rules |
| 6 Review | `app.py` + `core/pipeline.py` | Editable table, checks re-run on every edit |
| 7 Export | `core/export.py` | Claims, Exceptions, Source Info |

### Three decisions worth knowing about

**Numbers are never read by an LLM on a digital PDF.** `1,234.56` and
`1.234,56` are the same amount written two ways, and getting that wrong is
how a loss ratio silently moves. The parser derives the document's convention
from its own numbers, and where a token is genuinely ambiguous — `1.234` with
no evidence either way — it returns null with `AMBIGUOUS_SEPARATOR` and asks a
human, rather than guessing.

**A null is never a zero.** `0.00` and "no data" are different facts. Every
unparseable field carries a reason code, and the golden-file harness counts
nulls-silently-read-as-zero as a separate, must-be-zero metric.

**Column mappings are learned per carrier.** The fingerprint hashes the
carrier name, the column labels, and which header-block labels the template
prints — never their values. A second document from the same carrier
therefore matches even though the insured, policy number and valuation date
all differ, and costs zero LLM calls.

## Reconciliation

Sixteen rules (`core/reconcile.py`). R-04 and R-05 are the ones that matter:
they are the only rules that check the extraction against something the
*carrier* printed rather than something this app computed.

- **R-04** every money column sums to the printed footer total
- **R-05** the row count equals the printed claim count

A document with no `ERROR` finding shows a green **Reconciled** badge; anything
else is amber **Needs review**. Warnings never block an export.

## Accuracy

```bash
python -m tests.golden.report
```

```
fixture                                money             other      rows
------------------------------------------------------------------------
us_basic                     100.00% (18/18)   100.00% (36/36)       6/6
eu_format                    100.00% (18/18)   100.00% (36/36)       6/6
wc_medical                   100.00% (46/46)   100.00% (30/30)       6/6
accounting_negatives         100.00% (17/17)   100.00% (36/36)       6/6
mainframe_trailing_minus     100.00% (42/42)   100.00% (24/24)       6/6
multipage_repeat_header      100.00% (40/40)   100.00% (84/84)     14/14
ruled_table                  100.00% (18/18)   100.00% (36/36)       6/6
arithmetic_error             100.00% (11/11)   100.00% (24/24)       4/4
nulls_not_zeros              100.00% (11/11)   100.00% (24/24)       4/4
------------------------------------------------------------------------
ALL                          100.00% (221/221) 100.00% (330/330)   58/58

nulls silently read as zero: 0
money threshold (99.5%): PASS
```

**Read this number with care.** It is measured against synthetic fixtures this
repository generates. They span the formats the spec calls for — US and EU
separators, parentheses and trailing-minus negatives, scanned pages, repeated
headers across pages, WC medical columns, ruled and unruled tables, nulls
versus zeros, and a planted arithmetic error — but they are not real carrier
documents, and 100% here does not predict 100% on a Travelers PDF. The harness
exists so that the first ten real loss runs can be added as fixtures and the
number can be trusted.

## Testing

```bash
pytest                              # 400 tests
python -m tests.golden.report       # accuracy table
```

Fixture PDFs are generated at test time and never committed — `.gitignore`
excludes `*.pdf` and the spec forbids shipping loss-run documents. The expected
CSVs under `tests/golden/expected/` are committed so a reviewer can read what
the pipeline is supposed to produce. Regenerate them deliberately:

```bash
python -m tests.golden.generate --write-expected
```

## Data handling

Loss runs contain claimant names and injury descriptions — personal data, and
this app is a processor.

- Uploads are held in a temp directory and deleted on request after export.
- `data/profiles/` stores **format structure only**. An explicit whitelist
  refuses to write anything else, and raises rather than dropping it quietly,
  so a leak fails the write instead of hiding.
- Claims are never persisted; they live in the Streamlit session.
- The export has a redaction toggle that drops claimant names and loss
  descriptions.
- Test fixtures are synthetic. No real customer document is in this repo.

## Before this ships

The spec's own section 15 is the binding constraint, and it is not met yet:

1. **Get 10 real loss runs.** Every assumption in the extraction pipeline was
   validated against documents this repo generated. Real carrier PDFs will
   break some of them; that is what the golden-file harness is for.
2. **Run the discovery calls.** Eight to twelve conversations asking only
   "walk me through how you handle loss runs today". If fewer than four
   describe it as a real recurring pain, the build does not start.

Kill criteria: no pilot commitments by week 6, or fewer than three paying
customers by week 10.

## Stack

Python 3.11+ · Streamlit · pdfplumber · PyMuPDF · Pydantic v2 · openpyxl ·
Gemini (column mapping and scanned pages only). Free tiers throughout.

## Spec

`CLAUDE.md` is the source of truth for the data model, the pipeline and the
reconciliation rules.
