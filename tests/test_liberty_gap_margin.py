"""Liberty multi-line record failures that survive the 92a56af slicing fix.

On these pages the header block and body slice are already correct; what
rejects them is ``core.records.group_records``'s gap check, which derives
one page-wide floor (``max(min(gaps), 1.0) * MAX_INTRA_GAP_RATIO``) and
applies it to every record's internal line-to-line gaps. Measurement of
the real document separates three unrelated reasons a page can trip it:

* **Position 40 - a floating-point comparison artifact.** The rejecting
  gap exceeds the limit by 2.8e-14. Both sides are floating-point
  differences of PDF coordinates, so at that margin the comparison's
  direction reflects rounding rather than any spacing the document is
  expressing. This says nothing about the page's true geometry, and no
  claim is made that the underlying coordinates are exactly equal.
  Addressed by ``GAP_COMPARISON_TOLERANCE``.

* **Positions 41 and 48 - non-claim content setting the floor.** Contract
  metadata above the first claim (48) or a repeated totals block below it
  (41) is printed at a tighter pitch than claim content, so it, not the
  claims, decides the limit. Excluding gaps that touch an
  ``_ends_a_record``-flagged line resolves 48. It does not resolve 41,
  whose totals block continues onto un-flagged bare-value rows.

* **Positions 45 and 51 - real spacing differences.** 45's three claims
  each show a 14.6-15.1pt internal gap against a ~14.1-14.4pt norm; 51
  exceeds its limit by 0.0236 with the floor coming from ordinary,
  non-boundary claim content. Both are real geometry, neither candidate
  addresses them, and both remain open.

Position 44 is worth distinguishing from position 40: it *passes*, by
0.00745. That is a real, if small, margin - not the same phenomenon as
40's rounding-scale one, and not evidence for it. It is also the smallest
genuine margin in the corpus, which is what the 1e-6 tolerance must stay
clear of; the tolerance is a practical one, sized from the rounding noise
observed against that headroom, not a derived error bound.

Fixture geometry is measured from the real pages and any simplification is
stated in the fixture's own docstring. Claim content is synthetic per spec
section 9.
"""

from __future__ import annotations

import pytest

from core.extract_digital import (
    COLUMN_GUTTER_FACTOR,
    Line,
    Word,
    _ends_a_record,
    _extract_record_table,
    find_header_line,
    header_block,
    split_cells,
)
from core.records import MAX_INTRA_GAP_RATIO, detect_layout, is_identifier_candidate
from tests.test_liberty_record_recovery import (
    CHAR_WIDTH,
    CLAIMS,
    HEADER_PITCH,
    HEADER_TEMPLATE,
    INTER_RECORD_GAP,
    _header_lines,
    _layout_for,
    _word,
)

def _record(start_index: int, start_top: float, claim: dict, gaps: list[float]) -> tuple[list[Line], float]:
    """One claim's seven physical lines, with an explicit, caller-chosen
    sequence of six inter-line gaps -- unlike test_liberty_record_recovery's
    fixed RECORD_GAPS, so each scenario here can reproduce its own measured
    geometry rather than one universal shape.

    Claim content (claim number, claimant, dates, money) always comes from
    the shared, real-page-49-sourced ``CLAIMS`` fixtures in
    test_liberty_record_recovery -- never invented here -- even where the
    geometry being reproduced is measured from a *different* real page
    (40/41/45/48/51). That is a labelled simplification, not a hidden one:
    each scenario below states plainly that it reproduces that other
    page's geometry with page 49's claim text, not page 40/41/45/48/51's
    own claim numbers and claimants, which were not re-keyed into fixture
    form for this file.
    """
    rows = [
        [
            (claim["claim_number"], 30, 85), (claim["claimant"], 95, 150),
            (claim["loss_date"], 160, 200), (claim["report_date"], 210, 255),
            (claim["incurred_indemnity"], 260, 320), (claim["incurred_medical"], 330, 385),
            (claim["incurred_expense"], 395, 450), (claim["total_incurred"], 460, 520),
        ],
        [
            (claim["location"], 30, 70), (claim["paid_indemnity"], 260, 320),
            (claim["paid_medical"], 330, 385), (claim["paid_expense"], 395, 450),
            (claim["total_paid"], 460, 510),
        ],
        [
            (claim["cause"], 30, 130), (claim["status"], 160, 200),
            (claim["indemnity_or"], 260, 310), (claim["medical_or"], 330, 380),
            (claim["expense_or"], 395, 445), (claim["outstanding_reserve"], 460, 540),
        ],
        [(claim["applied_recovery"], 260, 320)],
        [(claim["hire_date"], 30, 80), ("FL", 160, 210), ("0", 260, 310)],
        [(claim["description"][:20], 30, 90)],
        [(claim["description"], 30, 200)],
    ]
    lines = []
    top = start_top
    for offset, row in enumerate(rows):
        words = tuple(_word(text, x0, x1, top) for text, x0, x1 in row)
        lines.append(Line(words=words, index=start_index + offset))
        if offset < len(gaps):
            top += gaps[offset]
    return lines, top


