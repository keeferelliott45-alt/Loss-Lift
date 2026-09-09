"""Where numeric evidence is decided, and what gets to overrule it.

The previous unit answered *what* a cell is. This one is about *when* that
answer is taken and *what may override it*, because a correct classification
is worthless if a furniture rule removed the row before the classifier ran, or
if a totals selector took the wrong row, or if a count with no scope was
overruled by one that also has none.

Five routing failures, all of the same shape: a decision made on partial
evidence, earlier or later than the evidence that settles it.

**Furniture ran first, and only protected what parsed.** A row repeated on
three pages is removed as a running footer unless it carries numeric evidence
-- and the test for that asked whether the cell *parsed*. An unreadable cell
parses by definition never, so exactly the evidence this whole thread exists to
preserve was the evidence furniture was free to delete.

**"More than one number, or no word at all" fails in both directions.**
``Paid 30,000.00`` is one number and one word, so it was erased; ``reported
within 30 to 60 days`` is two numbers, so it became a monetary finding. Neither
counting numbers nor looking for words is evidence about what a cell *is*. What
the column does on its other rows is, and so is what sits beside the cell on
its own row.

**Parentheses are not a currency mark.** Accounting negatives wear them, and so
does ``(2024)`` beside the words "Policy year".

**A count with no scope cannot settle one that also has none.** A digital
"Total Claims: 2" is total-led wording, which is why it outranks a bare policy
subtotal -- but a scanned page reporting 7 is a competing statement the wording
does not answer. Explicit report wording still wins; that control is kept.

**Two unreadable totals, and the first won.** Ranking exists to prefer a grand
total over a policy subtotal, and it was skipped entirely for rows that parse
nothing -- so a subtotal printed above a grand total became the document's.

Fixtures are generated PDFs and injected vision payloads driven through
``run_pipeline``: no corpus, no platform-specific paths.
"""

from __future__ import annotations

import json

import pymupdf
import pytest

from core.pipeline import ColumnMapping, build_claims, page_furniture, run_pipeline
from core.schema import DocumentStatus, RawRow, RawTable, Severity

LEFT = 40.0
LINE = 14.0
COLUMNS = (0.0, 90.0, 175.0, 250.0, 330.0, 420.0)
HEADERS = ("Claim No", "Date of Loss", "Status", "Loss Description", "Paid Total",
           "Total Incurred")
CLAIMS = (
    ("CN-1001", "03/12/2024", "OPEN", "Ladder fall", "1,200.00", "5,000.00"),
    ("CN-1002", "05/04/2024", "CLOSED", "Rear-end collision", "2,450.00", "2,450.00"),
    ("CN-1003", "09/21/2024", "OPEN", "Water ingress", "775.50", "2,000.00"),
)

#: The column offsets by name, so a fixture row can say where it puts text.
DESCRIPTION = 3
PAID = 4


def _write(path, rows, *, pages=1, footer_rows=()):
    document = pymupdf.open()
    for _ in range(pages):
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
        for footer in footer_rows:
            y += LINE
            for offset, cell in zip(COLUMNS, footer):
                if cell:
                    page.insert_text((LEFT + offset, y), cell, fontsize=8.5)
    document.save(path)
    document.close()
    return path


def _texts(document):
    return {
        text
        for row in document.unplaced_rows
        for text in list(row.amounts.values()) + list(row.ambiguous_values.values())
    }


def _r23(result):
    return [f for f in result.reconciliation.findings if f.rule_id == "R-23"]


def _descriptions(document):
    return " ".join(c.loss_description or "" for c in document.claims)


# --------------------------------------------------------------------------
# 1. Furniture may not delete evidence it cannot read
# --------------------------------------------------------------------------


@pytest.mark.parametrize("printed", ["4 / 30,000.00", "4-30,000.00", ".00 .00"])
def test_unreadable_evidence_repeated_on_every_page_survives_on_every_page(
    tmp_path, printed
):
    """Three pages, the same unreadable cell on each.

    Furniture removal runs before the evidence is classified and protected only
    rows whose cells *parsed*. An unreadable cell never parses, so the one kind
    of evidence that cannot defend itself was the one furniture was free to
    delete -- and a spreadsheet continuation repeating its figures across pages
    looks exactly like a running footer.
    """
    path = _write(
        tmp_path / "repeat.pdf", CLAIMS + (("", "", "", "", printed, ""),), pages=3
    )
    result = run_pipeline(path, use_vision=False)
    rows = [r for r in result.document.unplaced_rows if printed in _row_texts(r)]
    assert {r.page for r in rows} == {1, 2, 3}, [
        (r.page, r.row, r.ambiguous_values) for r in result.document.unplaced_rows
    ]
    assert len({(r.page, r.row) for r in rows}) == 3, "provenance was collapsed"
    assert all(r.amounts == {} for r in rows), rows
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


