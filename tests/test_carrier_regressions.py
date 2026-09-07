"""Regressions from QA against real-shaped carrier documents.

Each test here corresponds to a defect that reached a user: a false finding, a
document that lost every row, or a number read the wrong way round. The
fixtures reproduce the carrier layouts that caused them.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from core.extract_digital import extract_metadata, extract_pdf
from core.normalize import infer_recovery_sign, parse_money
from core.pipeline import run_pipeline
from core.profiles import detect_carrier, guess_field
from core.schema import DocumentStatus, Severity


def result_for(golden_dir, name):
    return run_pipeline(golden_dir / f"{name}.pdf", use_vision=False)


def findings(result, rule_id):
    return [f for f in result.reconciliation.findings if f.rule_id == rule_id]


# --------------------------------------------------------------------------
# "Claim Ref" was not a recognised header, so every row was dropped
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label", ["Claim Ref", "Claim Reference", "CLM REF", "claim ref no", "File Ref"]
)
def test_claim_ref_is_a_claim_number(label):
    assert guess_field(label).field == "claim_number"


def test_bare_ref_columns_still_go_to_the_mapping_screen():
    """"Ref No" references nothing in particular, so a human decides."""
    assert guess_field("Ref No").field is None
    assert guess_field("Reference").field is None


def test_a_claim_ref_document_keeps_its_rows(golden_dir):
    """The symptom was "skipped a row with no claim number" on every row, and
    R-04 then reporting every column as short by its full printed total."""
    result = result_for(golden_dir, "qa_mainframe_credit")
    assert len(result.document.claims) == 2
    assert not [w for w in result.warnings if "no claim number" in w]
    assert findings(result, "R-04") == []


# --------------------------------------------------------------------------
# Recoveries printed as credits
# --------------------------------------------------------------------------


def test_a_credit_recovery_is_still_parsed_as_printed():
    """The parser's job is to read the characters; the sign convention is a
    separate, document-level question."""
    assert parse_money("250.00-").value == Decimal("-250.00")
    assert parse_money("200,00-", "eu").value == Decimal("-200.00")


@pytest.mark.parametrize("name", ["qa_mainframe_credit", "qa_european_credit"])
def test_credit_recoveries_do_not_produce_a_false_math_error(golden_dir, name):
    """Reading a credit at face value turns R-01's subtraction into an
    addition, and every row with a recovery fails."""
    result = result_for(golden_dir, name)
    assert findings(result, "R-01") == []
    assert result.reconciliation.status is DocumentStatus.CLEAN
    assert result.recovery_sign.credit_convention is True


def test_the_recovery_correction_reaches_the_printed_totals(golden_dir):
    """Correcting the column but not the footer would swap one false R-01 for
    a false R-04."""
    result = result_for(golden_dir, "qa_mainframe_credit")
    assert result.document.printed_totals["recovery_total"] == Decimal("250.00")
    assert result.document.column_total("recovery_total") == Decimal("250.00")
    assert findings(result, "R-04") == []


def test_the_printed_text_is_kept_for_the_audit_trail(golden_dir):
    result = result_for(golden_dir, "qa_mainframe_credit")
    claim = next(c for c in result.document.claims if c.claim_number == "SA-102")
    assert claim.recovery_total == Decimal("250.00")
    assert claim.raw_cells["recovery_total"] == "250.00-"


def test_a_true_zero_recovery_survives_the_correction(golden_dir):
    result = result_for(golden_dir, "qa_mainframe_credit")
    claim = next(c for c in result.document.claims if c.claim_number == "SA-101")
    assert claim.recovery_total == Decimal("0")
    assert claim.raw_cells["recovery_total"] == "-0-"


def test_the_convention_needs_evidence_and_is_never_assumed():
    """Taking abs() of every recovery would hide a genuinely negative one and
    quietly repair a document that is actually wrong."""
    credit = infer_recovery_sign([("A", Decimal("500"), None, Decimal("-250"), Decimal("250"))])
    assert credit.should_negate is True

    positive = infer_recovery_sign([("A", Decimal("500"), None, Decimal("250"), Decimal("250"))])
    assert positive.should_negate is False

    conflicting = infer_recovery_sign([
        ("A", Decimal("500"), None, Decimal("-250"), Decimal("250")),
        ("B", Decimal("500"), None, Decimal("250"), Decimal("250")),
    ])
    assert conflicting.should_negate is False

    nothing_to_go_on = infer_recovery_sign([("A", None, None, Decimal("-250"), None)])
    assert nothing_to_go_on.should_negate is False


def test_a_reversed_recovery_is_not_silently_flipped():
    """A genuine credit reversal in an otherwise positive-convention document
    must stay negative, and R-01 must be allowed to notice it."""
    rows = [
        ("A", Decimal("500"), None, Decimal("250"), Decimal("250")),
        ("B", Decimal("400"), None, Decimal("-100"), Decimal("500")),
    ]
    assert infer_recovery_sign(rows).should_negate is False


# --------------------------------------------------------------------------
# Currency
# --------------------------------------------------------------------------


def test_a_euro_document_is_not_accused_of_mixing_currencies(golden_dir):
    """Defaulting the document to USD and then meeting a euro symbol on the
    rows made R-16 fire on every European loss run."""
    result = result_for(golden_dir, "qa_european_credit")
    assert result.document.currency == "EUR"
    assert result.document.currencies_seen == ["EUR"]
    assert findings(result, "R-16") == []


def test_a_genuinely_mixed_document_still_fires_r16(golden_dir, tmp_path):
    result = result_for(golden_dir, "qa_european_credit")
    document = result.document.model_copy(deep=True)
    document.claims[0].currency = "USD"
    document.currencies_seen = ["EUR", "USD"]
    from core.reconcile import reconcile

    assert [f.rule_id for f in reconcile(document).findings if f.rule_id == "R-16"]


def test_a_document_with_no_symbols_does_not_invent_one(golden_dir):
    result = result_for(golden_dir, "qa_mainframe_credit")
    assert result.document.currency == "USD"
    assert findings(result, "R-16") == []


# --------------------------------------------------------------------------
# Dates: an EU table under a US-formatted header
# --------------------------------------------------------------------------


def test_table_dates_are_not_settled_by_the_header_block(golden_dir):
    """The header prints 06/30/2024 and the claim prints 12.03.2023. Letting
    the header settle the order turns 12 March into 3 December."""
    result = result_for(golden_dir, "qa_european_credit")
    claim = result.document.claims[0]
    assert claim.date_of_loss == date(2023, 3, 12)
    assert result.document.valuation_date == date(2024, 6, 30)


def test_a_us_document_still_reads_its_dates_month_first(golden_dir):
    result = result_for(golden_dir, "qa_travelers_clean")
    assert result.document.claims[0].date_of_loss == date(2023, 5, 12)
    assert result.date_order.source == "evidence"


# --------------------------------------------------------------------------
# Column detection with right-aligned headers
# --------------------------------------------------------------------------


def test_adjacent_right_aligned_money_columns_stay_separate(golden_dir):
    """Right-aligned headers sit close enough that gap-splitting merges them,
    which merged three money columns into one cell and lost two of them."""
    table = extract_pdf(golden_dir / "qa_european_credit.pdf").tables[0]
    assert table.headers == [
        "Claim Ref", "Loss Date", "Status",
        "Paid Total", "Recovery Total", "Incurred Total",
    ]
    assert table.rows[0].cells[3] == "5.700,50 €"
    assert table.rows[0].cells[4] == "200,00-"
    assert table.rows[0].cells[5] == "5.500,50 €"


# --------------------------------------------------------------------------
# Carrier name and claim count
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("TRAVELERS INSURANCE - LOSS RUN REPORT", "TRAVELERS INSURANCE"),
        ("STATE AUTO - HISTORICAL LOSS REPORT", "STATE AUTO"),
        ("ALLIANZ GLOBAL - LOSS RUN STATEMENT", "ALLIANZ GLOBAL"),
        ("TEST CARRIER - INTENTIONAL ERROR REPORT", "TEST CARRIER"),
    ],
)
def test_the_report_title_is_not_part_of_the_carrier_name(line, expected):
    assert detect_carrier(line) == expected


def test_a_carrier_whose_name_contains_a_dash_is_left_alone():
    assert detect_carrier("Zurich - North America") == "Zurich - North America"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("TOTALS Claims: 2 $17,350.50", 2),
        ("Claims: 14", 14),
        ("Claim Count: 7", 7),
        ("Total Claims: 12", 12),
    ],
)
def test_footer_claim_counts_are_recognised(text, expected):
    """These documents state the count as "Claims: N" in the totals row. No
    pattern matched it, so R-05 — one of the two rules that check against what
    the carrier printed — never ran on any of them."""
    assert extract_metadata(text).printed_claim_count == expected


def test_a_claim_identifier_is_never_read_as_a_count():
    assert extract_metadata("Claim #: 12345").printed_claim_count is None
    assert extract_metadata("Claim # 88392").printed_claim_count is None


@pytest.mark.parametrize(
    "name",
    ["qa_travelers_clean", "qa_mainframe_credit", "qa_european_credit", "qa_dirty_errors"],
)
def test_r05_runs_on_every_qa_document(golden_dir, name):
    result = result_for(golden_dir, name)
    assert result.document.printed_claim_count is not None, "R-05 cannot run"
    assert findings(result, "R-05") == []


# --------------------------------------------------------------------------
# Valuation date phrasing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Valuation Date: 06/30/2024",
        "Valuation Dt: 06/30/2024",
        "Val Date: 06/30/2024",
        "Valued as of 06/30/2024",
        "Valued through 06/30/2024",
        "Evaluation Date: 06/30/2024",
        "Evaluated as of: 06/30/2024",
        "As of Date: 06/30/2024",
        "Data as of: 06/30/2024",
        "Values as of 06/30/2024",
    ],
)
def test_valuation_date_phrasings(text):
    assert extract_metadata(text).valuation_date_text == "06/30/2024"


def test_a_report_date_is_not_treated_as_a_valuation_date():
    """When the report was printed is not when the values are stated as of.
    Reading one as the other puts a wrong valuation date on a priced
    submission; R-06 flagging a missing one is the safer failure."""
    assert extract_metadata("Report Date: 07/15/2024").valuation_date_text is None
    assert extract_metadata("Run Date: 07/15/2024").valuation_date_text is None


# --------------------------------------------------------------------------
# The engine still catches what it should
# --------------------------------------------------------------------------


def test_real_defects_are_still_caught(golden_dir):
    """None of the false-positive fixes may cost a true positive."""
    result = result_for(golden_dir, "qa_dirty_errors")
    assert result.reconciliation.status is DocumentStatus.NEEDS_REVIEW

    r01 = findings(result, "R-01")
    assert len(r01) == 1
    assert r01[0].claim_number == "ERR-001"
    assert r01[0].expected == Decimal("60000.00")
    assert r01[0].actual == Decimal("55000.00")
    assert r01[0].severity is Severity.ERROR

    assert [f.claim_number for f in findings(result, "R-08")] == ["ERR-001"]
    assert [f.claim_number for f in findings(result, "R-09")] == ["ERR-001"]


def test_the_clean_documents_are_clean(golden_dir):
    for name in ("qa_travelers_clean", "qa_mainframe_credit", "qa_european_credit"):
        result = result_for(golden_dir, name)
        assert result.reconciliation.status is DocumentStatus.CLEAN, (
            f"{name}: {[str(f) for f in result.reconciliation.findings]}"
        )