def _extract(all_lines: list[Line]):
    header_index, block, block_end, layout = _layout_for(all_lines)
    return _extract_record_table(1, all_lines, header_index, block_end, layout, CHAR_WIDTH)


def _claim_rows(table):
    """Rows that carry a real claim number -- excludes leftover/unclaimed
    rows (a totals line, a stray annotation) that legitimately surface as
    rows of their own without belonging to any claim.

    Filters on ``is_identifier_candidate`` -- the same shape test
    production code itself uses to decide what counts as a claim number
    -- rather than "cell 0 is non-empty". A naive non-empty check is not
    enough: a leftover totals-block line can land label text like "Claim
    Count" in the claim-number column position, which is non-empty but
    is not, and must not be mistaken for, a claim identifier.
    """
    return {
        row.cells[0]: row
        for row in table.rows
        if row.cells and row.cells[0] and is_identifier_candidate(row.cells[0])
    }


# --- PDF position 40: a floating-point comparison artifact -----------------
#
# RESOLVED by Candidate 1 (GAP_COMPARISON_TOLERANCE in core/records.py).
# Verified independently, before Candidate 2 existed: with only the
# tolerance applied, this page (and no other of the ten record-shaped
# pages checked) flips from FAIL to OK. The two tests below replace the
# original currently-fails/xfail pair now that the behavior they were
# tracking has changed -- kept as a single always-must-pass test, per
# instruction not to leave a stale None-check in place once fixed.
#
# Simplification, stated plainly: the real page's own claim numbers
# (WC550C44573, WC550C46497, WC550C47293) are not reused here -- the first
# two of the shared CLAIMS fixtures stand in for them, since only the
# *geometry* is under test. The engineered nudge (limit + 1e-13) is not
# claimed to equal the real page's exact 2.842170943040401e-14 margin --
# it is chosen to be the same order of magnitude, which is all the
# mechanism being demonstrated requires.


def test_position_40_shaped_boundary_case_reconstructs():
    header_lines, top = _header_lines(0, 100.0)
    all_lines = list(header_lines)
    index = len(header_lines)
    normal_gaps = [10.0, 10.0, 10.0, 14.0, 10.0, 10.0]
    for claim in CLAIMS[:2]:
        rec, top = _record(index, top + INTER_RECORD_GAP, claim, normal_gaps)
        all_lines.extend(rec)
        index += len(rec)
    min_gap = 10.0
    limit = max(min_gap, 1.0) * MAX_INTRA_GAP_RATIO
    boundary_gaps = [10.0, 10.0, 10.0, limit + 1e-13, 10.0, 10.0]
    rec3, _ = _record(index, top + INTER_RECORD_GAP, CLAIMS[2], boundary_gaps)
    all_lines.extend(rec3)

    table = _extract(all_lines)
    assert table is not None
    by_claim = _claim_rows(table)
    assert set(by_claim) == {c["claim_number"] for c in CLAIMS[:3]}
    assert by_claim[CLAIMS[2]["claim_number"]].cells[1] == CLAIMS[2]["claimant"]
    assert by_claim[CLAIMS[2]["claim_number"]].cells[10] == CLAIMS[2]["paid_medical"]
    assert by_claim[CLAIMS[2]["claim_number"]].page == 1
    assert len(by_claim[CLAIMS[2]["claim_number"]].source_lines) == 7


