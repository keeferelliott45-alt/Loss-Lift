"""The same guarantees, asserted through the pipeline rather than the helpers.

Every defect below survived a helper-level test that passed. That is the point
of this file: a classifier can answer correctly and still have its answer
discarded downstream, by a continuation rule, a furniture rule, a totals-row
selector that only records rows it could parse, or a metadata step that never
carries the evidence to the rule meant to report it. The helper is not the
product; the document status is.

Four themes.

**Vision counts.** A scanned page reports a claim count as a bare integer with
no wording around it. That is not enough to establish document scope, and it
is not enough repeated three times either -- three sections each holding three
claims agree at three while the document holds nine. The counts are real
evidence and must reach the document; what they must not do is silently become
the report's total. This unit deliberately does not extend the vision schema to
carry the wording that would settle it: the honest answer today is "unresolved,
review it".

**Refusal is not erasure, in every punctuation.** ``4 30,000.00`` was the shape
that got fixed; ``4 / 30,000.00``, ``4 USD 30,000.00``, ``4 and 30,000.00`` and
``4-30,000.00`` are the same defect wearing different separators, and each was
still being erased. Enumerating separators is not a fix -- the next document
brings a new one. The rule is the general one: a cell under a monetary column
that carries digits and yields no confident single value survives as
unresolved.

**A label explains its own value and nothing else.** ``Software version
unknown`` beside ``$500.00`` erased the $500. ``Policy year 2024`` beside
``2024.00`` promoted the year to money. Both directions are wrong, and the
distinction the code drew -- whether the label happened to contain a digit --
was never evidence about the money.

**Provenance, including when nothing parsed.** A GRAND TOTAL row all of whose
cells are unreadable is still that document's total row. It was dropped whole,
because the selector only recorded rows it managed to parse a value from.

Fixtures are synthetic: generated PDFs and injected vision payloads, no corpus,
no platform-specific paths.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pymupdf
import pytest

from core.extract_vision import VisionExtraction
from core.pipeline import run_pipeline
from core.schema import DocumentStatus, Severity

LEFT = 40.0
LINE = 14.0
COLUMNS = (0.0, 90.0, 175.0, 250.0, 330.0, 420.0)
HEADERS = ("Claim No", "Date of Loss", "Status", "Paid Total", "Reserve Total",
           "Total Incurred")
CLAIMS = (
    ("CN-1001", "03/12/2024", "OPEN", "1,200.00", "3,800.00", "5,000.00"),
    ("CN-1002", "05/04/2024", "CLOSED", "2,450.00", "0.00", "2,450.00"),
    ("CN-1003", "09/21/2024", "OPEN", "775.50", "1,224.50", "2,000.00"),
)


def _write(path, rows, *, pages=1, footer_row=None):
    """A loss run with no printed grand total and no printed claim count."""
    document = pymupdf.open()
    for page_number in range(pages):
        page = document.new_page(width=612, height=792)
        y = 50.0
        for line in (
            "MERIDIAN MUTUAL ASSURANCE",
            "LOSS RUN REPORT",
            "Named Insured: Northwind Fabrication Ltd",
            "Policy Number: GL-4417-2024",
            "Policy Period: 01/01/2024 to 12/31/2024",
            "Valuation Date: 12/31/2024",
            "Currency: USD",
        ):
            page.insert_text((LEFT, y), line, fontsize=9)
            y += LINE
        y += LINE
        for offset, label in zip(COLUMNS, HEADERS):
            page.insert_text((LEFT + offset, y), label, fontsize=8.5)
        y += LINE
        for row in rows:
            for offset, cell in zip(COLUMNS, row):
                if cell:
                    page.insert_text((LEFT + offset, y), cell, fontsize=8.5)
            y += LINE
        if footer_row is not None:
            y += LINE
            for offset, cell in zip(COLUMNS, footer_row):
                if cell:
                    page.insert_text((LEFT + offset, y), cell, fontsize=8.5)
    document.save(path)
    document.close()
    return path


def _unplaced_texts(document):
    return {
        text
        for row in document.unplaced_rows
        for text in list(row.amounts.values()) + list(row.ambiguous_values.values())
    }


def _blocking(result):
    return [f for f in result.reconciliation.findings if f.severity is Severity.ERROR]


# --------------------------------------------------------------------------
# 1-3, 9. Vision counts, through the whole pipeline
# --------------------------------------------------------------------------


class _Model:
    """A Gemini stand-in returning one payload per page, in call order."""

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0
        self.models = self

    def generate_content(self, model, contents, config=None):
        body = self.payloads[min(self.calls, len(self.payloads) - 1)]
        self.calls += 1
        return type("Response", (), {"text": json.dumps(body)})()


def _payload(count, claim_numbers):
    return {
        "headers": ["Claim No", "Date of Loss", "Status", "Paid Total"],
        "rows": [
            {"cells": [number, "03/12/2024", "OPEN", "1,000.00"], "kind": "data"}
            for number in claim_numbers
        ],
        "printed_claim_count": count,
        "valuation_date": "12/31/2024",
    }


def _scanned(path, pages):
    """A PDF with no text layer at all, so every page takes the vision path."""
    document = pymupdf.open()
    for _ in range(pages):
        page = document.new_page(width=612, height=792)
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40))
        pixmap.clear_with(255)
        page.insert_image(pymupdf.Rect(20, 20, 60, 60), pixmap=pixmap)
    document.save(path)
    document.close()
    return path


def _vision(model):
    from core.extract_vision import extract_scanned_pages

    def extractor(path, pages):
        return extract_scanned_pages(path, pages, client=model)

    return extractor


def _run_vision(tmp_path, name, counts, per_page=2):
    path = _scanned(tmp_path / name, len(counts))
    model = _Model([
        _payload(count, [f"VC-{page}{i}" for i in range(per_page)])
        for page, count in enumerate(counts, start=1)
    ])
    return run_pipeline(path, use_vision=True, vision_extractor=_vision(model))


def test_distinct_vision_counts_reach_the_document_and_block(tmp_path):
    """Three scanned sections reporting 3, 2 and 1 state no report total.

    Every count must survive with its page -- they are the only evidence of
    why R-05 never ran -- R-27 must report them, and the badge must not go
    green.
    """
    result = _run_vision(tmp_path, "distinct.pdf", [3, 2, 1])
    document = result.document
    assert document.printed_claim_count is None, document.printed_claim_count
    assert [(e["page"], e["count"]) for e in document.printed_count_evidence] == [
        (1, 3), (2, 2), (3, 1)
    ], document.printed_count_evidence
    finding = next(
        f for f in result.reconciliation.findings if f.rule_id == "R-27"
    )
    assert finding.severity is Severity.ERROR
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


def test_repeated_vision_counts_do_not_establish_document_scope(tmp_path):
    """3, 3 and 3 agree, and agreement is not scope.

    Three sections of three claims each agree at three while the document
    holds nine. The vision payload carries a number and no wording, so nothing
    in it distinguishes a section's count from the report's -- and this unit
    does not extend the schema to find out. Unresolved is the honest answer.
    """
    result = _run_vision(tmp_path, "repeated.pdf", [3, 3, 3])
    document = result.document
    assert document.printed_claim_count is None, (
        f"repetition was treated as scope: {document.printed_claim_count}"
    )
    assert len(document.printed_count_evidence) == 3, document.printed_count_evidence
    assert any(f.rule_id == "R-27" for f in _blocking(result))
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


def test_a_single_vision_count_is_still_unresolved(tmp_path):
    """One page's integer says nothing about the document either."""
    result = _run_vision(tmp_path, "single.pdf", [6])
    assert result.document.printed_claim_count is None
    assert [e["count"] for e in result.document.printed_count_evidence] == [6]
    assert any(f.rule_id == "R-27" for f in _blocking(result))


