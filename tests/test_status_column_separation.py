"""A status column merged into the claimant name beside it.

An XL liability loss run prints "Claim Status" and "Claimant Name" as
neighbouring columns, wrapped over two header lines ("Claim" and
"Claimant Name" on the first, "Status" under "Claim" on the second). Two
of its rows have a claimant name that runs far enough right to touch the
currency beside it, so ``column_bounds`` -- which needs every row to keep
a channel clear -- cannot separate name from currency, and returns one
wide column covering status, name and currency together.

``subdivided`` exists for exactly that: the header names the column more
than once, so the rows are asked where the boundary is. It recovers the
name/currency edge but not the status/name edge, and the reason is the
statistic it judges with. ``_word_spacing`` is documented as "the space
this column sets between its own words", and a cut is taken only where
the rows leave a channel wider than that. It is measured across the whole
merged column, so on this page it averages in the wide gaps between three
different printed columns and reports 3.856pt as ordinary word spacing --
wider than the 3.8pt channel the rows actually leave between the status
and the name. The real boundary is then rejected for being too narrow to
be a boundary, and every row's status is read as part of the claimant's
name.

Geometry below is the real page's own word coordinates; only the names
are synthetic (spec section 9). Coordinates are what the mechanism reads
-- ``_word_spacing`` and ``_blank_point`` never look at the text -- so
substituting names changes nothing the test depends on.
"""

from __future__ import annotations

from core.extract_digital import (
    COLUMN_GUTTER_FACTOR,
    Line,
    Word,
    column_bounds,
    label_bounds,
    subdivided,
)
from core.profiles import guess_field

CHAR_WIDTH = 5.4698

#: The merged column ``column_bounds`` produces on the real page, and the
#: trigger-date column beside it (``subdivided`` needs more than one).
TRIGGER_DATE_COLUMN = (1424.7, 1473.5)
MERGED_COLUMN = (1483.3, 1675.8)

#: Real x positions of the two header lines' right-hand words.
_HEADER_ROWS = [
    [("Trigger", 1418.1, 1449.5), ("Date", 1451.9, 1472.9), ("Claim", 1477.3, 1502.3),
     ("Claimant", 1532.5, 1572.2), ("Name", 1574.5, 1601.2), ("Reporting", 1657.3, 1701.3)],
    [("Status", 1477.3, 1504.8), ("Currency", 1657.3, 1697.4)],
]

#: Real row geometry with synthetic names. The last two rows are the ones
#: whose name touches the currency and closes the channel -- without them
#: ``column_bounds`` would separate name from currency by itself and the
#: page would never have posed this question.
_DATA_ROWS = [
    [("28-Jan-13", 1429.8, 1473.5), ("Closed", 1499.0, 1528.7),
     ("ALVARADO", 1532.5, 1586.0), ("DE", 1588.3, 1600.5), ("ROSA", 1602.9, 1641.3),
     ("EUR", 1657.3, 1675.8)],
    [("06-Dec-12", 1427.5, 1473.5), ("Closed", 1499.0, 1528.7),
     ("PAULA", 1532.5, 1563.5), ("BENNETT", 1565.9, 1611.3), ("EUR", 1657.3, 1675.8)],
    [("15-Jan-13", 1429.8, 1473.5), ("Closed", 1499.0, 1528.7),
     ("MAE", 1532.5, 1570.2), ("CHANDLER", 1572.5, 1613.5), ("EUR", 1657.3, 1675.8)],
    [("07-Dec-12", 1427.5, 1473.5), ("Reopened", 1499.0, 1528.7),
     ("AUGUSTA", 1532.5, 1576.7), ("DELACROIX", 1579.1, 1629.9), ("EUR", 1657.3, 1675.8)],
    # name glued to the currency by text extraction, as on the real page
    [("26-Nov-12", 1429.8, 1473.5), ("Closed", 1499.0, 1528.7),
     ("MARIA", 1532.5, 1563.5), ("CONCETTA", 1565.9, 1614.5),
     ("HALVORSENEUR", 1616.9, 1675.8)],
    [("11-Feb-13", 1429.8, 1473.5), ("Closed", 1499.0, 1528.7),
     ("FLORENTINA", 1532.5, 1589.7), ("ALINA", 1592.1, 1619.5),
     ("OKONKWOEUR", 1621.9, 1675.8)],
]