def _row_texts(row):
    return list(row.amounts.values()) + list(row.ambiguous_values.values())


def test_genuine_repeated_page_furniture_is_still_exempt(tmp_path):
    """The control. A strapline on every page is one strapline, not three
    findings, and it carries nothing numeric to preserve."""
    path = _write(
        tmp_path / "furniture.pdf",
        CLAIMS + (("Confidential - internal use only", "", "", "", "", ""),),
        pages=3,
    )
    result = run_pipeline(path, use_vision=False)
    assert not any(
        "Confidential" in text for text in _texts(result.document)
    ), _texts(result.document)


# --------------------------------------------------------------------------
# 2. Financial text carrying one number is evidence, not litter
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "printed",
    ["Paid 30,000.00", "Amount 30,000.00", "Payment: 30,000.00", "30,000.00 dollars"],
)
def test_a_labelled_amount_alone_in_a_money_column_is_evidence(tmp_path, printed):
    """One number and one word, and it was erased.

    The rule it failed was "more than one number, or no word at all" -- a
    counting shortcut that has nothing to say about what a cell is. This cell
    stands alone under a column every other row fills with money. Whether the
    30,000.00 belongs to a claim is exactly what is unknown, so it is kept and
    reported, never folded into the claim above it.
    """
    path = _write(tmp_path / "labelled.pdf", CLAIMS + (("", "", "", "", printed, ""),))
    result = run_pipeline(path, use_vision=False)
    assert printed in _texts(result.document), (
        f"{printed!r} was erased; unplaced={result.document.unplaced_rows}"
    )
    assert "30,000.00" not in _descriptions(result.document), (
        f"{printed!r} was folded into a claim description"
    )
    assert _r23(result), "no finding was raised"
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


# --------------------------------------------------------------------------
# 3. Prose continuing into a money column stays prose
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "left, right",
    [
        ("reported within 30", "to 60 days"),
        ("2 of 3 vehicles", "were total losses"),
        ("01/01/2024 to", "12/31/2024 term"),
    ],
)
def test_prose_running_into_a_money_column_is_not_a_finding(tmp_path, left, right):
    """The surrounding evidence is the point.

    This row continues the description of the claim above it, and the sentence
    runs on past the description column into the money column. The fragment in
    the money column is the second half of that sentence, not a figure -- and
    the evidence for that is on the row: text immediately to its left, in a
    column that is not money, continuing into it.

    Counting numbers cannot see this. "reported within 30 to 60 days" carries
    two, which is precisely why the counting rule called it money.
    """
    path = _write(
        tmp_path / "prose.pdf",
        CLAIMS + (("", "", "", left, right, ""),),
    )
    result = run_pipeline(path, use_vision=False)
    assert right not in _texts(result.document), (
        f"prose continuation {right!r} became monetary evidence: "
        f"{[r.ambiguous_values for r in result.document.unplaced_rows]}"
    )
    assert not any(right in f.message for f in _r23(result)), [
        f.message for f in _r23(result)
    ]


# --------------------------------------------------------------------------
# 4. Established non-money is exempt, not merely un-promoted
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label, printed",
    [
        ("Policy year", "2024.00"),
        ("Policy year", "(2024)"),
        ("Software version", "1.20"),
        ("Excel serial date", "45292.00"),
    ],
)
def test_contextually_established_non_money_enters_neither_channel(
    tmp_path, label, printed
):
    """The row says what the number is, so there is nothing unresolved.

    An earlier unit weakened this to "not money, but kept as unresolved". That
    was wrong in a way that matters: it fills the exceptions list with rows the
    document has already explained, and an exceptions list nobody can finish
    reading is one nobody reads. Where the row establishes the value as
    non-money it is exempt from both channels.

    "(2024)" is the same fact wearing parentheses. Accounting negatives use
    them, so a bare parenthesis was being read as a currency mark -- which
    would make a labelled year definite money, the strongest possible reading
    of the weakest possible evidence.
    """
    path = _write(
        tmp_path / "labelled-non-money.pdf",
        CLAIMS + ((label, "", "", "", printed, ""),),
    )
    result = run_pipeline(path, use_vision=False)
    document = result.document
    assert printed not in _texts(document), (
        f"{label} {printed!r} was carried as evidence: "
        f"{[(r.amounts, r.ambiguous_values) for r in document.unplaced_rows]}"
    )
    money = {t for row in document.unplaced_rows for t in row.amounts.values()}
    assert printed not in money, f"{label} {printed!r} became money"


def test_an_accounting_negative_is_still_money(tmp_path):
    """The control parentheses exist for: with no label denying it, a
    parenthesised amount under a money column is a negative amount."""
    path = _write(
        tmp_path / "accounting.pdf", CLAIMS + (("", "", "", "", "(1,234.56)", ""),)
    )
    result = run_pipeline(path, use_vision=False)
    assert "(1,234.56)" in _texts(result.document), _texts(result.document)


