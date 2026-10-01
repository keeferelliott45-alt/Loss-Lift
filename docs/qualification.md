# Qualification: absolute accuracy against verified truth

The golden suite checks 108 synthetic rows, and the corpus gate checks that
nothing *changed*. Neither says how *right* LossLift is on a real document.
Qualification does: it scores the current checkout's extraction against
truth a person verified from the printed page.

The existing golden rows and the unlabeled private corpus cannot establish
99.9% accuracy. Until adjudicated truth exists for real documents, a
qualification report says what was measured and, where it applies,
`insufficient`.

## Commands

```
python -m tools.qualification validate --manifest M --truth T [--corpus C]
python -m tools.qualification run --corpus C --manifest M --truth T --out O \
    [--vision-replay R]
```

- **validate** checks the truth file against the manifest: ids and byte
  hashes, plus the corpus bytes when `--corpus` is given. Exit codes:
  - 0: valid;
  - 1: a file changed or is missing;
  - 2: unusable truth or setup.
- **run** scores the checkout it is run from. It writes
  `O/qualification.json` and `O/qualification.txt`. Exit codes:
  - 0: every document was scored against adjudicated truth;
  - 1: insufficient evidence (a document not scored, a claim page unread, or
    provisional truth);
  - 2: unusable truth or setup.

The corpus, manifest, truth, recordings and output must all live outside the
repository, as for the [corpus gate](corpus-gate.md). The manifest is the
gate's own: `python -m tools.corpus_gate init-manifest` writes one.

## What the runner guarantees

- **Committed code only.** It refuses a checkout with uncommitted changes and
  records the commit it scored.
- **Verified bytes.** Each document is copied into a private snapshot, hashed
  as it is copied, and read from there. A file whose bytes differ from the
  manifest, and so from the truth written for it, is reported
  `bytes_changed`, not scored.
- **No live model.**
  - Credentials are hidden for the run, and column mapping by model is off.
  - Scanned pages are read only from `--vision-replay` recordings, in the
    `core.extract_vision` replay format.
  - Without a recording a page stays unread and the report says so.
- **Isolation.** Profiles and temporary files go to a private directory that
  is removed afterwards.

## Truth, version 1

Truth is written by hand, from the page, never from LossLift's output. It
lives outside the repository.

```json
{"version": 1, "documents": [{
  "id": "doc-…", "sha256": "…", "format_family": "digital",
  "adjudication": "adjudicated", "page_count": 2,
  "pages": [{"page": 1, "run": "r1", "role": "claims"},
            {"page": 2, "run": null, "role": "claim_free"}],
  "runs": [{"id": "r1", "pages": [1], "status": "CLEAN",
            "printed": {"claim_count": "3", "totals": {"incurred_total": "32000.00"}}}],
  "status": "CLEAN",
  "printed": {"claim_count": {"state": "absent"}},
  "claims": [{"claim_number": "…", "anchor": {"page": 1},
              "fields": {"date_of_loss": "2022-01-10", "claim_status": "OPEN",
                         "paid_total": "30000.00", "reserve_total": "0.00",
                         "recovery_total": {"state": "absent"},
                         "incurred_total": "30000.00"}}]}]}
```

**Labels.** Each label is in one of four states, and none stands in for
another:
- `known` (a bare string is shorthand for a known value);
- `absent`: blank on the page, which is not zero;
- `ambiguous`: readable more than one way;
- `unscorable`: could not be established.

Money is a decimal *string*: JSON numbers are refused, and so is `null`.

**Complete or not qualification.**
- Every page is placed: in a run, or outside all runs with a role
  (`claims`, `claim_free`, `not_loss_run`).
- Every claim occurrence labels every critical field: `date_of_loss`,
  `claim_status`, `paid_total`, `reserve_total`, `recovery_total` and
  `incurred_total`.
- The document and every run state the canonical status a correct reading
  would have.
- The document's, each run's and each section's printed count and totals are
  labelled where they exist.
- Only `adjudicated` truth counts as qualification; `provisional` truth is
  scored and reported as insufficient.

**Occurrences.** Each printed occurrence of a claim is its own entry, anchored
to its page.
- Give a `row` only to tell apart occurrences that share an identifier and a
  page. It is LossLift's printed-line index for the row carrying the claim
  number, the "row" its evidence view names.
- An identifier that cannot be read is `"identity": "ambiguous"`, with no
  number.

**Errors.** A validation error names positions and field names only, never a
value, claim number or file name.

## Labelling: writing truth from the page

Truth is written from the printed page, never from LossLift's output. The
helper in `tools/qualification/label.py` never imports the pipeline; a test
enforces that.

```
python -m tools.qualification skeleton --manifest M --corpus C --out T
python -m tools.qualification review --manifest M --corpus C --truth T --out DIR
python -m tools.qualification sign-off --truth T --document ID [--note TEXT]
```

