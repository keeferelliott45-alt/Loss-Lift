"""The digital-format pack: well-formed cases, truth that matches the page.

These tests check the pack, not LossLift: every case parses under the frozen
truth schema, is bound to the bytes it wrote, and its truth agrees with what
the page prints. How LossLift scores on the pack is measured separately
(``tools.qualification.cases.evaluate_case``), and a disagreement is reported,
never fixed here.
"""

from __future__ import annotations

from decimal import Decimal

import pymupdf
import pytest

from tests.qualification.packs.digital_formats import build_cases
from tests.qualification.packs.digital_formats.printed import printed_money
from tools.qualification.cases import Expectation, sha256_file
from tools.qualification.truth import CRITICAL_FIELDS, LabelState

EXPECTED_IDS = {
    "letter-portrait", "a4-portrait", "landscape-components", "labelled-every-row",
    "labelled-some-rows", "labelled-unreadable", "furniture-controls", "absent-carrier",
    "wrapped-headers", "multiline-descriptions", "signed-and-recovery", "blank-versus-zero",
}


@pytest.fixture(scope="module")
def cases(tmp_path_factory):
    return build_cases(tmp_path_factory.mktemp("digital-formats"))


def _text(case) -> str:
    with pymupdf.open(case.pdf) as document:
        return "\n".join(page.get_text() for page in document)


def test_the_pack_has_its_twelve_cases_with_unique_ids(cases):
    assert {case.case_id for case in cases} == EXPECTED_IDS
    assert len(cases) == 12


def test_each_truth_is_bound_to_the_file_it_describes(cases):
    for case in cases:
        assert case.truth.sha256 == sha256_file(case.pdf), case.case_id
        assert case.truth.document_id == case.case_id
        assert case.truth.qualifies


def test_every_claim_is_anchored_and_labels_every_critical_field(cases):
    for case in cases:
        claim_pages = {p.page for p in case.truth.pages if p.role == "claims"}
        assert case.truth.claims, case.case_id
        for claim in case.truth.claims:
            assert claim.anchor.page in claim_pages
            assert set(CRITICAL_FIELDS) <= set(claim.fields), case.case_id


def test_every_known_claim_number_and_amount_is_printed_on_the_page(cases):
    for case in cases:
        text = _text(case)
        for claim in case.truth.claims:
            if claim.claim_number:
                assert claim.claim_number in text, (case.case_id, claim.position)
            for name, label in claim.fields.items():
                if label.state is LabelState.KNOWN and isinstance(label.value, Decimal):
                    shown = f"{abs(label.value):,.2f}"
                    assert shown in text, (case.case_id, claim.position, name)


def test_printed_totals_equal_the_sum_of_the_printed_rows(cases):
    for case in cases:
        for name, label in case.truth.printed.totals.items():
            values = [c.fields[name].value for c in case.truth.claims
                      if name in c.fields and c.fields[name].state is LabelState.KNOWN]
            if any(c.identity is LabelState.AMBIGUOUS for c in case.truth.claims):
                continue  # the unreadable row's amounts are in the printed total too
            assert label.value == sum(values, Decimal("0")), (case.case_id, name)


def test_incurred_ties_to_paid_reserve_and_recovery_on_every_row(cases):
    """The carrier's own arithmetic is right in every case: any R-01 is a reading error."""
    for case in cases:
        for claim in case.truth.claims:
            def value(name):
                label = claim.fields.get(name)
                return label.value if label and label.state is LabelState.KNOWN else Decimal("0")
            assert value("incurred_total") == (value("paid_total") + value("reserve_total")
                                               - value("recovery_total")), case.case_id


def test_the_declared_formats_are_what_was_written(cases):
    by_id = {case.case_id: case for case in cases}
    with pymupdf.open(by_id["a4-portrait"].pdf) as document:
        assert round(document[0].rect.width) == 595
    with pymupdf.open(by_id["landscape-components"].pdf) as document:
        assert document[0].rect.width > document[0].rect.height
    assert "Claim No: HE-24001" in _text(by_id["labelled-every-row"])
    assert "HOLLOWAY" not in _text(by_id["absent-carrier"])
    blank = by_id["blank-versus-zero"].truth.claims[0].fields
    assert blank["reserve_total"].state is LabelState.ABSENT
    zero = by_id["blank-versus-zero"].truth.claims[1].fields
    assert zero["reserve_total"].state is LabelState.KNOWN and zero["reserve_total"].value == 0


def test_signed_amounts_are_authored_negative(cases):
    signed = next(c for c in cases if c.case_id == "signed-and-recovery").truth.claims
    assert signed[1].fields["paid_total"].value == Decimal("-250.00")
    assert signed[3].fields["paid_total"].value == Decimal("-75.00")
    assert signed[0].fields["recovery_total"].value == Decimal("138.26")


def test_expectations_are_declared_consistently(cases):
    for case in cases:
        if case.expectation is Expectation.REVIEW:
            assert case.truth.status == "NEEDS_REVIEW"
    unreadable = next(c for c in cases if c.case_id == "labelled-unreadable")
    assert unreadable.expectation is Expectation.REVIEW
    assert sum(c.identity is LabelState.AMBIGUOUS for c in unreadable.truth.claims) == 1


def test_a_rebuild_prints_the_same_content_and_truth(cases, tmp_path):
    again = {case.case_id: case for case in build_cases(tmp_path)}
    for case in cases:
        other = again[case.case_id]
        assert _text(case) == _text(other), case.case_id
        assert case.truth.claims == other.truth.claims
        assert case.truth.printed == other.truth.printed


@pytest.mark.parametrize("printed, value", [
    ("1,234.56", "1234.56"), ("(1,234.56)", "-1234.56"), ("1,234.56-", "-1234.56"),
    ("0.00", "0.00"), ("", None), ("$5,000", "5000"),
])
def test_the_pack_reads_printed_amounts_as_a_person_does(printed, value):
    assert printed_money(printed) == value