# --------------------------------------------------------------------------
# 5. A wordless count cannot be settled by another wordless count
# --------------------------------------------------------------------------


class _Model:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0
        self.models = self

    def generate_content(self, model, contents, config=None):
        body = self.payloads[min(self.calls, len(self.payloads) - 1)]
        self.calls += 1
        return type("Response", (), {"text": json.dumps(body)})()


def _vision(model):
    from core.extract_vision import extract_scanned_pages

    def extractor(path, pages):
        return extract_scanned_pages(path, pages, client=model)

    return extractor


def _mixed(path, header_line):
    """One digital page stating a count, and one scanned page after it."""
    document = pymupdf.open()
    page = document.new_page(width=612, height=792)
    y = 50.0
    for line in ("MERIDIAN MUTUAL ASSURANCE", "Valuation Date: 12/31/2024",
                 header_line):
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
    return path


def _run_mixed(tmp_path, name, header_line, vision_count=7):
    path = _mixed(tmp_path / name, header_line)
    model = _Model([{
        "headers": ["Claim No", "Date of Loss", "Status", "Paid Total"],
        "rows": [
            {"cells": [n, "03/12/2024", "OPEN", "1,000.00"], "kind": "data"}
            for n in ("VC-1", "VC-2")
        ],
        "printed_claim_count": vision_count,
        "valuation_date": "12/31/2024",
    }])
    return run_pipeline(path, use_vision=True, vision_extractor=_vision(model))


def test_a_total_led_count_does_not_outrank_an_unscoped_competitor(tmp_path):
    """"Total Claims: 2" names a total without naming which total.

    That is enough to outrank a policy subtotal, whose wording says outright
    that it covers one policy. It is not enough to outrank a scanned page
    reporting 7, because nothing in either statement says which of them covers
    the document. Both are kept; neither is adopted.
    """
    result = _run_mixed(tmp_path, "competing.pdf", "Total Claims: 2")
    document = result.document
    assert document.printed_claim_count is None, document.printed_claim_count
    counts = {e["count"] for e in document.printed_count_evidence}
    assert {2, 7} <= counts, document.printed_count_evidence
    finding = next(f for f in result.reconciliation.findings if f.rule_id == "R-27")
    assert finding.severity is Severity.ERROR
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


def test_explicit_report_wording_remains_authoritative(tmp_path):
    """The control. "Overall Total Claims" says which total it is, and a
    scanned page's bare integer does not contradict a statement of scope."""
    result = _run_mixed(tmp_path, "overall.pdf", "Overall Total Claims: 2")
    assert result.document.printed_claim_count == 2, (
        result.document.printed_claim_count
    )


# --------------------------------------------------------------------------
# 6. Unreadable totals are ranked like readable ones
# --------------------------------------------------------------------------


def test_an_unreadable_grand_total_outranks_an_unreadable_subtotal(tmp_path):
    """Ranking is why a grand total beats a policy subtotal. Rows that parse
    nothing were exempted from it, so whichever came first won -- and a policy
    subtotal is printed first."""
    path = _write(
        tmp_path / "two-totals.pdf",
        CLAIMS,
        footer_rows=(
            ("Policy Total", "", "", "", "4 30,000.00", "1 2"),
            ("GRAND TOTAL", "", "", "", "5 60,000.00", "3 4"),
        ),
    )
    result = run_pipeline(path, use_vision=False)
    document = result.document
    assert "5 60,000.00" in document.unreadable_totals.values(), (
        f"the subtotal was taken as the document total: {document.unreadable_totals}"
    )
    grand = document.unreadable_totals_row
    assert grand is not None
    # The document's own total row, not the subtotal's -- both raise R-26 and
    # each must name where it was printed.
    finding = next(
        f for f in result.reconciliation.findings
        if f.rule_id == "R-26" and "document's total row" in f.message
    )
    assert f"line {grand + 1}" in finding.message, (grand, finding.message)
    # The subtotal is still accounted for, separately from the document total.
    assert any(
        "4 30,000.00" in (section.unreadable_totals or {}).values()
        for section in document.printed_sections
    ), [s.unreadable_totals for s in document.printed_sections]
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


# --------------------------------------------------------------------------
# 7. What the column cannot say about itself, the row says
#
# The three structures below were each broken by an early draft of this unit
# and caught by the corpus rather than by reasoning. Their structure is
# reproduced synthetically here (spec section 9); none of them is a real
# document. Together they fix the boundary: a cell is monetary evidence when
# its column says so, or when its own row does, and never merely because a
# heading over it said "money".
# --------------------------------------------------------------------------