def _line(rows, index, top):
    return Line(
        words=tuple(
            Word(text=t, x0=x0, x1=x1, top=top, bottom=top + 7.0) for t, x0, x1 in rows
        ),
        index=index,
    )


def _header_block():
    return [_line(_HEADER_ROWS[0], 1, 100.0), _line(_HEADER_ROWS[1], 2, 109.0)]


def _data_lines():
    return [_line(row, 3 + i, 120.0 + 10.0 * i) for i, row in enumerate(_DATA_ROWS)]


def test_fixture_reproduces_the_merged_column():
    """The fixture must actually pose the problem: whole-column gutters
    have to merge status, name and currency, or the test below would be
    proving nothing."""
    bounds = column_bounds(_data_lines(), CHAR_WIDTH)
    covering = [b for b in bounds if b[0] <= 1500 <= b[1]]
    assert covering, f"no column covers the status position: {bounds}"
    start, end = covering[0]
    assert start < 1528.7 and end > 1657.3, (
        f"fixture did not reproduce the merged column, got ({start}, {end})"
    )


def test_status_column_is_cut_away_from_the_claimant_name():
    """The rows leave a 3.8pt channel between the status and the name, and
    the header names both. That is the evidence ``subdivided`` asks for, so
    the cut must be taken."""
    result = subdivided(
        [TRIGGER_DATE_COLUMN, MERGED_COLUMN], _header_block(), _data_lines(), CHAR_WIDTH
    )
    cuts = sorted(
        {round(b[0], 1) for b in result} | {round(b[1], 1) for b in result}
    )
    assert any(1528.7 <= c <= 1532.5 for c in cuts), (
        f"status/claimant boundary not cut; edges found: {cuts}"
    )
    # The name/currency edge the rows also agree on must still be found.
    assert any(1641.3 <= c <= 1657.3 for c in cuts), (
        f"name/currency boundary lost; edges found: {cuts}"
    )


def test_status_and_claimant_name_resolve_to_separate_fields():
    """The point of the cut: the document's status stops being read as
    part of the claimant's name."""
    result = subdivided(
        [TRIGGER_DATE_COLUMN, MERGED_COLUMN], _header_block(), _data_lines(), CHAR_WIDTH
    )
    labels = label_bounds(_header_block(), result, CHAR_WIDTH)
    fields = [guess_field(label).field for label in labels]
    assert "claim_status" in fields, f"no status column; labels={labels}"
    assert "claimant_name" in fields, f"no claimant column; labels={labels}"
    # and they are two different columns, not one label read twice
    assert fields.index("claim_status") != fields.index("claimant_name")


def test_a_two_word_heading_over_prose_is_still_not_cut():
    """The guard the spacing test exists to provide: where a heading's two
    words sit over running prose that tiles the line, no channel is wider
    than ordinary word spacing and nothing may be cut -- otherwise every
    description gets halved."""
    header = [
        _line([("Accident", 100.0, 150.0), ("Description", 250.0, 320.0)], 1, 100.0),
    ]
    # Prose whose words tile the whole span, leaving only ordinary spaces.
    rows = []
    for i in range(6):
        words, x = [], 100.0
        for token in "THIRD PARTY ASSERTS FALL DUE TO AN OBSTACLE ON THE".split():
            width = len(token) * 5.0
            words.append((token, x, x + width))
            x += width + 3.0
        rows.append(_line(words, 3 + i, 120.0 + 10.0 * i))
    result = subdivided([(100.0, 320.0)], header, rows, CHAR_WIDTH)
    assert result == [(100.0, 320.0)], f"prose column was cut: {result}"
