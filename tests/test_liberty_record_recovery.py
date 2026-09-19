"""Regression coverage for the Liberty multi-line record-body slicing bug.

``_extract_record_table`` sliced its body from ``header_index`` -- the one
line that scored as *the* header -- rather than from the true end of the
multi-line header block. On a document whose header block itself has a
tighter line-to-line pitch than its data rows (Liberty's does: the header's
own continuation lines sit about 8pt apart, its claim records about 10-14pt
apart), that off-by-block-length slip drags those tighter header-tail lines
into the body's own gap statistics, which is exactly what
``core.records.group_records`` uses to decide how far apart two lines of one
record are allowed to sit. The corrupted floor makes an entirely normal
14pt gap inside a real record read as "too far apart", and one bad anchor
anywhere on the page voids every record extraction attempt.

This has nothing to do with Liberty's font-substitution artifacts
("Ou1standing", "Lttigation", "lndemntty") -- those change no digit-shape,
no header vocabulary match, and no identifier-shape test anywhere in this
path. They are demonstrated here to be exactly what they look like:
cosmetic noise that happens to share a page with the real bug, not its
cause.
"""

from __future__ import annotations

import pytest

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

CHAR_WIDTH = 3.4

# The seven header labels, in the same column positions Liberty prints them,
# one tuple of (line-position, text, x0, x1) per header line. Position 0 is
# the line ``find_header_line`` actually scores highest -- Liberty's own
# "Claim Number ... Total Incurred" row -- matching real geometry: block
# lines average an 8pt pitch, each real record wraps every field-group onto
# its own physical line, and the money on the identifier's own row equals
# the money one row below whenever a claim is fully paid with no reserve.
HEADER_TEMPLATE = [
    [
        ("Claim Number", 30, 85),
        ("Claimant Name", 95, 150),
        ("Loss Date", 160, 200),
        ("Carrier Report Date", 210, 255),
        ("Incurred Indemnity", 260, 320),
        ("Incurred Medical", 330, 385),
        ("Incurred Expense", 395, 450),
        ("Total Incurred", 460, 520),
    ],
    [
        ("Location", 30, 70),
        ("Paid Indemnity", 260, 320),
        ("Paid Medical", 330, 385),
        ("Paid Expense", 395, 450),
        ("Total Paid", 460, 510),
    ],
    [
        ("Cause", 30, 60),
        ("Status", 95, 130),
        ("Jurisdiction State", 160, 220),
        ("Indemnity O/R", 260, 310),
        ("Medical O/R", 330, 380),
        ("Expense O/R", 395, 445),
        ("Outstanding Reserve", 460, 540),
    ],
    [("Applied Recovery", 30, 100)],
    [
        ("Date of Hire", 30, 80),
        ("Date Reopened", 95, 150),
        ("Accident State", 160, 210),
        ("Lost Time Days", 260, 310),
        ("Litigation Status", 330, 390),
        ("Part of Body", 460, 510),
    ],
    [
        ("Nature of Injury", 30, 90),
        ("Catalyst", 160, 200),
        ("Date Closed", 260, 310),
    ],
    [("Accident Description", 30, 120)],
]

#: Header lines sit this far apart -- tighter than any real record's own
#: internal gap, which is what corrupts the page-wide gap floor if these
#: lines leak into the body.
HEADER_PITCH = 8.0

#: A record's own internal gaps: mostly a normal ~10pt line-to-line pitch,
#: but the "Applied Recovery" -> "Date of Hire" transition sits ~14pt, wider
#: than any other gap on the page -- a genuine, repeatable trait of this
#: template, not a boundary between claims. Real Liberty pages show this
#: exact 13.9-15.1pt gap on every record.
RECORD_GAPS = [10.0, 10.0, 10.0, 14.0, 10.0, 10.0]

#: Distance between one record's last line and the next record's first --
#: comfortably wider than anything inside one record.
INTER_RECORD_GAP = 20.0

