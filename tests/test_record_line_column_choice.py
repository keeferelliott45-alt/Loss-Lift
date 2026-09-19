"""Choosing a record line's columns by what the rows actually fill.

A multi-line record reads each of its lines against its own columns, and
picks between two readings: the whitespace gutters the rows leave, and the
gaps between the header's own labels. The reading that names more columns
wins.

Counting names alone rewards the wrong answer when the header carries a
label for a column the page never fills. Liberty's cause/status line heads
seven fields, but prints nothing under "Jurisdiction State" on any row.
Header-derived bounds name all seven and split the cause where the *label*
"Cause" ends -- so a cause longer than its heading spills into the status
column, and a row reading

    Cause  = "ORA- STRUCK BY/AGAINST OBJECTS"
    Status = "OR OTHER PERSONS Closed"

loses the tail of its cause and its status together (``parse_status``
correctly refuses "OR OTHER PERSONS Closed", since the status does not
lead the cell). The rows' own gutters name six fields and read every one
of them correctly, but lose the count 6-7 and are discarded.

A label over a column no row fills is not evidence that the boundaries are
right, so it should not be what decides between them.

Geometry here is taken from Liberty's real cause/status record line; claim
content is synthetic (spec section 9).
"""

from __future__ import annotations

from core.extract_digital import (
    COLUMN_GUTTER_FACTOR,
    Line,
    Word,
    _extract_record_table,
    find_header_line,
    header_block,
    split_cells,
)
from core.records import detect_layout

CHAR_WIDTH = 3.3619

#: Real x spans from Liberty's record header block, one entry per header
#: line. "Jurisdiction State" is real and really is never filled.
_HEADER = [
    [("Claim Number", 41.2, 83.0), ("Claimant Name", 95.0, 190.0),
     ("Loss Date", 243.3, 290.0), ("Total Incurred", 704.3, 780.0)],
    [("Location", 41.2, 83.0), ("Paid Indemnity", 449.0, 500.0),
     ("Paid Medical", 534.0, 585.0), ("Total Paid", 704.3, 760.0)],
    [("Cause", 38.3, 60.0), ("Status", 243.3, 268.0),
     ("Jurisdiction State", 300.0, 380.0), ("Indemnity O/R", 449.0, 500.0),
     ("Medical O/R", 534.0, 585.0), ("Expense O/R", 619.2, 670.0),
     ("Outstanding Reserve", 704.3, 790.0)],
    [("Applied Recovery", 449.0, 520.0)],
    [("Date of Hire", 41.2, 90.0), ("Accident State", 243.3, 300.0)],
    [("Nature of Injury", 41.2, 100.0)],
    [("Accident Description", 41.2, 120.0)],
]

_CLAIMS = [
    # (claim number, claimant, cause text spans, status)
    ("SYN0000901", "ALVARADO,ROSA", [("0YA-", 38.3, 55.0), ("MISCELLANEOUS-NOC", 57.0, 140.0)]),
    ("SYN0000902", "BENNETT,PAULA", [("0LA", 38.3, 50.0), ("-MATERIAL", 52.0, 95.0),
                                     ("HANDLING", 97.0, 140.0), ("-", 142.0, 146.0),
                                     ("MECHANICAL", 148.0, 205.0)]),
    # the long one: its cause runs well past where the label "Cause" ends
    ("SYN0000903", "CHANDLER,MAE", [("0RA-", 38.3, 55.0), ("STRUCK", 57.0, 90.0),
                                    ("BY/AGAINST", 92.0, 145.0), ("OBJECTS", 147.0, 185.0),
                                    ("OR", 187.0, 196.0), ("OTHER", 198.0, 224.0),
                                    ("PERSONS", 226.4, 226.4 + 0.0)]),
]

_HEADER_PITCH = 8.0
_RECORD_GAPS = [10.0, 10.0, 10.0, 14.0, 10.0, 10.0]
_INTER_RECORD = 22.0


def _w(text, x0, x1, top):
    return Word(text=text, x0=x0, x1=x1, top=top, bottom=top + 6.0)


def _line(cells, index, top):
    return Line(words=tuple(_w(t, a, b, top) for t, a, b in cells), index=index)


def _build_page():
    lines, top, index = [], 100.0, 0
    for row in _HEADER:
        lines.append(_line(row, index, top))
        index += 1
        top += _HEADER_PITCH

    expected = {}
    for number, claimant, cause_words in _CLAIMS:
        top += _INTER_RECORD
        start = index
        rows = [
            [(number, 41.2, 83.0), (claimant, 95.0, 190.0),
             ("01/05/2023", 243.3, 290.0), ("$141.00", 704.3, 740.0)],
            [("-UNKNOWN", 41.2, 83.0), ("$0.00", 449.0, 470.0),
             ("$126.00", 534.0, 560.0), ("$141.00", 704.3, 740.0)],
            # cause words, then the status well clear of them at its own x
            [*[(t, a, b) for t, a, b in cause_words if b > a],
             ("Closed", 243.3, 263.8), ("$0.00", 449.0, 470.0),
             ("$0.00", 534.0, 560.0), ("$0.00", 619.2, 645.0),
             ("$0.00", 704.3, 730.0)],
            [("$0.00", 449.0, 470.0)],
            [("1/14/16", 41.2, 80.0), ("FL", 243.3, 255.0)],
            [("000 -UNDEFINED", 41.2, 100.0)],
            [("LIFTED MULCH BAGS", 41.2, 120.0)],
        ]
        for offset, row in enumerate(rows):
            lines.append(_line(row, index, top))
            index += 1
            if offset < len(_RECORD_GAPS):
                top += _RECORD_GAPS[offset]
        expected[number] = list(range(start, start + 7))
    return lines, expected


def _extract(lines):
    header_index, header_cells = find_header_line(lines, CHAR_WIDTH)
    block = header_block(lines, header_index, CHAR_WIDTH)
    block_end = max(line.index for line in block)
    layout = detect_layout(
        [split_cells(line, CHAR_WIDTH, COLUMN_GUTTER_FACTOR) for line in block]
    )
    assert layout.is_multi_line, "fixture must exercise the record path"
    return _extract_record_table(1, lines, header_index, block_end, layout, CHAR_WIDTH)


def _row_for(table, number):
    for row in table.rows:
        if row.cells and row.cells[0] == number:
            return row
    raise AssertionError(f"{number} not extracted")


def test_long_cause_keeps_its_tail_and_its_status():
    """The row whose cause outruns its heading must still read both fields."""
    lines, _ = _build_page()
    table = _extract(lines)
    assert table is not None

    headers = table.headers
    cause_at = headers.index("Cause")
    status_at = headers.index("Status")

    row = _row_for(table, "SYN0000903")
    assert row.cells[cause_at] == "0RA- STRUCK BY/AGAINST OBJECTS OR OTHER", (
        f"cause truncated: {row.cells[cause_at]!r}"
    )
    assert row.cells[status_at] == "Closed", (
        f"status column contaminated: {row.cells[status_at]!r}"
    )


def test_short_causes_are_unaffected():
    """The two rows whose cause fits must read exactly as before."""
    lines, _ = _build_page()
    table = _extract(lines)
    assert table is not None
    status_at = table.headers.index("Status")
    for number in ("SYN0000901", "SYN0000902"):
        assert _row_for(table, number).cells[status_at] == "Closed"


def test_every_claim_keeps_its_own_seven_lines():
    """Column choice must not disturb record membership or provenance."""
    lines, expected = _build_page()
    table = _extract(lines)
    assert table is not None
    for number, source_lines in expected.items():
        assert _row_for(table, number).source_lines == source_lines
