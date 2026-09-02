"""Stress fixtures — harder combinations than any single QA report caught.

These are deliberately harder than the regular golden fixtures: a 44-claim
multi-page document, genuinely mixed currencies, a document with zero
disambiguating evidence anywhere in it, a realistic pile of simultaneous
carrier defects, and an obscure format that needs the mapping screen *and*
every normalisation convention at once.

Each assertion here is exact — a count, a specific rule, a specific value —
rather than a blanket accuracy threshold, because for the genuinely
adversarial fixtures "accuracy" is not well defined: the correct behaviour on
an unresolvable field is to return null, not to guess right.
"""

from __future__ import annotations

from decimal import Decimal

from core.pipeline import ColumnMapping, run_pipeline
from core.schema import DocumentStatus, NullReason, Severity
from tests.golden.fixtures import (
    STRESS_MEGA_WC,
    STRESS_OBSCURE_EU_CREDIT,
    STRESS_TRUE_AMBIGUITY,
)


def result_for(golden_dir, name, **kwargs):
    return run_pipeline(golden_dir / f"{name}.pdf", use_vision=False, **kwargs)


def findings(result, rule_id):
    return [f for f in result.reconciliation.findings if f.rule_id == rule_id]


# --------------------------------------------------------------------------
# 44 claims, 5 pages, four planted WARN/INFO findings, one genuine duplicate
# --------------------------------------------------------------------------


def test_a_large_multipage_document_extracts_every_claim(golden_dir):
    result = result_for(golden_dir, "stress_mega_wc")
    assert len(result.document.claims) == len(STRESS_MEGA_WC.claims)
    assert findings(result, "R-04") == []
    assert findings(result, "R-05") == []


def test_only_the_duplicate_blocks_the_badge(golden_dir):
    """44 claims and 5 pages of simultaneous findings, of which exactly one
    is a hard fail: the duplicated claim number. An outlier, a
    closed-with-reserve claim and a legitimate negative paid all fire beside
    it and none of them blocks the badge, because only ERROR severity does.

    R-11 counts a claim twice and every total built from it is wrong by that
    claim, which is why it is a hard fail rather than a flag."""
    result = result_for(golden_dir, "stress_mega_wc")
    assert [f.rule_id for f in result.reconciliation.errors] == ["R-11"]
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW
    assert len(result.reconciliation.warnings) >= 4


def test_the_duplicate_is_caught_on_the_right_page(golden_dir):
    result = result_for(golden_dir, "stress_mega_wc")
    r11 = findings(result, "R-11")
    assert len(r11) == 1
    assert r11[0].claim_number == "WC-STR-0038"
    # A duplicate two pages away is a different rule (R-12); this one must
    # not be double-counted there.
    assert findings(result, "R-12") == []


def test_the_outlier_and_the_reserve_warning_both_fire(golden_dir):
    result = result_for(golden_dir, "stress_mega_wc")
    r13 = findings(result, "R-13")
    assert {f.claim_number for f in r13} == {"WC-STR-9001"}

    r08 = findings(result, "R-08")
    assert [f.claim_number for f in r08] == ["WC-STR-9002"]

    r14 = findings(result, "R-14")
    assert [f.claim_number for f in r14] == ["WC-STR-9003"]


