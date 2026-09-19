"""Stage 6 — the editable review table and live re-reconciliation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from core.pipeline import (
    PROVENANCE_COLUMNS,
    apply_edits,
    rerun_reconciliation,
    review_columns,
    run_pipeline,
    to_records,
)
from core.schema import ClaimStatus, DocumentStatus, NullReason, SourceMethod
from tests.golden.fixtures import ALL_FIXTURES, printed_r01_violations


@pytest.fixture()
def broken(golden_dir):
    return run_pipeline(golden_dir / "arithmetic_error.pdf", use_vision=False)


def test_fixtures_only_break_the_arithmetic_they_mean_to():
    """A value with no column on the page is invisible to the reader and to
    the reconciler; leaving one in plants an error nobody intended."""
    for fixture in ALL_FIXTURES:
        assert tuple(printed_r01_violations(fixture)) == fixture.deliberate_r01_errors, (
            f"{fixture.name}: unintended arithmetic mismatch"
        )


def test_review_columns_hide_columns_the_document_never_had(golden_dir):
    result = run_pipeline(golden_dir / "us_basic.pdf", use_vision=False)
    columns = review_columns(result.document)
    assert "paid_total" in columns
    assert "paid_medical" not in columns      # a GL run has no medical column
    assert "claim_number" in columns and "incurred_total" in columns


def test_wc_columns_appear_for_a_wc_document(golden_dir):
    result = run_pipeline(golden_dir / "wc_medical.pdf", use_vision=False)
    columns = review_columns(result.document)
    assert "paid_medical" in columns and "reserve_medical" in columns


def test_records_carry_provenance(broken):
    records = to_records(broken.document)
    assert all(key in records[0] for key in PROVENANCE_COLUMNS)
    assert records[0]["_page"] == 1
    assert records[0]["_method"] == "digital"


def test_editing_a_cell_reruns_the_checks(broken):
    assert broken.reconciliation.status is DocumentStatus.NEEDS_REVIEW
    records = to_records(broken.document)
    index = next(i for i, r in enumerate(records) if r["claim_number"] == "FM-0003")
    records[index]["incurred_total"] = 31400.00

    updated = apply_edits(broken.document, records)
    assert rerun_reconciliation(updated).status is DocumentStatus.CLEAN


def test_an_edited_cell_is_marked_manual(broken):
    """The contract: provenance is answered per field, not per row.

    ``source_method`` describes the row and says manual as soon as any cell is
    corrected. That is true of the row and false of its other nine fields, so
    it is not the authority — ``provenance_of`` is. A corrected cell is manual;
    everything beside it still came off the page and still says so.
    """
    records = to_records(broken.document)
    index = next(i for i, r in enumerate(records) if r["claim_number"] == "FM-0003")
    records[index]["incurred_total"] = 31400.00
    updated = apply_edits(broken.document, records)

    edited = next(c for c in updated.claims if c.claim_number == "FM-0003")
    untouched = next(c for c in updated.claims if c.claim_number == "FM-0001")

    assert edited.provenance_of("incurred_total") is SourceMethod.MANUAL
    assert edited.provenance_of("paid_total") is SourceMethod.DIGITAL
    assert edited.confidence_for("incurred_total") == 1.0
    assert untouched.provenance_of("incurred_total") is SourceMethod.DIGITAL
    # The row is still marked, so nothing downstream loses sight of the edit.
    assert edited.source_method is SourceMethod.MANUAL
    assert untouched.source_method is SourceMethod.DIGITAL


def test_the_extracted_value_survives_the_correction(broken):
    """A correction must never erase what the carrier's page actually said."""
    records = to_records(broken.document)
    index = next(i for i, r in enumerate(records) if r["claim_number"] == "FM-0003")
    was = broken.document.claims[index].incurred_total
    records[index]["incurred_total"] = 31400.00
    edited = apply_edits(broken.document, records).claims[index]

    assert edited.incurred_total == Decimal("31400.00")
    assert edited.original_of("incurred_total") == f"{was:f}"
    assert edited.original_of("paid_total") is None


def test_a_second_edit_does_not_overwrite_the_extracted_value(broken):
    """The original is the document's answer, not the reviewer's last one."""
    records = to_records(broken.document)
    was = broken.document.claims[0].incurred_total
    records[0]["incurred_total"] = 100.0
    once = apply_edits(broken.document, records)

    again = to_records(once)
    again[0]["incurred_total"] = 200.0
    twice = apply_edits(once, again)

    assert twice.claims[0].incurred_total == Decimal("200.00")
    assert twice.claims[0].original_of("incurred_total") == f"{was:f}"


def test_edited_money_stays_decimal(broken):
    records = to_records(broken.document)
    records[0]["incurred_total"] = 1234.56
    updated = apply_edits(broken.document, records)
    value = updated.claims[0].incurred_total
    assert isinstance(value, Decimal)
    assert value == Decimal("1234.56")


