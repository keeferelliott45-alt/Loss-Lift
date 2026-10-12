"""A claim-number vote that admits too many numbering series admits none.

``core.records.consensus_shapes`` admits every identifier shape that recurs
often enough, plus every recurring member of an admitted shape's family. The
floor is relative to the most common shape, so on a large table whose
identifier column holds many unrelated codes -- a register or parts list
under a "Claim Number" heading, not a loss run -- every shape clears it and
every row becomes a claim. If the table also prints a total that its rows
add up to, the over-extraction reads **reconciled**.

The guard: more than ``MAX_SERIES_FAMILIES`` numbering families means the
column holds no claim-number series. The vote then admits nothing, so no
claim is built. Every claim-like row is kept as a refused row (R-29), the
document reads NEEDS_REVIEW, and nothing is dropped or guessed. A loss run's
own few series, a series that grew a digit, and a large single-series run
are unaffected.

GATE-REQUIRED: this changes extraction. Synthetic documents only.
"""

from __future__ import annotations

import pymupdf

from core.pipeline import run_pipeline
from core.records import (
    MAX_SERIES_FAMILIES,
    NO_SERIES,
    consensus_shapes,
    leading_identifier,
    identifier_shape,
    series_families,
)
from core.review import canonical_status
from core.schema import DocumentStatus

#: Codes of unrelated shapes, as a register's reference column prints them.
FAMILIES = (
    lambda n: f"AB{n:04d}",
    lambda n: f"{n:03d}-XY",
    lambda n: f"K{n:02d}-{n:03d}",
    lambda n: f"{n:05d}",
    lambda n: f"Q-{n:03d}-R",
    lambda n: f"{n:02d}ZZ{n:02d}",
)


def _codes(families: int, each: int) -> list[str]:
    return [FAMILIES[f](100 + n) for f in range(families) for n in range(each)]


# --- the vote ----------------------------------------------------------------


def test_a_column_of_many_unrelated_series_admits_none():
    codes = _codes(len(FAMILIES), 6)
    assert len(FAMILIES) > MAX_SERIES_FAMILIES
    vote = consensus_shapes(codes)
    assert vote == {NO_SERIES}
    assert all(leading_identifier(code, vote) is None for code in codes)


def test_no_series_is_a_shape_no_identifier_can_have():
    assert identifier_shape(NO_SERIES) != NO_SERIES
    assert identifier_shape("no claim-number series".upper()) != NO_SERIES


def test_a_loss_runs_own_few_series_are_still_admitted():
    codes = _codes(MAX_SERIES_FAMILIES, 6)
    assert {identifier_shape(code) for code in codes} <= consensus_shapes(codes)


def test_a_series_that_grew_a_digit_is_one_family():
    later = [f"AL2015{n:04d}-1" for n in range(8400, 8420)]
    earlier = [f"AL2014{n:05d}-1" for n in range(6500, 6504)]
    admitted = consensus_shapes(later + earlier)
    assert len(series_families(admitted)) == 1
    assert identifier_shape(earlier[0]) in admitted


def test_a_large_single_series_run_is_not_refused_for_its_size():
    numbers = [f"WC{n:07d}" for n in range(5000)]
    assert consensus_shapes(numbers) == {identifier_shape(numbers[0])}


# --- end to end --------------------------------------------------------------


def _register_pdf(tmp_path, codes, *, total=True):
    """A table under loss-run headings whose 'claim numbers' are register codes,
    with a printed total its rows add up to."""
    document = pymupdf.open()
    page = None
    y = 0.0
    paid_sum = 0
    for index, code in enumerate(codes):
        if page is None or y > 760:
            page = document.new_page(width=612, height=792)
            page.insert_text((40, 40), "RECORDS REGISTER", fontsize=9)
            page.insert_text((40, 52), "Valuation Date: 12/31/2022", fontsize=8)
            y = 76.0
            for x, label in ((40, "Claim Number"), (140, "Loss Date"), (210, "Status"),
                             (270, "Paid Total"), (350, "Reserve Total"),
                             (440, "Incurred Total")):
                page.insert_text((x, y), label, fontsize=8)
            y += 12
        paid = 100 + index
        paid_sum += paid
        for x, value in ((40, code), (140, f"0{1 + index % 9}/1{index % 9}/2022"),
                         (210, "Closed"), (270, f"{paid:,}.00"), (350, "0.00"),
                         (440, f"{paid:,}.00")):
            page.insert_text((x, y), value, fontsize=8)
        y += 12
    if total:
        for x, value in ((40, "TOTAL"), (270, f"{paid_sum:,}.00"), (350, "0.00"),
                         (440, f"{paid_sum:,}.00")):
            page.insert_text((x, y), value, fontsize=8)
    path = tmp_path / "register.pdf"
    document.save(path)
    document.close()
    return path


def test_a_register_is_not_read_as_a_reconciled_loss_run(tmp_path):
    codes = _codes(len(FAMILIES), 6)
    result = run_pipeline(_register_pdf(tmp_path, codes), use_vision=False,
                          profiles_dir=tmp_path / "profiles")
    assert result.document.claims == []
    assert canonical_status(result.reconciliation) is DocumentStatus.NEEDS_REVIEW


def test_no_register_row_is_dropped_every_one_is_recorded(tmp_path):
    codes = _codes(len(FAMILIES), 6)
    result = run_pipeline(_register_pdf(tmp_path, codes), use_vision=False,
                          profiles_dir=tmp_path / "profiles")
    refused = result.document.refused_claim_rows
    assert sorted(row.identifier for row in refused) == sorted(codes)
    assert all(row.report for row in refused)
    rules = {finding.rule_id for finding in result.reconciliation.findings}
    assert {"R-29", "R-20"} <= rules


def test_a_table_with_a_loss_runs_few_series_still_reads_clean(tmp_path):
    codes = _codes(MAX_SERIES_FAMILIES, 6)
    result = run_pipeline(_register_pdf(tmp_path, codes), use_vision=False,
                          profiles_dir=tmp_path / "profiles")
    assert sorted(c.claim_number for c in result.document.claims) == sorted(codes)
    assert canonical_status(result.reconciliation) is DocumentStatus.CLEAN
