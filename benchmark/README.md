# Real-document benchmark

Synthetic fixtures prove the engine still does what it did yesterday. They
cannot tell us whether it reads documents nobody designed for it. This
directory holds the measurements from real carrier loss runs, and the registry
of how each one failed.

## The source documents are not here, on purpose

Real loss runs carry claimant names and injury descriptions. Nothing in this
directory is a PDF. What is committed is the manifest describing each document,
the measurements taken from it, and the failure analysis — enough to reproduce
a result and to argue about it, without the repository holding anyone's claim
data (spec section 9).

Keep the corpus wherever you keep it, named `<doc_id>.pdf`, and point the
runner at it:

```
python -m benchmark.run --docs /path/to/corpus --label cold-baseline
```

A document in the manifest but missing from that directory is recorded as
`not_present`. A corpus that quietly shrinks is how a benchmark starts lying.

## Method

The loop, in order, one document at a time:

**Real document → run it cold → preserve the evidence → root cause → classify
→ generalized fix → regression fixture → whole test suite → rerun the entire
corpus.**

Cold means before any adaptation. The point of a real document is what it
reveals about unseen documents, so a fix earns its place by answering: *why
would this help a document nobody has looked at yet, exhibiting the same
mechanism?* A fix that cannot answer that is overfitting to a PDF and does not
go in. No filename checks, no per-document exceptions.

Ground truth is only ever what the document itself prints or what a person has
adjudicated. It is never inferred from what the engine produced. Where a count
has not been adjudicated, `expected_claims` stays empty and the metrics that
depend on it stay empty too.

## False-clean is the defect that matters

**False-clean rate: how often a document is called clean when the evidence says
it is materially wrong or materially incomplete.**

A visible failure is always better than a plausible wrong answer. A reviewer
can act on "this did not reconcile"; nobody can act on a green badge over a
number that is quietly wrong. So automation rate is never improved by relaxing
a check, and abstention beats unsupported certainty.

The first entry in `failures.csv` is exactly this: AIG extracted nothing and
reported CLEAN, because with no rows every other rule was vacuously silent.
R-20 now makes an empty document a hard fail.

`false_clean` in `results.csv` is only filled where it is machine-adjudicable —
today, a count mismatch against a printed count. A document with no adjudicated
ground truth is left blank rather than being recorded as passing.

## Zero claims is not yet a verified zero-loss report

R-20 stops an empty extraction from reading as clean, but it deliberately
cannot yet tell the two apart:

* a genuine loss-free account, which is a good submission and worth stating, and
* a claim table that was never read.

They are identical from inside the engine. Distinguishing them needs
independent evidence — a printed claim count of zero, "no claims were found for
this policy", or equivalent carrier language — and until that exists the rule
states both possibilities and leaves the call to a reviewer. `len(claims) == 0`
is treated as neither success nor failure on its own.

## Failure taxonomy

| Code | Class |
|---|---|
| F01 | Empty extraction |
| F02 | Row over-extraction |
| F03 | Row under-extraction |
| F04 | Semantic column mapping |
| F05 | Header detection |
| F06 | Multi-line claim reconstruction |
| F07 | Footer / total detection |
| F08 | Locale and numeric parsing |
| F09 | OCR / vision extraction |
| F10 | Page and source completeness |
| F11 | Claim deduplication |
| F12 | Policy and coverage segmentation |
| F13 | Carrier identification |
| F14 | Date interpretation |
| F15 | Monetary interpretation |
| F16 | Source and provenance evidence |
| F17 | Incorrect clean / reconciliation state |

Extend this when reality shows a genuinely different class. Do not add a code
to describe one PDF.

## Files

| File | Holds |
|---|---|
| `corpus_manifest.csv` | One row per document: what it is, where it came from, what is known to be true about it |
| `results.csv` | One row per document per run, appended, never rewritten |
| `failures.csv` | Every failure found, its class, its root cause, and the commit and test that closed it |
| `run.py` | The runner |

`results.csv` is append-only. Earlier rows are the record of what the engine
used to do; overwriting them destroys the only evidence that anything improved.