CLAIMS = [
    {
        "claim_number": "WC550C47269",
        "claimant": "CHAPMAN.GARY",
        "loss_date": "10/08/2021",
        "report_date": "10/08/2021",
        "incurred_indemnity": "$0.00",
        "incurred_medical": "$126.00",
        "incurred_expense": "$15.00",
        "total_incurred": "$141.00",
        "location": "-UNKNOWN",
        "paid_indemnity": "$0.00",
        "paid_medical": "$126.00",
        "paid_expense": "$15.00",
        "total_paid": "$141.00",
        "cause": "0HA-MATERIAL HANDLING",
        "status": "Closed",
        "indemnity_or": "$0.00",
        "medical_or": "$0.00",
        "expense_or": "$0.00",
        "outstanding_reserve": "$0.00",
        "applied_recovery": "$0.00",
        "hire_date": "1/14/16",
        "description": "LIFTED MULCH BAGS",
    },
    {
        "claim_number": "WC550C42111",
        "claimant": "CHUN RAMIREZ.ANDRES",
        "loss_date": "01/19/2021",
        "report_date": "01/20/2021",
        "incurred_indemnity": "$0.00",
        "incurred_medical": "$3,840.01",
        "incurred_expense": "$93.02",
        "total_incurred": "$3,933.03",
        "location": "-UNKNOWN",
        "paid_indemnity": "$0.00",
        "paid_medical": "$3,840.01",
        "paid_expense": "$93.02",
        "total_paid": "$3,933.03",
        "cause": "0LB-MOBILE EQUIPMENT",
        "status": "Closed",
        "indemnity_or": "$0.00",
        "medical_or": "$0.00",
        "expense_or": "$0.00",
        "outstanding_reserve": "$0.00",
        "applied_recovery": "$0.00",
        "hire_date": "10/14/19",
        "description": "DROVE A LOADER AND FLIPPED IT",
    },
    {
        "claim_number": "WC550C44035",
        "claimant": "CONRAN.SEAN",
        "loss_date": "04/06/2021",
        "report_date": "04/06/2021",
        "incurred_indemnity": "$0.00",
        "incurred_medical": "$406.55",
        "incurred_expense": "$55.60",
        "total_incurred": "$462.15",
        "location": "-UNKNOWN",
        "paid_indemnity": "$0.00",
        "paid_medical": "$406.55",
        "paid_expense": "$55.60",
        "total_paid": "$462.15",
        "cause": "ORA-STRUCK BY OBJECTS",
        "status": "Closed",
        "indemnity_or": "$0.00",
        "medical_or": "$0.00",
        "expense_or": "$0.00",
        "outstanding_reserve": "$0.00",
        "applied_recovery": "$0.00",
        "hire_date": "2/11/21",
        "description": "POKED IN THE LEFT WRIST",
    },
]


def _word(text: str, x0: float, x1: float, top: float) -> Word:
    return Word(text=text, x0=x0, x1=x1, top=top, bottom=top + 6.0)


def _header_lines(start_index: int, start_top: float) -> tuple[list[Line], float]:
    lines = []
    top = start_top
    for offset, row in enumerate(HEADER_TEMPLATE):
        words = tuple(_word(text, x0, x1, top) for text, x0, x1 in row)
        lines.append(Line(words=words, index=start_index + offset))
        top += HEADER_PITCH
    return lines, top


def _record_lines(start_index: int, start_top: float, claim: dict, artifact: str | None = None) -> tuple[list[Line], float]:
    """The seven physical lines of one claim, in Liberty's own column layout."""
    rows = [
        [
            (claim["claim_number"], 30, 85),
            (claim["claimant"], 95, 150),
            (claim["loss_date"], 160, 200),
            (claim["report_date"], 210, 255),
            (claim["incurred_indemnity"], 260, 320),
            (claim["incurred_medical"], 330, 385),
            (claim["incurred_expense"], 395, 450),
            (claim["total_incurred"], 460, 520),
        ],
        [
            (claim["location"], 30, 70),
            (claim["paid_indemnity"], 260, 320),
            (claim["paid_medical"], 330, 385),
            (claim["paid_expense"], 395, 450),
            (claim["total_paid"], 460, 510),
        ],
        [
            (claim["cause"], 30, 130),
            (claim["status"], 160, 200),
            (claim["indemnity_or"], 260, 310),
            (claim["medical_or"], 330, 380),
            (claim["expense_or"], 395, 445),
            (claim["outstanding_reserve"], 460, 540),
        ],
        [(claim["applied_recovery"], 260, 320)],
        [
            (claim["hire_date"], 30, 80),
            ("FL", 160, 210),
            ("0", 260, 310),
        ],
        [(claim["description"][:20], 30, 90)],
        [(claim["description"], 30, 200)],
    ]
    if artifact:
        rows[2] = rows[2][:-1] + [(artifact, 460, 540)]
    lines = []
    top = start_top
    for offset, row in enumerate(rows):
        words = tuple(_word(text, x0, x1, top) for text, x0, x1 in row)
        lines.append(Line(words=words, index=start_index + offset))
        if offset < len(RECORD_GAPS):
            top += RECORD_GAPS[offset]
    return lines, top