def test_a_column_wide_median_is_not_thrown_off_by_one_outlier(golden_dir):
    """R-13's median must come from the other 43 rows, not be dragged toward
    the outlier it is supposed to be detecting."""
    result = result_for(golden_dir, "stress_mega_wc")
    outlier = next(
        c for c in result.document.claims if c.claim_number == "WC-STR-9001"
    )
    assert outlier.paid_indemnity == Decimal("410000.00")
    ordinary = [
        c.paid_indemnity
        for c in result.document.claims
        if c.claim_number not in {"WC-STR-9001", "WC-STR-9003"}
        and c.paid_indemnity is not None
    ]
    median = sorted(ordinary)[len(ordinary) // 2]
    assert median < Decimal("10000.00")


# --------------------------------------------------------------------------
# Genuinely mixed currency
# --------------------------------------------------------------------------


def test_r16_actually_fires_on_a_genuinely_mixed_document(golden_dir):
    result = result_for(golden_dir, "stress_true_mixed_currency")
    r16 = findings(result, "R-16")
    assert len(r16) == 1
    assert "EUR" in r16[0].message and "USD" in r16[0].message


def test_each_row_still_parses_correctly_despite_the_conflict(golden_dir):
    """Two separators present resolves a single cell (spec rule 1) even when
    the *document* cannot settle on one convention overall."""
    result = result_for(golden_dir, "stress_true_mixed_currency")
    by_number = {c.claim_number: c for c in result.document.claims}
    assert by_number["US-001"].paid_total == Decimal("4200.00")
    assert by_number["US-001"].currency == "USD"
    assert by_number["EU-101"].paid_total == Decimal("3100.50")
    assert by_number["EU-101"].currency == "EUR"


def test_a_legitimate_negative_incurred_survives_in_both_currencies(golden_dir):
    result = result_for(golden_dir, "stress_true_mixed_currency")
    by_number = {c.claim_number: c for c in result.document.claims}
    assert by_number["US-002"].incurred_total == Decimal("-1600.00")
    assert by_number["EU-102"].incurred_total == Decimal("7100.00")


def test_conflicting_locale_evidence_is_reported_not_hidden(golden_dir):
    result = result_for(golden_dir, "stress_true_mixed_currency")
    assert result.locale.confident is False
    assert result.locale.us_votes > 0 and result.locale.eu_votes > 0


# --------------------------------------------------------------------------
# Zero disambiguating evidence anywhere in the document
# --------------------------------------------------------------------------


def test_every_amount_stays_null_with_a_reason(golden_dir):
    result = result_for(golden_dir, "stress_true_ambiguity")
    for claim in result.document.claims:
        assert claim.paid_total is None
        assert claim.reserve_total is None
        assert claim.incurred_total is None
        assert claim.issue("incurred_total") is NullReason.AMBIGUOUS_SEPARATOR


def test_every_claim_table_date_stays_null_with_a_reason(golden_dir):
    """The header's own evidence (a policy period ending on the 31st) must
    not leak into the table: that is a different format context, and
    borrowing it would silently parse dates the table itself cannot prove."""
    result = result_for(golden_dir, "stress_true_ambiguity")
    for claim in result.document.claims:
        assert claim.date_of_loss is None
        assert claim.issue("date_of_loss") is NullReason.AMBIGUOUS_DATE_ORDER


def test_the_header_still_resolves_its_own_unambiguous_evidence(golden_dir):
    """The policy period end date (12/31/2024) has a day above 12 and proves
    month-first for the header block. That evidence is real for the header
    and must still be used there -- "never guess" is not "never use
    evidence," and a document is not made worse by refusing a fact it
    actually contains."""
    result = result_for(golden_dir, "stress_true_ambiguity")
    assert result.document.policy_period_end.isoformat() == "2024-12-31"
    assert result.document.policy_period_start.isoformat() == "2024-01-01"


def test_the_review_screen_has_something_to_show_for_every_row(golden_dir):
    """Total ambiguity must not mean total silence: R-15 has to list every
    field so a human knows exactly what to type in."""
    result = result_for(golden_dir, "stress_true_ambiguity")
    r15 = findings(result, "R-15")
    assert len(r15) >= len(STRESS_TRUE_AMBIGUITY.claims) * 3
    assert all(f.severity is Severity.WARN for f in r15)


def test_claim_numbers_are_the_one_thing_that_did_resolve(golden_dir):
    """Not everything in the document is ambiguous -- only the fields whose
    shape genuinely is. The claim numbers are plain text and must come
    through fine."""
    result = result_for(golden_dir, "stress_true_ambiguity")
    assert {c.claim_number for c in result.document.claims} == {
        "AMB-001", "AMB-002", "AMB-003",
    }


# --------------------------------------------------------------------------
# A realistic pile of simultaneous defects
# --------------------------------------------------------------------------


def test_every_planted_defect_is_caught_and_nothing_extra(golden_dir):
    result = result_for(golden_dir, "stress_dirty_avalanche")
    rule_ids = sorted({f.rule_id for f in result.reconciliation.findings})
    assert rule_ids == [
        "R-01", "R-04", "R-05", "R-07", "R-11", "R-15", "R-18", "R-23",
    ]
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW


def test_na_recovery_is_null_not_zero_amid_the_noise(golden_dir):
    result = result_for(golden_dir, "stress_dirty_avalanche")
    claim = next(c for c in result.document.claims if c.claim_number == "DRT-002")
    assert claim.recovery_total is None
    assert claim.issue("recovery_total") is NullReason.NOT_APPLICABLE


def test_the_blank_claim_number_row_keeps_its_money_in_reconciliation(golden_dir):
    """The planted row has no claim number and $750 of real money on it.

    It is right not to become a claim -- nothing says whose it is. It was not
    right to leave as a warning: this fixture has been quietly dropping $750
    since it was written, and the assertion that a warning was enough is what
    let it. R-23 keeps the amount where a reviewer has to answer for it.
    """
    result = result_for(golden_dir, "stress_dirty_avalanche")
    assert len(result.document.claims) == 6  # 7 planted, 1 has no claim number

    dropped = result.document.unplaced_rows
    assert len(dropped) == 1
    assert dropped[0].amounts == {"paid_total": "750.00", "incurred_total": "750.00"}
    assert dropped[0].where().startswith("page 1, line ")

    raised = [f for f in result.reconciliation.findings if f.rule_id == "R-23"]
    assert len(raised) == 1
    assert "750.00" in raised[0].message


def test_the_unreadable_amount_genuinely_breaks_the_footer_tie(golden_dir):
    """DRT-007's paid amount is real text ("TBD - pending adjuster") that no
    parser should invent a number from -- the resulting R-04 gap is the
    correct outcome, not a bug to paper over."""
    result = result_for(golden_dir, "stress_dirty_avalanche")
    claim = next(c for c in result.document.claims if c.claim_number == "DRT-007")
    assert claim.paid_total is None
    assert claim.issue("paid_total") is NullReason.UNPARSEABLE
    r04 = findings(result, "R-04")
    assert {f.field for f in r04} == {"paid_total", "incurred_total"}


def test_the_real_arithmetic_defect_is_found_among_the_noise(golden_dir):
    result = result_for(golden_dir, "stress_dirty_avalanche")
    r01 = findings(result, "R-01")
    assert len(r01) == 1
    assert r01[0].claim_number == "DRT-006"


def test_the_invalid_date_is_null_and_flagged_required(golden_dir):
    result = result_for(golden_dir, "stress_dirty_avalanche")
    claim = next(c for c in result.document.claims if c.claim_number == "DRT-003")
    assert claim.date_of_loss is None
    assert claim.issue("date_of_loss") is NullReason.INVALID_DATE
    assert any(
        f.rule_id == "R-07" and f.claim_number == "DRT-003"
        for f in result.reconciliation.findings
    )


# --------------------------------------------------------------------------
# Obscure headers + EU + credit recoveries + multipage, all at once
# --------------------------------------------------------------------------


def test_the_table_is_found_but_maps_only_the_recognisable_columns(golden_dir):
    """4 of 9 labels are heuristically recognisable; the rest need a human.
    If the table were not detected at all, this would show up as zero
    headers rather than a mapping screen -- a materially worse failure."""
    result = result_for(golden_dir, "stress_obscure_headers_eu_credit")
    assert result.mapping.headers != []
    assert result.needs_mapping is True
    assert result.document.claims == []
    mapped = {name for name in result.mapping.fields.values() if name}
    assert mapped == {"claim_status", "paid_total", "reserve_total", "recovery_total"}


def _full_mapping(result) -> ColumnMapping:
    headers = result.mapping.headers
    fields = {0: "claim_number", 1: "date_of_loss", 2: "date_reported",
              3: "claim_status", 4: "claimant_name", 5: "paid_total",
              6: "reserve_total", 7: "recovery_total", 8: "incurred_total"}
    return ColumnMapping(headers=headers, fields=fields, source="manual",
                         fingerprint=result.mapping.fingerprint)


def test_once_mapped_every_convention_resolves_together(golden_dir):
    """EU separators, dd/mm/yyyy dates, credit-sign recoveries and a table
    spanning three pages with a repeated header all have to cooperate for
    this to come back clean."""
    first_pass = result_for(golden_dir, "stress_obscure_headers_eu_credit")
    result = result_for(
        golden_dir, "stress_obscure_headers_eu_credit",
        mapping_override=_full_mapping(first_pass),
    )
    assert len(result.document.claims) == len(STRESS_OBSCURE_EU_CREDIT.claims)
    assert result.document.locale_hint == "eu" and result.document.locale_confident
    assert result.document.date_order == "dmy" and result.document.date_order_confident
    assert result.recovery_sign.credit_convention is True
    assert result.reconciliation.status is DocumentStatus.CLEAN


def test_the_mapped_recovery_matches_what_was_printed(golden_dir):
    first_pass = result_for(golden_dir, "stress_obscure_headers_eu_credit")
    result = result_for(
        golden_dir, "stress_obscure_headers_eu_credit",
        mapping_override=_full_mapping(first_pass),
    )
    credited = [
        c for c in result.document.claims
        if c.raw_cells.get("recovery_total", "").endswith("-")
    ]
    assert credited
    for claim in credited:
        assert claim.recovery_total is not None and claim.recovery_total > 0
