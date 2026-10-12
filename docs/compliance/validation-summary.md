> **Draft for counsel/broker review — not legal advice.**

# Validation summary

What has been tested, how, and with what result, as of the commit this file
was last updated on. This is the evidence an insurer's AI program may ask a
vendor for (NAIC Model Bulletin guideline 4.1, and the "validation, testing
and drift" records in its section 4). It says what has **not** been measured
as plainly as what has.

## Headline

- **Synthetic accuracy:** 108 of 108 rows across 14 synthetic carrier formats
  read exactly. Money fields 100%, text fields 100%. That is the floor every
  change must keep.
- **Real-document accuracy: not yet measured.** No real loss run has had its
  extracted values compared with values a person verified off the printed
  page. Until that is done, no accuracy figure for real documents is claimed.
  The tooling to do it exists (`tools/qualification`, labelling helper in
  progress); the adjudicated truth does not yet.
- **Change control on real documents:** every change to extraction or
  reconciliation is compared, document by document, against a private set of
  real loss runs before it merges. The comparison detects *change*, not
  *correctness*.

## 1. Synthetic accuracy ratchet (golden files)

`tests/golden/`: synthetic loss-run PDFs generated from code, each with a
hand-written expected CSV. `python -m tests.golden.baseline` reads every one
through the full pipeline and scores field-level accuracy per carrier
format, separately for money and text fields.

| Measure | Result |
|---|---|
| Rows | 108 / 108 |
| Money fields | 100% |
| Text fields | 100% |
| Carrier formats | 14 |

Formats covered include US and EU number formats, accounting negatives in
parentheses, mainframe trailing minus and `CR` credits, multi-page reports
with repeated headers, workers' compensation medical columns, a ruled table,
obscure headers, mixed currency, a deliberately wrong row (the arithmetic
check must catch it) and a scanned page.

The per-carrier numbers are recorded in `tests/golden/accuracy_baseline.json`.
A change that lowers any carrier's number fails, even if the blended average
stays high.

## 2. Automated test suite

`python -m pytest` runs the unit, regression and end-to-end tests on
synthetic data (see the handoff for tonight's exact counts). Known gaps are
written as **strict expected failures** that turn red the moment a gap is
closed without the test being updated, so a gap cannot be closed silently.

## 3. Silent-failure fuzzing

`tests/fuzz/` generates synthetic loss runs and packets with a known truth,
applies hazard operators (26 at the time of writing: missing and duplicated
pages, restarted numbering, foreign pages, wrong totals and counts, European
and mainframe amounts, blank fields, split claims, and others), and classifies
each result:

- **SAFE**: correct, or not correct and flagged for review;
- **LOUD**: flagged although correct (an over-flag);
- **SILENT**: wrong while reading CLEAN.

**The pass bar is zero SILENT.** A SILENT case is a defect regardless of how
rare it is. Results of the most recent campaign are recorded in the run log
(`fuzz/LOG.md`) and the release handoff.

## 4. Qualification cases

`tests/qualification/` holds 24 synthetic cases with truth fixed by
construction, each with an expected outcome ("supported" or "review"). The
ledger records which are met: **23 of 24**. The unmet case
(`mixed-series-packet`, a digital report bound beside a scanned report) fails
*closed*: it asks for review when it need not. It is never silently wrong.

## 5. Real-corpus change gate

A private set of real loss runs (10 documents in the current release) lives
outside the source repository. Before a change to extraction or
reconciliation merges, both the old and the new code read every document, and
any difference in claim count, status, page accounting, printed evidence,
findings or values fails the gate. A difference can be accepted only by an
explicit, reviewed entry naming the document, the field and both values.

The gate's output carries ids, counts and keyed digests, never document text
(`docs/corpus-gate.md`, `docs/cloud-corpus-gate.md`). It shows that a change
did **not move** results on real documents. It does not show that those
results are **right**.

## 6. Scanned pages

Scanned pages are read by a vision model. Its answers can be recorded once
and replayed exactly, so tests and the change gate never call a live model.
Vision-read values are capped at 0.85 confidence and labelled. A page that was
not read is reported as unread, never as empty. Real scanned documents have
not yet been measured for accuracy.

## 7. What a reviewer should ask us for next

1. The first real-document accuracy report: a set of real loss runs labelled
   by a person, scored field by field, money and text separately.
2. The scanned-path accuracy on the same basis.
3. The rate of documents a pilot customer saw marked NEEDS_REVIEW, and why.