def test_typed_text_is_parsed_the_document_s_way(golden_dir):
    result = run_pipeline(golden_dir / "eu_format.pdf", use_vision=False)
    records = to_records(result.document)
    records[0]["incurred_total"] = "9.876,54"        # the user types EU style
    updated = apply_edits(result.document, records)
    assert updated.claims[0].incurred_total == Decimal("9876.54")


def test_clearing_a_cell_makes_it_null_not_zero(broken):
    records = to_records(broken.document)
    records[0]["incurred_total"] = None
    updated = apply_edits(broken.document, records)
    assert updated.claims[0].incurred_total is None
    findings = rerun_reconciliation(updated).findings
    assert any(f.rule_id == "R-07" and f.claim_number == "FM-0001" for f in findings)


def test_unparseable_input_is_recorded_with_a_reason(broken):
    records = to_records(broken.document)
    records[0]["incurred_total"] = "about a thousand"
    updated = apply_edits(broken.document, records)
    assert updated.claims[0].incurred_total is None
    assert updated.claims[0].issue("incurred_total") is NullReason.UNPARSEABLE


def test_emptying_the_claim_number_cannot_delete_carrier_evidence(broken):
    records = to_records(broken.document)
    before = len(records)
    records[1]["claim_number"] = ""
    with pytest.raises(ValueError, match="cannot be deleted"):
        apply_edits(broken.document, records)
    assert len(broken.document.claims) == before


def test_a_new_row_is_manual(broken):
    records = to_records(broken.document)
    blank = {key: None for key in records[0]}
    blank["claim_number"] = "FM-9999"
    blank["incurred_total"] = 500.0
    blank["date_of_loss"] = date(2024, 3, 1)
    records.append(blank)

    updated = apply_edits(broken.document, records)
    added = next(c for c in updated.claims if c.claim_number == "FM-9999")
    # Added outright: there is no reading behind any of it.
    assert added.source_method is SourceMethod.MANUAL
    assert added.provenance_of("incurred_total") is SourceMethod.MANUAL
    assert added.provenance_of("paid_total") is SourceMethod.MANUAL
    assert added.incurred_total == Decimal("500.00")


def test_status_edits_go_through_the_vocabulary(broken):
    records = to_records(broken.document)
    records[0]["claim_status"] = "closed"
    updated = apply_edits(broken.document, records)
    assert updated.claims[0].claim_status is ClaimStatus.CLOSED


def test_editing_does_not_touch_the_original(broken):
    before = broken.document.model_dump_json()
    records = to_records(broken.document)
    records[0]["incurred_total"] = 1.0
    apply_edits(broken.document, records)
    assert broken.document.model_dump_json() == before


def test_no_edits_means_no_change(broken):
    records = to_records(broken.document)
    updated = apply_edits(broken.document, records)
    assert [c.source_method for c in updated.claims] == [
        c.source_method for c in broken.document.claims
    ]
    assert [c.incurred_total for c in updated.claims] == [
        c.incurred_total for c in broken.document.claims
    ]


def test_a_dataframe_round_trip_does_not_invent_edits(broken):
    """The review table goes through pandas, which turns an empty amount into
    NaN. NaN is a null; if it came back as an edit, every blank cell would be
    marked manual and every claim would look touched."""
    import pandas as pd

    columns = review_columns(broken.document)
    records = to_records(broken.document, columns)
    frame = pd.DataFrame(records, columns=list(columns) + list(PROVENANCE_COLUMNS))
    updated = apply_edits(broken.document, frame.to_dict("records"))

    assert all(claim.source_method is SourceMethod.DIGITAL for claim in updated.claims)
    assert [c.reserve_total for c in updated.claims] == [
        c.reserve_total for c in broken.document.claims
    ]
    assert updated.claims[1].reserve_total is None


@pytest.mark.parametrize("blank", ["nan", "numpy_nan", "pandas_na", "decimal_nan", "inf", "none"])
def test_every_flavour_of_null_stays_null(broken, blank):
    """pandas, numpy and pyarrow each have their own null and none of them is
    None. Any reaching Decimal becomes Decimal("NaN"), which the schema
    rejects — so the review screen would crash instead of showing a blank."""
    import numpy as np
    import pandas as pd

    values = {
        "nan": float("nan"),
        "numpy_nan": np.float64("nan"),
        "pandas_na": pd.NA,
        "decimal_nan": Decimal("NaN"),
        "inf": float("inf"),
        "none": None,
    }
    records = to_records(broken.document)
    records[0]["incurred_total"] = values[blank]

    updated = apply_edits(broken.document, records)
    assert updated.claims[0].incurred_total is None