def test_position_40_shaped_case_far_past_tolerance_still_fails_closed():
    """The tolerance is 1e-6 -- a margin of, say, 0.01 past the limit must
    still reject. This is the negative control: the tolerance must not
    have been implemented as (or accidentally become) something that
    swallows real differences, only rounding noise."""
    header_lines, top = _header_lines(0, 100.0)
    all_lines = list(header_lines)
    index = len(header_lines)
    normal_gaps = [10.0, 10.0, 10.0, 14.0, 10.0, 10.0]
    for claim in CLAIMS[:2]:
        rec, top = _record(index, top + INTER_RECORD_GAP, claim, normal_gaps)
        all_lines.extend(rec)
        index += len(rec)
    min_gap = 10.0
    limit = max(min_gap, 1.0) * MAX_INTRA_GAP_RATIO
    boundary_gaps = [10.0, 10.0, 10.0, limit + 0.01, 10.0, 10.0]
    rec3, _ = _record(index, top + INTER_RECORD_GAP, CLAIMS[2], boundary_gaps)
    all_lines.extend(rec3)

    table = _extract(all_lines)
    assert table is None


# --- PDF position 41: non-claim content corrupting the gap floor ----------
#
# Fidelity, verified directly (not assumed): the five totals-block lines
# below reproduce the real page's own text and relative offsets (23.0,
# 9.4, 8.6, 9.4, 8.6 vs. the measured 23.28, 9.35, 8.65, 9.35, 8.65 --
# rounded for readability, not altered in kind), and were checked against
# the actual `_ends_a_record` function: the "Total for..." and "Claim
# Count:..." lines come back `is_boundary=True`, their bare-value
# continuation rows `False` -- matching the real page exactly. The real
# page in fact carries a second, near-identical totals block immediately
# after this one ("Total for Contract Effective Date:..."); only one is
# reproduced here, since one is already sufficient to pull the minimum
# gap down to the real page's own ~8.6-8.7pt range.


def test_position_41_shaped_totals_block_currently_fails():
    header_lines, top = _header_lines(0, 100.0)
    all_lines = list(header_lines)
    index = len(header_lines)

    rec, top = _record(index, top + INTER_RECORD_GAP, CLAIMS[0], [10.0, 10.0, 10.0, 14.0, 10.0, 10.0])
    all_lines.extend(rec)
    index += len(rec)

    totals_rows = [
        (top + 23.0, [("Total", 30, 60), ("for", 65, 80), ("Contract", 85, 130),
                       ("Number:", 135, 180), ("WCCZ9147233202", 185, 260)]),
        (top + 32.4, [("Claim", 30, 60), ("Count", 65, 100), (":", 105, 110),
                       ("28", 115, 135), ("$25,297.00", 260, 320), ("$37,438.75", 330, 385)]),
        (top + 41.0, [("$11,312.60", 260, 320), ("$25,173.30", 330, 385)]),
        (top + 50.4, [("$13,984.40", 260, 320), ("$12,265.45", 330, 385)]),
        (top + 59.0, [("$-3,500.00", 260, 320)]),
    ]
    for line_top, words in totals_rows:
        all_lines.append(Line(
            words=tuple(_word(t, x0, x1, line_top) for t, x0, x1 in words), index=index,
        ))
        index += 1

    table = _extract(all_lines)
    assert table is None, (
        "if this starts passing, the totals-block contamination has been "
        "addressed -- see the xfail'd companion test for the intended "
        "positive assertion"
    )