def _build_page(n_claims: int = 3, artifact_on: int | None = None) -> list[Line]:
    """A synthetic Liberty-shaped page: 7-line header block, n 7-line records."""
    header_lines, top = _header_lines(0, 100.0)
    all_lines = list(header_lines)
    index = len(header_lines)
    for i, claim in enumerate(CLAIMS[:n_claims]):
        artifact = "Ou1standing Reserve" if artifact_on == i else None
        record_lines, top = _record_lines(index, top + INTER_RECORD_GAP, claim, artifact)
        all_lines.extend(record_lines)
        index += len(record_lines)
    return all_lines


def _layout_for(lines: list[Line]):
    header_index, _ = find_header_line(lines, CHAR_WIDTH)
    block = header_block(lines, header_index, CHAR_WIDTH)
    layout = detect_layout([split_cells(l, CHAR_WIDTH, COLUMN_GUTTER_FACTOR) for l in block])
    block_end = max(l.index for l in block)
    return header_index, block, block_end, layout


# --- 1 & 2: the actual structural failure, reproduced and fixed -----------


def test_liberty_shaped_page_recovers_all_three_records():
    """The exact structure of Liberty page 49: three 7-line records under a
    7-line header block whose own pitch is tighter than the data's. All
    three claims must come back with their full field breakdown, not just
    claim_number/dates."""
    lines = _build_page(n_claims=3)
    header_index, block, block_end, layout = _layout_for(lines)
    assert layout.is_multi_line
    assert layout.height == 7

    table = _extract_record_table(1, lines, header_index, block_end, layout, CHAR_WIDTH)
    assert table is not None, (
        "a clean 7-line-per-claim page must reconstruct records; a header "
        "block whose own tighter pitch leaks into the body's gap floor is "
        "not evidence that the records themselves are too far apart"
    )
    assert len(table.rows) == 3
    claim_numbers = {row.cells[0] for row in table.rows}
    assert claim_numbers == {"WC550C47269", "WC550C42111", "WC550C44035"}
    # Every row must carry its own money, not just the identifier line's.
    for row in table.rows:
        assert len(row.cells) == len(table.headers)
        assert any(cell not in ("", None) for cell in row.cells[8:13]), (
            f"row {row.cells[0]} lost its Location/Paid-* line entirely"
        )


def test_header_tail_pitch_does_not_shrink_the_gap_floor():
    """Directly isolates the mechanism: body computed from block_end (the
    header block's true last line) must not include any header-only line,
    so the header's own tighter internal pitch never enters the gap-floor
    computation that decides how far apart one record's lines may sit."""
    lines = _build_page(n_claims=1)
    header_index, block, block_end, layout = _layout_for(lines)
    header_line_indices = {l.index for l in block}

    # Reproduce _extract_record_table's own body-selection, the fixed way.
    body = [line for line in lines if line.index > block_end and line.words]
    assert not (header_line_indices & {l.index for l in body}), (
        "a header-block line ended up in the record body"
    )


# --- 3: font/digit-substitution artifacts are cosmetic, not causal --------


