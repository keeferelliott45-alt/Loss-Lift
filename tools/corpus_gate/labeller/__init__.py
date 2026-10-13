"""R1 labelling helper: owner-side real-document truth adjudication.

Real-document accuracy is currently unmeasurable: the corpus lives only on
the owner's machine, the corpus gate publishes changes rather than values,
and no adjudicated truth labels exist. This package gives the owner a fast,
private, rigorous labelling session, entirely offline, using the repo's
existing privacy conventions (everything real stays outside Git).

Commands
--------

    py -m tools.corpus_gate.labeller init \
        --corpus DIR --manifest FILE --out DIR [--repo DIR] [--force]

        Verify the corpus against the manifest (hash of every document), run
        the CURRENT pipeline on each verified document, and write one
        worksheet CSV per document into --out: one row per claim-field with
        the extracted value, per-field provenance (page, row id, printed
        lines, read method, null reason) and a blank TRUTH column. A
        ``document_status`` row records the extraction's CLEAN /
        NEEDS_REVIEW verdict for the cross-tab. Exit 4 if any document's
        extraction failed; the others are still written.

    py -m tools.corpus_gate.labeller import \
        --corpus DIR --manifest FILE --worksheets DIR --truth FILE [--repo DIR]

        Read the adjudicated worksheets back, hash-verify the staged corpus
        copies again (refusing a document whose bytes no longer match the
        manifest), validate the label schema (money as plain Decimal
        strings, ISO dates, status/bool enums, AGREE/EMPTY tokens), refuse
        enum-invalid truth and duplicate or unknown rows, and merge the
        result into the truth file OUTSIDE the repository. Blank truth is
        written as an explicit null: never agreement, never imputed.

    py -m tools.corpus_gate.labeller measure \
        --truth FILE --manifest FILE [--labels FILE] --out DIR [--repo DIR]

        Compute field-level accuracy per document and per carrier /
        template-family -- money and text separately, with the golden
        harness's metric definitions (correct non-null values over expected
        non-null values; should-be-null and nulls-as-zeros tracked beside
        them) -- plus the agreement rate on ``claim_status`` and the
        extracted NEEDS_REVIEW x truth cross-tab. Un-adjudicated fields are
        reported as excluded-with-count and never counted as correct.
        Writes measure.json and measure.md into --out (local, private) and
        prints the publishable summary -- counts and rates only, no
        document content -- to stdout.

Privacy rules
-------------

No corpus content, document text, or path to the owner's staging area may
appear in this package or anything it writes inside the repository. The
manifest, worksheets and truth file all live outside the repo, exactly like
the corpus gate's manifest and labels; ``init`` and ``import`` refuse to
write inside the working tree. The labeller never contacts the network and
never runs a model.

Fail-closed rules
-----------------

Refuse to run without a valid manifest; refuse documents whose hash
mismatches; refuse truth values the schema cannot accept; never impute a
missing truth. An empty TRUTH cell means "not adjudicated" everywhere it
appears.
"""