@pytest.mark.xfail(reason="intended correct behavior; not yet implemented (see investigation report)", strict=True)
def test_position_41_shaped_totals_block_should_not_block_the_real_claim():
    header_lines, top = _header_lines(0, 100.0)
    all_lines = list(header_lines)
    index = len(header_lines)
    rec, top = _record(index, top + INTER_RECORD_GAP, CLAIMS[0], [10.0, 10.0, 10.0, 14.0, 10.0, 10.0])
    all_lines.extend(rec)
    index += len(rec)
    totals_rows = [
        (top + 23.0, [("Total", 30, 60), ("for", 65, 80), ("Contract", 85, 130),
                       ("Number:", 135, 180), ("WCCZ9147233202", 185, 260)]),
        (top + 32.4, [("Claim", 30, 60), ("Count", 65, 100), (":", 105, 110),
                       ("28", 115, 135), ("$25,297.00", 260, 320), ("$37,438.75", 330, 385)]),
        (top + 41.0, [("$11,312.60", 260, 320), ("$25,173.30", 330, 385)]),
        (top + 50.4, [("$13,984.40", 260, 320), ("$12,265.45", 330, 385)]),
        (top + 59.0, [("$-3,500.00", 260, 320)]),
    ]
    for line_top, words in totals_rows:
        all_lines.append(Line(words=tuple(_word(t, x0, x1, line_top) for t, x0, x1 in words), index=index))
        index += 1

    table = _extract(all_lines)
    assert table is not None
    by_claim = _claim_rows(table)
    assert set(by_claim) == {CLAIMS[0]["claim_number"]}
    assert by_claim[CLAIMS[0]["claim_number"]].cells[1] == CLAIMS[0]["claimant"]
    assert by_claim[CLAIMS[0]["claim_number"]].cells[10] == CLAIMS[0]["paid_medical"]
    # The totals block's own values must never land on the claim's row.
    assert "$25,297.00" not in by_claim[CLAIMS[0]["claim_number"]].cells
    assert "28" not in by_claim


# --- PDF position 45: genuinely wider record-internal spacing -------------
#
# Simplification, stated plainly: the real page's three claims show an
# *increasing* gap across the page (14.64, 14.89, 15.11 -- not a flat
# value). This fixture applies one representative value (14.6, matching
# the smallest of the three, i.e. the most conservative choice) to all
# three claims uniformly, understating the true margin on the real page's
# 2nd and 3rd claims rather than overstating it. The "normal" gaps (9.6)
# match the real page's own measured minimum (9.6013524) to 3 significant
# figures.


def test_position_45_shaped_wider_spacing_currently_fails():
    header_lines, top = _header_lines(0, 100.0)
    all_lines = list(header_lines)
    index = len(header_lines)
    wide_gaps = [9.6, 9.6, 9.6, 14.6, 9.6, 9.6]
    for claim in CLAIMS:
        rec, top = _record(index, top + INTER_RECORD_GAP, claim, wide_gaps)
        all_lines.extend(rec)
        index += len(rec)

    table = _extract(all_lines)
    assert table is None, (
        "if this starts passing, the wider-spacing case has been "
        "addressed -- see the xfail'd companion test"
    )


@pytest.mark.xfail(reason="intended correct behavior; not yet implemented (see investigation report)", strict=True)
def test_position_45_shaped_wider_spacing_should_reconstruct():
    header_lines, top = _header_lines(0, 100.0)
    all_lines = list(header_lines)
    index = len(header_lines)
    wide_gaps = [9.6, 9.6, 9.6, 14.6, 9.6, 9.6]
    for claim in CLAIMS:
        rec, top = _record(index, top + INTER_RECORD_GAP, claim, wide_gaps)
        all_lines.extend(rec)
        index += len(rec)

    table = _extract(all_lines)
    assert table is not None
    by_claim = _claim_rows(table)
    assert set(by_claim) == {c["claim_number"] for c in CLAIMS}
    for claim in CLAIMS:
        assert by_claim[claim["claim_number"]].cells[1] == claim["claimant"]


# --- PDF position 48: non-claim content ABOVE the first claim -------------
#
# This is the fixture that exercises boundary-gap filtering. It only does
# that if its geometry is faithful, and an earlier version of it was not:
# it spaced the two metadata lines 9.84pt apart, which puts the page's
# unfiltered floor at 9.84 and its limit at 14.76 -- already above the
# 14.40 internal gap the fixture then used, so the record reconstructed
# with no filtering at all and the test passed identically on unmodified
# 92a56af. It asserted the right things about a page that never posed the
# question.
#
# The vertical geometry below is measured from the real page instead of
# chosen, and reproduces its whole body rhythm rather than one number:
#
#   metadata pair gap                       9.589879   <- the page floor
#   metadata -> first claim anchor          9.697100
#   claim 1 internal   9.8394 10.0750 10.0874 14.1606 10.0688 10.3312
#   inter-record                           22.551400
#   claim 2 internal   9.8270  9.8394 10.3292 14.1712 10.0812 10.3106
#   inter-record                           22.795200
#   claim 3 internal   9.8332  9.8394 10.3292 14.4044 10.0812 10.3292
#
# which yields, exactly as on the real page:
#
#   unfiltered floor 9.589879 -> limit 14.384819  (from the metadata pair)
#   filtered   floor 9.827000 -> limit 14.740500  (metadata gaps dropped)
#   claim 3's widest internal gap                 14.404400
#
# 14.404400 sits between the two limits, which is what makes this test
# discriminate: it fails without boundary filtering and passes with it.
# The margin over the unfiltered limit is 0.0196 -- four orders of
# magnitude above the numerical tolerance, so the tolerance alone cannot
# rescue it either.
#
# Horizontal geometry: the metadata lines' word x-positions are the real
# page's own, because their spacing decides whether ``split_cells`` merges
# them into one cell ("Contract Effective Date: 10/13/20") and therefore
# whether ``_ends_a_record``'s colon test fires at all. A previous version
# spaced these words 5pt apart, they stayed separate cells, and the lines
# were silently not boundary-flagged.
#
# Claim content is synthetic (spec section 9); only the geometry is taken
# from the real document.