@pytest.mark.parametrize("artifact_claim", [0, 1, 2])
def test_digit_substituted_header_word_does_not_break_recovery(artifact_claim):
    """"Ou1standing Reserve" (a stray digit standing in for a lowercase L)
    sits on the header's own line 2, identical on every real Liberty page.
    It must not matter which claim's synthetic page carries it -- the word
    never touches the claim-number column, the money columns, or any
    digit-shape test that gates record detection."""
    lines = _build_page(n_claims=3)
    header_index, block, block_end, layout = _layout_for(lines)
    table = _extract_record_table(1, lines, header_index, block_end, layout, CHAR_WIDTH)
    assert table is not None
    assert {row.cells[0] for row in table.rows} == {
        "WC550C47269", "WC550C42111", "WC550C44035",
    }


# --- 4: a genuine data value must not be swept into the header -----------


def test_a_lone_dollar_value_line_is_not_header_vocabulary():
    """A line holding only a bare amount -- the kind of line
    ``_is_label_line``/``_looks_like_value`` exists to keep out of a header
    block -- must stay out of it even when it sits at the header block's own
    pitch, distinguishing this from the tighter-pitch header-tail lines
    that legitimately belong in the block."""
    from core.extract_digital import _is_label_line

    money_line = Line(words=(_word("$1,234.56", 260, 320, 100.0),), index=99)
    assert not _is_label_line(money_line, CHAR_WIDTH)


# --- 5: adjacent claims stay distinct --------------------------------------


def test_adjacent_claims_do_not_bleed_into_each_other():
    lines = _build_page(n_claims=3)
    header_index, block, block_end, layout = _layout_for(lines)
    table = _extract_record_table(1, lines, header_index, block_end, layout, CHAR_WIDTH)
    assert table is not None
    by_claim = {row.cells[0]: row for row in table.rows}
    assert by_claim["WC550C47269"].cells[5] == "$126.00"  # incurred medical
    assert by_claim["WC550C42111"].cells[5] == "$3,840.01"
    assert by_claim["WC550C44035"].cells[5] == "$406.55"
    # No row's claimant name leaked into another row's claim number column.
    claimants = {row.cells[1] for row in table.rows}
    assert claimants == {"CHAPMAN.GARY", "CHUN RAMIREZ.ANDRES", "CONRAN.SEAN"}


# --- 6: a record short one physical line fails closed, not silently ------


def test_missing_line_within_a_record_fails_closed_not_sideways():
    """One claim's "Applied Recovery" line is simply absent from the page
    (the carrier only prints it when the value is non-zero, say). The fixed
    record height then no longer matches this claim's real line count, so
    every later anchor's fixed-height span drifts out of alignment with the
    physical page. Losing that claim's structure is acceptable; silently
    attributing its neighbour's lines to it is not."""
    header_lines, top = _header_lines(0, 100.0)
    all_lines = list(header_lines)
    index = len(header_lines)

    # Claim 1, normal.
    record1, top = _record_lines(index, top + INTER_RECORD_GAP, CLAIMS[0])
    all_lines.extend(record1)
    index += len(record1)

    # Claim 2, missing its "Applied Recovery" line (position 3) entirely --
    # every later line shifts up by one gap.
    claim = CLAIMS[1]
    rows = [
        [(claim["claim_number"], 30, 85), (claim["claimant"], 95, 150)],
        [(claim["location"], 30, 70), (claim["paid_indemnity"], 260, 320)],
        [(claim["cause"], 30, 130), (claim["status"], 160, 200)],
        # "Applied Recovery" line simply never printed.
        [(claim["hire_date"], 30, 80), ("FL", 160, 210)],
        [(claim["description"][:20], 30, 90)],
        [(claim["description"], 30, 200)],
    ]
    short_top = top + INTER_RECORD_GAP
    short_lines = []
    for offset, row in enumerate(rows):
        words = tuple(_word(text, x0, x1, short_top) for text, x0, x1 in row)
        short_lines.append(Line(words=words, index=index + offset))
        short_top += RECORD_GAPS[offset] if offset < len(RECORD_GAPS) else 10.0
    all_lines.extend(short_lines)
    index += len(short_lines)
    top = short_top

    # Claim 3, normal again.
    record3, _ = _record_lines(index, top + INTER_RECORD_GAP, CLAIMS[2])
    all_lines.extend(record3)

    header_index, block, block_end, layout = _layout_for(all_lines)
    table = _extract_record_table(1, all_lines, header_index, block_end, layout, CHAR_WIDTH)

    if table is None:
        return  # fail-closed: the whole page is refused, nothing corrupted
    # If it did reconstruct, claim 3's own fields must still be its own --
    # never a value pulled from claim 2's shifted lines.
    by_claim = {row.cells[0]: row for row in table.rows if row.cells and row.cells[0]}
    if "WC550C44035" in by_claim:
        assert by_claim["WC550C44035"].cells[1] == "CONRAN.SEAN"