def test_a_digital_candidate_does_not_silence_vision_counts(tmp_path):
    """A mixed document: digital pages state a count, scanned pages state others.

    The digital count is one candidate among several, not an answer that
    excuses ignoring the rest. Only source evidence establishing *document*
    scope may set the document's count.
    """
    path = tmp_path / "mixed.pdf"
    document = pymupdf.open()
    page = document.new_page(width=612, height=792)
    y = 50.0
    for line in (
        "MERIDIAN MUTUAL ASSURANCE",
        "Valuation Date: 12/31/2024",
        "Claim Count = 2",
    ):
        page.insert_text((LEFT, y), line, fontsize=9)
        y += LINE
    for offset, label in zip(COLUMNS, HEADERS):
        page.insert_text((LEFT + offset, y), label, fontsize=8.5)
    y += LINE
    for row in CLAIMS[:2]:
        for offset, cell in zip(COLUMNS, row):
            page.insert_text((LEFT + offset, y), cell, fontsize=8.5)
        y += LINE
    scan = document.new_page(width=612, height=792)
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40))
    pixmap.clear_with(255)
    scan.insert_image(pymupdf.Rect(20, 20, 60, 60), pixmap=pixmap)
    document.save(path)
    document.close()

    model = _Model([_payload(7, ["VC-1", "VC-2"])])
    result = run_pipeline(path, use_vision=True, vision_extractor=_vision(model))
    counts = {e["count"] for e in result.document.printed_count_evidence}
    assert 7 in counts, (
        f"the scanned page's count was dropped: {result.document.printed_count_evidence}"
    )
    assert 2 in counts, result.document.printed_count_evidence
    assert result.document.printed_claim_count is None
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