#: Distinct paid_medical per claim, so a mis-assignment between claims
#: cannot pass unnoticed.
SYNTHETIC_48_CLAIMS = [
    {
        "claim_number": "SYN0000481", "claimant": "ALVARADO,ROSA",
        "loss_date": "07/16/2021", "report_date": "07/16/2021",
        "incurred_indemnity": "$0.00", "incurred_medical": "$2,952.24",
        "incurred_expense": "$91.94", "total_incurred": "$3,044.18",
        "location": "-UNKNOWN", "paid_indemnity": "$0.00",
        "paid_medical": "$2,952.24", "paid_expense": "$91.94",
        "total_paid": "$3,044.18", "cause": "0LA-MATERIAL HANDLING",
        "status": "Closed", "indemnity_or": "$0.00", "medical_or": "$0.00",
        "expense_or": "$0.00", "outstanding_reserve": "$0.00",
        "applied_recovery": "$0.00", "hire_date": "11/23/20",
        "description": "CUT KNEE USING TRIMMER",
    },
    {
        "claim_number": "SYN0000482", "claimant": "BENNETT,PAULA",
        "loss_date": "11/05/2020", "report_date": "11/05/2020",
        "incurred_indemnity": "$0.00", "incurred_medical": "$141.72",
        "incurred_expense": "$34.65", "total_incurred": "$176.37",
        "location": "-UNKNOWN", "paid_indemnity": "$0.00",
        "paid_medical": "$141.72", "paid_expense": "$34.65",
        "total_paid": "$176.37", "cause": "0LA-MATERIAL HANDLING",
        "status": "Closed", "indemnity_or": "$0.00", "medical_or": "$0.00",
        "expense_or": "$0.00", "outstanding_reserve": "$0.00",
        "applied_recovery": "$0.00", "hire_date": "8/17/20",
        "description": "CUT FINGER WITH TRIMMER",
    },
    {
        "claim_number": "SYN0000483", "claimant": "CHANDLER,MAE",
        "loss_date": "07/01/2021", "report_date": "07/01/2021",
        "incurred_indemnity": "$0.00", "incurred_medical": "$107.10",
        "incurred_expense": "$15.00", "total_incurred": "$122.10",
        "location": "-UNKNOWN", "paid_indemnity": "$0.00",
        "paid_medical": "$107.10", "paid_expense": "$15.00",
        "total_paid": "$122.10", "cause": "0WW-INJURED BY INSECT",
        "status": "Closed", "indemnity_or": "$0.00", "medical_or": "$0.00",
        "expense_or": "$0.00", "outstanding_reserve": "$0.00",
        "applied_recovery": "$0.00", "hire_date": "12/14/20",
        "description": "STUNG WHILE CUTTING TREES",
    },
]

#: Measured from the real page; see the table above.
_P48_METADATA_GAP = 9.589879
_P48_METADATA_TO_ANCHOR = 9.697100
_P48_INTERNAL_GAPS = [
    [9.8394, 10.0750, 10.0874, 14.1606, 10.0688, 10.3312],
    [9.8270, 9.8394, 10.3292, 14.1712, 10.0812, 10.3106],
    [9.8332, 9.8394, 10.3292, 14.4044, 10.0812, 10.3292],
]
_P48_INTER_RECORD_GAPS = [22.5514, 22.7952]