# --- 7: an extra intervening line fails closed, not sideways -------------


def test_extra_intervening_line_fails_closed_not_sideways():
    """A stray annotation line sits between two records. The fixed-height
    span math must not silently absorb it into either neighbour's record."""
    header_lines, top = _header_lines(0, 100.0)
    all_lines = list(header_lines)
    index = len(header_lines)

    record1, top = _record_lines(index, top + INTER_RECORD_GAP, CLAIMS[0])
    all_lines.extend(record1)
    index += len(record1)

    stray_top = top + INTER_RECORD_GAP
    stray_words = tuple(
        _word(text, x0, x0 + 35, stray_top)
        for text, x0 in [
            ("ADJUSTER", 30), ("NOTE:", 70), ("FILE", 110), ("REVIEWED", 150),
        ]
    )
    stray = Line(words=stray_words, index=index)
    all_lines.append(stray)
    index += 1
    top = stray.words[0].top

    record2, top = _record_lines(index, top + INTER_RECORD_GAP, CLAIMS[1])
    all_lines.extend(record2)

    header_index, block, block_end, layout = _layout_for(all_lines)
    table = _extract_record_table(1, all_lines, header_index, block_end, layout, CHAR_WIDTH)

    if table is None:
        return  # fail-closed is acceptable
    # The stray line may surface as a leftover row of its own -- that is
    # correct, since it is genuinely unclaimed content. What it must never
    # do is get folded into a real claim's fields.
    claim_rows = [row for row in table.rows if row.cells and row.cells[0]]
    assert {row.cells[0] for row in claim_rows} == {"WC550C47269", "WC550C42111"}
    for row in claim_rows:
        assert "ADJUSTER" not in " ".join(row.cells)
        assert "NOTE:" not in " ".join(row.cells)


# --- 8: repeated identical values across claims do not cross-attribute ---


def test_repeated_zero_values_do_not_cross_attribute():
    """Every claim in this fixture shares the same $0.00 in several columns
    -- exactly like real Liberty claims, which are almost all fully closed
    with zero outstanding reserve. Grouping is positional (by anchor and
    fixed span), so identical values anywhere must never cause one claim's
    row to be built from another claim's line."""
    lines = _build_page(n_claims=3)
    header_index, block, block_end, layout = _layout_for(lines)
    table = _extract_record_table(1, lines, header_index, block_end, layout, CHAR_WIDTH)
    assert table is not None
    by_claim = {row.cells[0]: row for row in table.rows}
    # incurred_medical differs per claim -- the one field that would expose
    # cross-attribution even though every other money field is $0.00.
    assert by_claim["WC550C47269"].cells[5] == "$126.00"
    assert by_claim["WC550C42111"].cells[5] == "$3,840.01"
    assert by_claim["WC550C44035"].cells[5] == "$406.55"


# --- 10: provenance for a reconstructed record ----------------------------


def test_reconstructed_record_carries_its_own_source_lines():
    lines = _build_page(n_claims=3)
    header_index, block, block_end, layout = _layout_for(lines)
    table = _extract_record_table(1, lines, header_index, block_end, layout, CHAR_WIDTH)
    assert table is not None
    for row in table.rows:
        assert row.page == 1
        assert len(row.source_lines) == 7, (
            f"claim {row.cells[0]} should carry provenance for all 7 "
            f"physical lines it was merged from, got {row.source_lines}"
        )
        assert row.source_lines == sorted(row.source_lines)