@pytest.mark.parametrize("count", [True, False, -4, 0.5, "3"])
def test_invalid_vision_counts_are_refused(tmp_path, count):
    """A boolean, a negative, a float and a string are not claim counts.

    ``isinstance(True, int)`` is True in Python, so a model answering ``true``
    used to become a printed claim count of 1.
    """
    path = _scanned(tmp_path / "invalid.pdf", 1)
    model = _Model([_payload(count, ["VC-1", "VC-2"])])
    result = run_pipeline(path, use_vision=True, vision_extractor=_vision(model))
    assert result.document.printed_claim_count is None
    assert result.document.printed_count_evidence == [], (
        f"{count!r} was accepted as a count"
    )


# --------------------------------------------------------------------------
# 4. Refusal is not erasure, whatever the separator
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "printed",
    ["4 30,000.00", "4 / 30,000.00", "4 USD 30,000.00", "4 and 30,000.00",
     "4-30,000.00", "4; 30,000.00", "4|30,000.00"],
)
def test_every_merged_form_survives_as_unresolved(tmp_path, printed):
    """Enumerating separators is not a fix; the next document brings another.

    A cell under a monetary column carrying digits, which yields no confident
    single value, is unresolved evidence. That is the whole rule, and it holds
    for a slash, a currency word, a conjunction, a hyphen or a pipe alike.
    """
    path = _write(tmp_path / "merged.pdf", CLAIMS + (("", "", "", printed, "", ""),))
    result = run_pipeline(path, use_vision=False)
    document = result.document
    assert document.unplaced_rows, f"{printed!r} was erased"
    row = document.unplaced_rows[0]
    assert row.amounts == {}, f"{printed!r} was accepted as an amount"
    assert row.parsed_amounts == {}, f"{printed!r} was given a value"
    assert printed in row.ambiguous_values.values(), row.ambiguous_values
    assert row.page == 1 and row.row is not None, row
    finding = next(f for f in result.reconciliation.findings if f.rule_id == "R-23")
    assert printed in finding.message, finding.message
    assert "430000" not in finding.message and "430,000" not in finding.message
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


def test_a_merged_row_repeated_across_pages_survives_every_time(tmp_path):
    """Evidence is weighed before the continuation and furniture rules.

    A row repeated on three pages is what a running footer looks like -- and
    also what a spreadsheet continuation looks like. Numeric evidence is not
    furniture, on any of them.
    """
    path = _write(
        tmp_path / "repeat.pdf",
        CLAIMS + (("", "", "", "4 30,000.00", "", ""),),
        pages=3,
    )
    result = run_pipeline(path, use_vision=False)
    pages = {row.page for row in result.document.unplaced_rows}
    assert pages == {1, 2, 3}, [
        (r.page, r.ambiguous_values) for r in result.document.unplaced_rows
    ]


def test_prose_is_still_not_numeric_evidence(tmp_path):
    """The guard the general rule must not lose: a text row stays a warning."""
    path = _write(
        tmp_path / "prose.pdf", CLAIMS + (("", "", "", "Property Claim", "", ""),)
    )
    result = run_pipeline(path, use_vision=False)
    assert "Property Claim" not in _unplaced_texts(result.document)


# --------------------------------------------------------------------------
# 5-6. A label explains its own value and nothing else
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label", ["Software version unknown", "Policy identifier UNKNOWN"]
)
def test_a_label_does_not_erase_the_money_beside_it(tmp_path, label):
    """The label explains its own cell. The $500.00 is a separate fact."""
    path = _write(
        tmp_path / "mixed-row.pdf",
        CLAIMS + ((label, "", "", "$500.00", "", ""),),
    )
    result = run_pipeline(path, use_vision=False)
    assert "$500.00" in _unplaced_texts(result.document), (
        f"{label!r} erased an unrelated monetary cell"
    )
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