def _position_48_metadata(top: float, index: int) -> tuple[list[Line], float, int]:
    """The two contract-metadata lines the real page prints above its first
    claim, at their real word positions and their real 9.589879pt spacing."""
    rows = [
        (top, [("Contract", 44.56, 71.64), ("Effective", 73.55, 100.44),
               ("Date:", 102.59, 118.42), ("10/13/20", 120.82, 146.01)]),
        (top + _P48_METADATA_GAP,
         [("Contract", 44.56, 71.88), ("Number:", 73.78, 100.00),
          ("WCCZ9147233202", 102.24, 157.46), ("-", 159.79, 161.89),
          ("Impact", 163.80, 184.70), ("Landscaping", 186.58, 226.19)]),
    ]
    lines = []
    for line_top, words in rows:
        lines.append(Line(words=tuple(_word(t, x0, x1, line_top) for t, x0, x1 in words), index=index))
        index += 1
    return lines, rows[-1][0], index


def _build_position_48_page() -> tuple[list[Line], list[Line], dict[str, list[int]]]:
    """Returns (all lines, the two metadata lines, claim number -> the
    physical line indices that claim's record occupies)."""
    header_lines, top = _header_lines(0, 100.0)
    all_lines = list(header_lines)
    index = len(header_lines)

    metadata_lines, top, index = _position_48_metadata(top + 10.0, index)
    all_lines.extend(metadata_lines)

    expected_lines: dict[str, list[int]] = {}
    lead = _P48_METADATA_TO_ANCHOR
    for position, claim in enumerate(SYNTHETIC_48_CLAIMS):
        record, top = _record(index, top + lead, claim, _P48_INTERNAL_GAPS[position])
        all_lines.extend(record)
        expected_lines[claim["claim_number"]] = [line.index for line in record]
        index += len(record)
        if position < len(_P48_INTER_RECORD_GAPS):
            lead = _P48_INTER_RECORD_GAPS[position]
    return all_lines, metadata_lines, expected_lines


def test_position_48_metadata_lines_are_boundary_flagged():
    """The mechanism under test only engages on lines ``_ends_a_record``
    actually flags, so assert that directly rather than inferring it from
    the extraction succeeding."""
    metadata_lines, _, _ = _position_48_metadata(100.0, 0)
    for line in metadata_lines:
        cells = split_cells(line, CHAR_WIDTH, COLUMN_GUTTER_FACTOR)
        assert _ends_a_record(line, cells), (line.index, [c[0] for c in cells])


def test_position_48_geometry_discriminates_boundary_filtering():
    """Guards the property that made the previous fixture vacuous: the
    page's widest in-record gap must fall *between* the unfiltered and
    filtered limits, so that filtering is the only thing that can accept
    it. If a later edit moves any of these numbers, this fails before the
    reconstruction test starts passing for the wrong reason."""
    all_lines, _, _ = _build_position_48_page()
    header_index, _, block_end, layout = _layout_for(all_lines)
    body = [line for line in all_lines if line.index > block_end and line.words]
    cells = {line.index: split_cells(line, CHAR_WIDTH, COLUMN_GUTTER_FACTOR) for line in body}
    boundary = [_ends_a_record(line, cells[line.index]) for line in body]
    tops = [min(word.top for word in line.words) for line in body]
    gaps = [tops[i + 1] - tops[i] for i in range(len(tops) - 1)]
    filtered = [g for i, g in enumerate(gaps) if not boundary[i] and not boundary[i + 1]]

    unfiltered_limit = max(min(gaps), 1.0) * MAX_INTRA_GAP_RATIO
    filtered_limit = max(min(filtered), 1.0) * MAX_INTRA_GAP_RATIO
    widest_internal = max(_P48_INTERNAL_GAPS[2])

    assert unfiltered_limit < widest_internal < filtered_limit, (
        f"unfiltered={unfiltered_limit:.6f} widest={widest_internal:.6f} "
        f"filtered={filtered_limit:.6f}"
    )
    # And by a margin far larger than the numerical tolerance, so that the
    # tolerance alone cannot account for the difference.
    assert widest_internal - unfiltered_limit > 1e-3