1. **skeleton** writes a blank entry per manifest document. It holds only
   what the bytes say: id, hash, page count, one entry per page. Every
   judgement (format family, page roles, statuses, printed counts) is
   `"TODO"`, which `validate` refuses. It never overwrites a truth file.
2. Labels are filled in from the page. Until a person has checked them the
   document stays `provisional`.
3. **review** writes one self-contained HTML sheet per document. Each page
   image sits beside the labels anchored to it, with the printed facts, the
   completeness check and a list of what is still `TODO`. It reads the truth
   leniently, so a half-written file can be reviewed.
4. **sign-off** marks one document `adjudicated` after the person who checked
   the sheet confirms it. It refuses incomplete truth, and appends the time,
   id and hash to `T.signoff.log` beside the truth file.

Every output holds real document content (the sheets hold page images). Like
the corpus and truth, it must live outside the repository, and the commands
refuse a path inside it. Sheets are delivered to the reviewer privately,
never published.

## How a document is scored

**Matching** is one to one, by printed identifier and page anchor, never by
money:
- A claim read on the wrong page is *misplaced*: missed there, and invented
  where it was read.
- A second read of one occurrence is an invented *duplicate*. The copy with
  the most field errors is the one scored.
- Occurrences no anchor can tell apart are *ambiguous*: shown and never
  scored.

**Claims.**
- precision = matched / (matched + invented);
- recall = matched / (matched + missing).

A tied claim count proves nothing: a claim replaced by another is one
missing and one invented.

**Fields**, reported for money fields, critical fields (money included) and
optionally labelled other fields:
- a wrong value costs both precision and recall;
- a printed value not read costs recall;
- a value read where nothing is printed costs precision.

A zero filled into a blank is also counted as `null_as_zero`, and a printed
zero read as nothing as `zero_as_null`.

**Runs and pages.**
- Truth runs are matched to extracted runs by identical page sets. Pages the
  truth places outside every run are ignored on the extracted side.
- A single report with `runs: []` measures like one run.
- A matched claim read into the wrong run is counted as `wrong_run`.
- Claim pages the reader failed, skipped or left unresolved are counted as
  unread.

**Printed facts.** Document, run and section counts and totals are compared
label by label.

**Status and acceptance.**
- *Auto-accepted* is what a user would take without review: a canonical
  (`core.review`) document or run status of CLEAN with no column mapping
  pending.
- Every claim metric is reported for all claims and again for the
  auto-accepted subset.
- *False CLEAN* is an auto-accepted document or run that the truth says
  needs review, or that holds a claim, critical-field or run error.

**Rates.** A rate with no denominator is `null`, never 100%, and its counts
sit beside it. Each block also reports its scored coverage.

`must_be_zero` collects `false_clean_documents`, `false_clean_runs` and
`null_as_zero`.

## The public report

The report contains:
- counts, rates and fixed categories (`scored`, `missing_file`,
  `bytes_changed`, `pipeline_failed`);
- the commit and the manifest and truth file hashes;
- document ids sealed under the manifest's salt;
- for a pipeline failure, only the exception's type name.

Before the report is written, it is searched for every private string the run
touched:
- paths and file names;
- document ids and hashes;
- claim numbers, dates and amounts.

One hit stops the write.

## Synthetic cases (the frozen interface)

A pack is a module exporting:

```python
def build_cases(root: Path) -> list[QualificationCase]
```

`tools.qualification.cases.QualificationCase` holds:
- `case_id`;
- the generated `pdf`;
- its `truth` (use `document_truth(case_id, pdf, spec)`, which takes the id
  and the hash of the bytes just written);
- an `expectation`;
- optionally, a `replay` directory or an offline `extractor`.

**Expectations.**
- `SUPPORTED`: read exactly, meaning every claim, critical field, run and the
  stated status.
- `REVIEW`: the truth is NEEDS_REVIEW, and the case must not be
  auto-accepted. Its claim and field errors are still measured.

`evaluate_case` runs a case offline with its own profiles and returns the
metrics and any shortfalls, by fixed category.

**Rules for packs.**
- Truth comes from how the document was built.
- Packs never edit a discovery registry; packs are listed centrally.
- The central list is `tests/qualification/registry.py`. `tests/qualification/test_packs.py` runs
  every registered case through the current pipeline and compares each outcome with
  `tests/qualification/pack_ledger.json`, which records whether the case is met and, if not,
  its shortfalls and the follow-up that owns them. The test fails when any outcome moves, in
  either direction: a regression is caught, and a fix has to be recorded. Truth is never
  changed to fit.
- PDF bytes may differ between builds; printed content, truth and results
  must not.
