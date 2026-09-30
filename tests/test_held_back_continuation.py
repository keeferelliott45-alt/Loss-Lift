"""A continuation line held back by a run boundary is kept whole.

Found checking the corpus gate on PR #9 (via PR #19): on main, text lines on
pages after a report's last claim were folded into that claim's description.
PR #9 folds a line only into a claim of its own run, so those lines became
skipped-row warnings -- and a skipped row's warning keeps only a 60-character
preview. On one real document 61 of the 169 words that left the description
were then kept nowhere. The structure is reproduced synthetically; nothing
from the document is committed.

A line held back only because the claim above was read in another run is
recorded with its whole text. Every other skipped row keeps its preview
exactly as before.
"""

from __future__ import annotations

from core.pipeline import ColumnMapping, build_claims
from core.schema import RawRow, RawTable

HEADERS = ["Claim No", "Date of Loss", "Status", "Loss Description", "Paid Total",
           "Incurred Total"]
MAPPING = ColumnMapping(
    headers=HEADERS,
    fields={0: "claim_number", 1: "date_of_loss", 2: "claim_status",
            3: "loss_description", 4: "paid_total", 5: "incurred_total"},
)
LONG = ("Claimant advised of settlement terms and release forms mailed to "
        "defence counsel for review on receipt of the signed documents")
CLAIM = ["CN-1001", "03/12/2024", "OPEN", "Ladder fall", "1,200.00", "5,000.00"]


def _table(page, *rows):
    return RawTable(page=page, headers=HEADERS, strategy="words", total_rows=[],
                    rows=[RawRow(cells=list(r), page=page, line_index=i)
                          for i, r in enumerate(rows, 1)])


def test_a_line_held_back_by_a_run_boundary_is_kept_whole():
    claims, warnings, _ = build_claims(
        [_table(1, CLAIM), _table(2, ["", "", "", LONG, "", ""])],
        MAPPING, "us", "mdy", runs={1: 0, 2: 1},
    )
    assert claims[0].loss_description == "Ladder fall"
    assert len(warnings) == 1 and LONG in warnings[0], warnings


def test_the_same_line_within_one_report_still_folds():
    claims, warnings, _ = build_claims(
        [_table(1, CLAIM), _table(2, ["", "", "", LONG, "", ""])],
        MAPPING, "us", "mdy",
    )
    assert claims[0].loss_description == f"Ladder fall {LONG}"
    assert warnings == []


def test_an_ordinary_skipped_row_keeps_its_preview():
    """No claim above it to have joined: the warning is worded as on main."""
    _claims, warnings, _ = build_claims(
        [_table(1, ["", "", "", LONG, "", ""], CLAIM)], MAPPING, "us", "mdy",
    )
    assert warnings == [
        f"Page 1: skipped a row with no claim number ({LONG[:60]})."
    ]