def test_position_48_shaped_leading_metadata_reconstructs():
    all_lines, metadata_lines, expected_lines = _build_position_48_page()
    table = _extract(all_lines)
    assert table is not None, (
        "boundary-adjacent gaps must not set the floor for records that "
        "have nothing to do with them"
    )

    by_claim = _claim_rows(table)
    assert set(by_claim) == {c["claim_number"] for c in SYNTHETIC_48_CLAIMS}

    metadata_indices = {line.index for line in metadata_lines}
    for claim in SYNTHETIC_48_CLAIMS:
        row = by_claim[claim["claim_number"]]
        # Field assignment, including a value distinct per claim.
        assert row.cells[1] == claim["claimant"]
        assert row.cells[10] == claim["paid_medical"]
        assert row.cells[11] == claim["paid_expense"]
        # Exact source-line membership: its own seven lines, no others.
        assert row.source_lines == expected_lines[claim["claim_number"]]
        assert not metadata_indices & set(row.source_lines)
        # No neighbouring claim's distinguishing value leaked in.
        for other in SYNTHETIC_48_CLAIMS:
            if other is not claim:
                assert other["claimant"] not in row.cells
                assert other["claim_number"] not in row.cells
    # The metadata pair's own text never lands on a claim row.
    for row in by_claim.values():
        assert "10/13/20" not in row.cells
        assert "WCCZ9147233202" not in row.cells


# --- PDF position 51: small, real, unexplained jitter ----------------------
#
# Unlike 41/48, this page's minimum gap comes from ordinary claim content
# (`bnd=False` on both ends, verified against the real page), not a
# boundary line -- so neither candidate below is expected to help it. Its
# real minimum (9.581536) and real violating gap (14.395887, margin
# +0.0236) are reproduced to 4-5 significant figures.


def test_position_51_shaped_small_real_margin_currently_fails():
    header_lines, top = _header_lines(0, 100.0)
    all_lines = list(header_lines)
    index = len(header_lines)

    for claim, gaps in zip(
        CLAIMS,
        [[10.0, 10.0, 10.0, 14.0, 10.0, 10.0],
         [10.0, 9.5815, 10.0, 14.0, 10.0, 10.0],  # the page's own real minimum, from ordinary content
         [10.0, 10.0, 10.0, 14.3959, 10.0, 10.0]],  # the real violating gap
    ):
        rec, top = _record(index, top + INTER_RECORD_GAP, claim, gaps)
        all_lines.extend(rec)
        index += len(rec)

    table = _extract(all_lines)
    assert table is None, (
        "if this starts passing, something changed the gap-floor "
        "computation in a way this investigation did not anticipate -- "
        "re-examine before assuming it is fixed correctly, since neither "
        "measured candidate for the other pages was expected to reach "
        "this page's real, non-boundary, non-floating-point margin"
    )


# --- Boundary safety: must hold before, during, and after any fix --------


def test_a_wider_record_never_merges_with_its_neighbour():
    """Whatever mechanism eventually accommodates a wider internal gap, it
    must not do so by treating two adjacent claims as one record just
    because a large gap now falls inside the tolerance. The inter-record
    gap (~20pt+ on every real page measured) stays far wider than even
    position 45's widest internal gap (15.1pt) -- this fixture keeps that
    same ~5-6pt separation, and no accommodation of the *internal* gap
    should ever be generous enough to close it."""
    header_lines, top = _header_lines(0, 100.0)
    all_lines = list(header_lines)
    index = len(header_lines)
    wide_gaps = [9.6, 9.6, 9.6, 14.6, 9.6, 9.6]
    for claim in CLAIMS[:2]:
        rec, top = _record(index, top + INTER_RECORD_GAP, claim, wide_gaps)
        all_lines.extend(rec)
        index += len(rec)

    table = _extract(all_lines)
    if table is None:
        return  # fail-closed is always an acceptable outcome
    by_claim = _claim_rows(table)
    assert set(by_claim) == {CLAIMS[0]["claim_number"], CLAIMS[1]["claim_number"]}
    assert by_claim[CLAIMS[0]["claim_number"]].cells[1] == CLAIMS[0]["claimant"]
    assert by_claim[CLAIMS[1]["claim_number"]].cells[1] == CLAIMS[1]["claimant"]
    assert by_claim[CLAIMS[0]["claim_number"]].cells[10] == CLAIMS[0]["paid_medical"]
    assert by_claim[CLAIMS[1]["claim_number"]].cells[10] == CLAIMS[1]["paid_medical"]