def _tables(rows, mapping_fields, headers, page=1):
    return [
        RawTable(page=page, headers=list(headers), strategy="words",
                 rows=[RawRow(cells=list(cells), page=page, line_index=i)
                       for i, cells in enumerate(rows)],
                 total_rows=[])
    ]


MONEY_ROW_HEADERS = ["Claim No", "Description", "Paid Medical", "Paid ALAE",
                     "Reserve Total"]
MONEY_ROW_MAPPING = ColumnMapping(
    headers=MONEY_ROW_HEADERS,
    fields={0: "claim_number", 1: "loss_description", 2: "paid_medical",
            3: "paid_expense", 4: "reserve_total"},
)


def test_a_column_with_only_one_cell_is_vouched_for_by_its_row():
    """A page carrying a single row cannot establish its own columns.

    The unreadable cell is the *only* cell that column has, so asking the
    column whether it carries money asks the cell to vouch for itself. The row
    answers instead: every other money cell on it reads as an amount, in
    columns this table did establish, so the row is a row of figures and the
    refused cell is one of them.

    Erasing it was the whole defect this unit exists to fix, arriving by a new
    route -- a stricter column test rather than a furniture rule.
    """
    tables = _tables(
        [["", "", ".00", ".00 .00", ".00"]],
        MONEY_ROW_MAPPING.fields, MONEY_ROW_HEADERS,
    )
    _, _, unplaced = build_claims(tables, MONEY_ROW_MAPPING, "us", "mdy")
    carried = {t for row in unplaced for t in
               list(row.amounts.values()) + list(row.ambiguous_values.values())}
    assert ".00 .00" in carried, (
        f"the fused cell was erased; its column had no other cell to speak "
        f"for it: {[(r.amounts, r.ambiguous_values) for r in unplaced]}"
    )


FOOTER_HEADERS = ["Reported", "Claim No", "Insured", "Paid Total", "Total Incurred"]
FOOTER_MAPPING = ColumnMapping(
    headers=FOOTER_HEADERS,
    fields={0: "date_reported", 1: "claim_number", 2: "claimant_name",
            3: "paid_indemnity", 4: "incurred_total"},
)


def test_a_running_footer_carrying_a_date_is_still_furniture():
    """The control the strapline test cannot give: furniture with a digit in it.

    A report footer prints "Print Date: 5/23/2023" and the date lands under a
    heading that says incurred. It carries a digit and it repeats on every
    page, which is the shape of a spreadsheet continuation -- but no money
    column of *this* block holds an amount, so nothing here is a row of
    figures and the date is not one. Exempting it made the letterhead of every
    page a finding, and folded it into a claim's description besides.
    """
    rows = [["Report ID : MV", "335", "Continental Casualty",
             "Print Date:", "5/23/2023"]]
    tables = [
        RawTable(page=page, headers=list(FOOTER_HEADERS), strategy="words",
                 rows=[RawRow(cells=list(rows[0]), page=page, line_index=13)],
                 total_rows=[])
        for page in (1, 2, 3)
    ]
    assert page_furniture(tables, FOOTER_MAPPING, "us"), (
        "a footer repeated on three pages was not recognised as furniture"
    )
    _, _, unplaced = build_claims(tables, FOOTER_MAPPING, "us", "mdy")
    assert unplaced == [], [(r.page, r.ambiguous_values) for r in unplaced]


DISCLAIMER_HEADERS = ["Paid Indemnity", "Paid ALAE", "Reserve Indemnity",
                      "Reserve ALAE", "Total Incurred"]
DISCLAIMER_MAPPING = ColumnMapping(
    headers=DISCLAIMER_HEADERS,
    fields={0: "paid_indemnity", 1: "paid_expense", 2: "reserve_indemnity",
            3: "reserve_expense", 4: "incurred_total"},
)


def test_a_disclaimer_paragraph_under_money_headings_is_not_evidence():
    """A legal notice wrapped across the page, every column of it mapped money.

    The extractor cuts the paragraph at whatever boundaries the claims table
    set, so its fragments land under money headings. "(4) any unauthorized"
    and "last 30 days." carry digits and sit in mapped money columns -- and no
    cell anywhere in the block reads as an amount, which is what says the
    whole block is prose.
    """
    tables = _tables(
        [
            ["recipient will use this information", "only for its own internal",
             "purposes or for such purposes", "authorized by the insured;",
             "(4) any unauthorized"],
            ["disclosure must be reported", "within the", "last 30 days.", "", ""],
        ],
        DISCLAIMER_MAPPING.fields, DISCLAIMER_HEADERS,
    )
    _, _, unplaced = build_claims(tables, DISCLAIMER_MAPPING, "us", "mdy")
    carried = {t for row in unplaced for t in
               list(row.amounts.values()) + list(row.ambiguous_values.values())}
    assert not carried, f"prose in a money column became evidence: {carried}"