@pytest.mark.parametrize(
    "label, printed", [("Policy year 2024", "2024.00"),
                       ("Software version 1.20", "1.20")]
)
def test_a_label_carrying_a_digit_does_not_make_its_value_money(
    tmp_path, label, printed
):
    """The label's own digit is not evidence about the column beside it.

    The label repeats the figure -- "Policy year 2024" over "2024.00" -- which
    is the row saying the same thing twice, not two competing statements. It
    is neither promoted to a definite amount nor held as unresolved: the row
    has explained its own number, and an exceptions list carrying rows the
    document already explained is one nobody reads to the end.

    The neighbouring test above holds the other half of this: a label exempts
    the value it names and cannot speak for an unrelated ``$500.00`` sharing
    its row.
    """
    path = _write(
        tmp_path / "labelled.pdf", CLAIMS + ((label, "", "", printed, "", ""),)
    )
    result = run_pipeline(path, use_vision=False)
    document = result.document
    assert printed not in _unplaced_texts(document), (
        f"{label!r} left {printed!r} on the exceptions list: "
        f"{[(r.amounts, r.ambiguous_values) for r in document.unplaced_rows]}"
    )


# --------------------------------------------------------------------------
# 7-8. The total row's identity survives even when nothing parses
# --------------------------------------------------------------------------


def test_a_wholly_unreadable_grand_total_row_is_still_the_total_row(tmp_path):
    """Every monetary cell refused, so nothing was parsed -- and the row was
    dropped whole, because the selector only recorded rows it could parse.

    It is still this document's printed total, and R-04 is now checking
    nothing at all against it.
    """
    path = _write(
        tmp_path / "grand.pdf",
        CLAIMS,
        footer_row=("GRAND TOTAL", "", "", "4 30,000.00", "1 2", "5 6"),
    )
    result = run_pipeline(path, use_vision=False)
    document = result.document
    assert document.unreadable_totals, "the total row was lost entirely"
    assert "4 30,000.00" in document.unreadable_totals.values(), (
        document.unreadable_totals
    )
    assert document.unreadable_totals_page == 1
    assert document.unreadable_totals_row is not None, (
        "the total row's identity was not retained"
    )
    findings = [f for f in result.reconciliation.findings if f.rule_id == "R-26"]
    assert findings, "an unreadable grand total raised nothing"
    assert all(f.severity is Severity.ERROR for f in findings)
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


def test_r26_line_numbers_follow_the_one_based_reviewer_convention(tmp_path):
    """A reviewer counts lines from one; the index counts from zero.

    Every other place that says "page N, line M" adds one -- Claim.where and
    UnplacedRow.where both do. R-26 printed the raw index, sending a reviewer
    to the line above. The finding's *identity* stays on the index, so it is
    stable regardless of how it is displayed.
    """
    path = _write(
        tmp_path / "lines.pdf",
        CLAIMS,
        footer_row=("GRAND TOTAL", "", "", "4 30,000.00", "1 2", "5 6"),
    )
    result = run_pipeline(path, use_vision=False)
    index = result.document.unreadable_totals_row
    finding = next(f for f in result.reconciliation.findings if f.rule_id == "R-26")
    assert f"line {index + 1}" in finding.message, (index, finding.message)
    assert f"row-{index}-" in finding.condition, (index, finding.condition)


@pytest.mark.parametrize(
    "prose",
    ["Property Claim", "within the last 30 days.", "history located for this"],
)
def test_prose_mentioning_a_number_is_not_monetary_evidence(tmp_path, prose):
    """Found by the corpus, not by reasoning -- twice now.

    Generalising "two numbers run together" to "anything with a digit" swept
    up a disclaimer ending "within the last 30 days.", one finding per line of
    the paragraph. The rule is two general properties, neither a list of
    separators: more than one number in the cell, which is what a collision is;
    or no word in it at all, which is a number nobody could read. A sentence
    that mentions a figure satisfies neither.
    """
    path = _write(tmp_path / "prose-num.pdf", CLAIMS + (("", "", "", prose, "", ""),))
    result = run_pipeline(path, use_vision=False)
    assert prose not in _unplaced_texts(result.document), (
        f"{prose!r} was read as monetary evidence"
    )