def test_a_totals_block_never_becomes_a_claim_row():
    """Regardless of how the gap floor is computed, a line already
    identified as ending a record (``_ends_a_record`` -- a "Label:" line,
    a claim-count line, a total line) must never itself be read as, or
    folded into, a claim row. This is the safety property any fix to the
    gap statistic must not weaken."""
    header_lines, top = _header_lines(0, 100.0)
    all_lines = list(header_lines)
    index = len(header_lines)
    rec, top = _record(index, top + INTER_RECORD_GAP, CLAIMS[0], [10.0, 10.0, 10.0, 14.0, 10.0, 10.0])
    all_lines.extend(rec)
    index += len(rec)
    label_line = Line(
        words=(
            _word("Claim", 30, 60, top + 23.0), _word("Count", 65, 100, top + 23.0),
            _word(":", 105, 110, top + 23.0), _word("28", 115, 135, top + 23.0),
        ),
        index=index,
    )
    all_lines.append(label_line)

    table = _extract(all_lines)
    if table is None:
        return
    claim_rows = _claim_rows(table)
    for row in claim_rows.values():
        assert "28" not in row.cells
        assert "Claim Count" not in " ".join(row.cells)


def test_valid_multi_claim_extraction_stays_correct_with_adjacent_totals_content():
    """A must-succeed case, not a permissive one: three ordinary claims
    (normal ~10pt/14pt rhythm, comfortably under today's limit already, so
    this must reconstruct under both unmodified and modified production
    code) followed by a totals block. Extraction must both succeed AND
    keep every claim correctly separated and provenance-attributed, with
    the totals content excluded from every claim row -- not merely "did
    not crash" or "row count looks right"."""
    header_lines, top = _header_lines(0, 100.0)
    all_lines = list(header_lines)
    index = len(header_lines)
    normal_gaps = [10.0, 10.0, 10.0, 14.0, 10.0, 10.0]
    for claim in CLAIMS:
        rec, top = _record(index, top + INTER_RECORD_GAP, claim, normal_gaps)
        all_lines.extend(rec)
        index += len(rec)
    totals_rows = [
        (top + 23.0, [("Total", 30, 60), ("for", 65, 80), ("Contract", 85, 130),
                       ("Number:", 135, 180), ("WCCZ9147233202", 185, 260)]),
        (top + 32.4, [("Claim", 30, 60), ("Count", 65, 100), (":", 105, 110),
                       ("28", 115, 135), ("$25,297.00", 260, 320), ("$37,438.75", 330, 385)]),
    ]
    for line_top, words in totals_rows:
        all_lines.append(Line(words=tuple(_word(t, x0, x1, line_top) for t, x0, x1 in words), index=index))
        index += 1

    table = _extract(all_lines)
    assert table is not None, "this shape is well within today's own tolerance and must already succeed"

    by_claim = _claim_rows(table)
    assert set(by_claim) == {c["claim_number"] for c in CLAIMS}

    for claim in CLAIMS:
        row = by_claim[claim["claim_number"]]
        # Field assignments: claimant and one distinguishing money field,
        # per claim -- not just "a row exists for this claim number".
        assert row.cells[1] == claim["claimant"]
        assert row.cells[10] == claim["paid_medical"]
        assert row.cells[11] == claim["paid_expense"]
        # Provenance: each claim's row points at its own seven physical
        # lines, page 1, in order -- not borrowed from a neighbour.
        assert row.page == 1
        assert len(row.source_lines) == 7
        assert row.source_lines == sorted(row.source_lines)

    # No claim's row absorbed anything from a neighbouring claim or from
    # the totals block below all three.
    assert by_claim[CLAIMS[0]["claim_number"]].cells[10] != by_claim[CLAIMS[1]["claim_number"]].cells[10] or (
        CLAIMS[0]["paid_medical"] == CLAIMS[1]["paid_medical"]
    )
    for row in by_claim.values():
        assert "$25,297.00" not in row.cells
        assert "28" not in row.cells
        assert "Claim Count" not in " ".join(row.cells)

    # The totals block's own source lines never got folded into any
    # claim's provenance.
    totals_line_indices = set(range(index - len(totals_rows), index))
    for row in by_claim.values():
        assert not (totals_line_indices & set(row.source_lines))
