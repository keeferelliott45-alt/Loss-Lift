"""Exhaustive coverage of spec section 4.

Every format listed in the spec appears here by name, plus the ambiguous cases
that must fail rather than guess.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from core.normalize import (
    NumberParse,
    clean_text,
    infer_date_order,
    infer_locale,
    normalize_label,
    parse_bool,
    parse_date,
    parse_int,
    parse_money,
    parse_status,
    parse_text,
)
from core.schema import ClaimStatus, NullReason


def money(raw, locale=None, **kwargs) -> NumberParse:
    return parse_money(raw, locale, **kwargs)


# --------------------------------------------------------------------------
# The table in spec section 4, line by line
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "locale", "expected"),
    [
        ("1,234.56", None, Decimal("1234.56")),      # US thousands + decimal
        ("1.234,56", None, Decimal("1234.56")),      # EU thousands + decimal
        ("1 234,56", None, Decimal("1234.56")),      # French / non-breaking space
        ("1 234,56", None, Decimal("1234.56")), # literal NBSP
        ("(1,234.56)", None, Decimal("-1234.56")),   # accounting negative
        ("1,234.56-", None, Decimal("-1234.56")),    # mainframe trailing minus
        ("-1,234.56", None, Decimal("-1234.56")),    # leading minus
        ("$1,234", "us", Decimal("1234")),           # currency prefix
        ("1.234 €", "eu", Decimal("1234")),     # currency suffix
        ("-0-", None, Decimal("0")),                 # mainframe zero
        ("1,234.56 CR", None, Decimal("-1234.56")),  # credit is negative
    ],
)
def test_spec_number_formats(raw, locale, expected):
    parsed = money(raw, locale)
    assert parsed.value == expected, f"{raw!r} -> {parsed.value} ({parsed.reason})"
    assert parsed.reason is None


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("N/A", NullReason.NOT_APPLICABLE),
        ("NA", NullReason.NOT_APPLICABLE),
        ("n/a", NullReason.NOT_APPLICABLE),
        ("", NullReason.EMPTY),
        ("   ", NullReason.EMPTY),
        (None, NullReason.EMPTY),
        ("--", NullReason.DASH_PLACEHOLDER),
    ],
)
def test_null_tokens_are_null_never_zero(raw, reason):
    parsed = money(raw)
    assert parsed.value is None
    assert parsed.reason is reason


def test_dash_is_zero_when_the_carrier_profile_says_so():
    assert money("--", dash_means_zero=True).value == Decimal("0")
    assert money("--", dash_means_zero=False).value is None


# --------------------------------------------------------------------------
# Separator disambiguation (the four numbered rules)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,234.56", Decimal("1234.56")),
        ("1.234,56", Decimal("1234.56")),
        ("1,234,567.89", Decimal("1234567.89")),
        ("1.234.567,89", Decimal("1234567.89")),
        ("12,345,678.90", Decimal("12345678.90")),
    ],
)
def test_rule_1_last_separator_is_the_decimal(raw, expected):
    assert money(raw).value == expected


@pytest.mark.parametrize("raw", ["1,234", "1.234", "$1,234", "12.345"])
def test_rule_2_lone_separator_with_three_digits_is_ambiguous(raw):
    parsed = money(raw, locale=None)
    assert parsed.value is None
    assert parsed.reason is NullReason.AMBIGUOUS_SEPARATOR
    assert parsed.ambiguous is True


@pytest.mark.parametrize(
    ("raw", "us_value", "eu_value"),
    [
        ("1,234", Decimal("1234"), Decimal("1.234")),
        ("1.234", Decimal("1.234"), Decimal("1234")),
    ],
)
def test_rule_2_resolves_with_a_locale(raw, us_value, eu_value):
    assert money(raw, "us").value == us_value
    assert money(raw, "eu").value == eu_value


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,5", Decimal("1.5")),        # two digits after: decimal either way
        ("1.5", Decimal("1.5")),
        ("1,23", Decimal("1.23")),
        ("0.05", Decimal("0.05")),
        ("1234.5678", Decimal("1234.5678")),
        ("12345,678", Decimal("12345.678")),   # too many leading digits to group
        ("1,234,567", Decimal("1234567")),     # repeated separator is grouping
        ("1.234.567", Decimal("1234567")),
    ],
)
def test_unambiguous_lone_separators_need_no_locale(raw, expected):
    parsed = money(raw, locale=None)
    assert parsed.value == expected
    assert parsed.reason is None


def test_rule_3_document_level_inference():
    inference = infer_locale(["1,234.56", "900", "12"])
    assert inference.locale == "us"
    assert inference.confident is True
    assert money("1,234", inference.for_parsing).value == Decimal("1234")

    inference = infer_locale(["1.234,56", "900"])
    assert inference.locale == "eu"
    assert inference.confident is True
    assert money("1.234", inference.for_parsing).value == Decimal("1234")


def test_rule_3_default_is_us_but_unproven():
    inference = infer_locale(["100", "200", "300"])
    assert inference.locale == "us"
    assert inference.confident is False
    assert inference.for_parsing is None


def test_rule_4_unproven_locale_never_guesses():
    inference = infer_locale(["100", "250"])
    parsed = money("1,234", inference.for_parsing)
    assert parsed.value is None
    assert parsed.reason is NullReason.AMBIGUOUS_SEPARATOR


def test_conflicting_evidence_is_not_confident():
    inference = infer_locale(["1,234.56", "9.876,54"])
    assert inference.confident is False
    assert inference.us_votes == 1
    assert inference.eu_votes == 1


@pytest.mark.parametrize(
    ("tokens", "locale"),
    [
        (["1,234,567"], "us"),     # repeated comma = grouping = US
        (["1.234.567"], "eu"),
        (["12,5"], "eu"),          # comma with 2 digits after = EU decimal
        (["12.5"], "us"),
        (["1.234,56"], "eu"),
        (["1,234.56"], "us"),
    ],
)
def test_locale_evidence_sources(tokens, locale):
    assert infer_locale(tokens).locale == locale
    assert infer_locale(tokens).confident is True


# --------------------------------------------------------------------------
# Signs, currency, junk
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("(0.00)", Decimal("0.00")),
        ("($1,234.56)", Decimal("-1234.56")),
        ("1,234.56 DR", Decimal("1234.56")),
        ("+1,234.56", Decimal("1234.56")),
        ("−1,234.56", Decimal("-1234.56")),   # unicode minus
        ("USD 1,234.56", Decimal("1234.56")),
        ("1,234.56 USD", Decimal("1234.56")),
        ("0", Decimal("0")),
        ("0.00", Decimal("0.00")),
    ],
)
def test_signs_and_currency(raw, expected):
    parsed = money(raw)
    assert parsed.value == expected
    assert parsed.reason is None


@pytest.mark.parametrize(
    ("raw", "code"),
    [("$1,234.56", "USD"), ("1.234,56 €", "EUR"), ("£500.00", "GBP"), ("1,234.56", None)],
)
def test_currency_symbol_is_reported(raw, code):
    assert money(raw).currency == code


@pytest.mark.parametrize(
    "raw",
    ["abc", "12abc", "1,23,456", "12,34,567", "1.2.3,4,5", "(1,234", "50%", "#REF!"],
)
def test_junk_is_unparseable_not_zero(raw):
    parsed = money(raw)
    assert parsed.value is None
    assert parsed.reason is NullReason.UNPARSEABLE


def test_floats_are_refused_outright():
    with pytest.raises(TypeError):
        parse_money(1234.56)


def test_decimal_and_int_pass_through():
    assert money(Decimal("1234.56")).value == Decimal("1234.56")
    assert money(7).value == Decimal("7")


def test_parse_result_has_no_truthiness_trap():
    parsed = money("0.00")
    with pytest.raises(TypeError):
        bool(parsed)
    assert parsed.ok is True


def test_zero_and_null_are_different_facts():
    zero, null = money("0.00"), money("")
    assert zero.value == Decimal("0.00") and zero.ok
    assert null.value is None and not null.ok


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "order", "expected"),
    [
        ("03/04/2024", "mdy", date(2024, 3, 4)),
        ("03/04/2024", "dmy", date(2024, 4, 3)),
        ("13/04/2024", None, date(2024, 4, 13)),    # >12 settles it
        ("04/13/2024", None, date(2024, 4, 13)),
        ("2024-03-04", None, date(2024, 3, 4)),     # ISO is self-identifying
        ("2024/03/04", None, date(2024, 3, 4)),
        ("20240304", None, date(2024, 3, 4)),
        ("4-Mar-24", None, date(2024, 3, 4)),
        ("Mar 4, 2024", None, date(2024, 3, 4)),
        ("March 4, 2024", None, date(2024, 3, 4)),
        ("4 March 2024", None, date(2024, 3, 4)),
        ("SEPT 9 2019", None, date(2019, 9, 9)),
        ("03.04.2024", "dmy", date(2024, 4, 3)),
        ("3/4/24", "mdy", date(2024, 3, 4)),
        ("03/04/2024 00:00:00", "mdy", date(2024, 3, 4)),
    ],
)
def test_date_formats(raw, order, expected):
    parsed = parse_date(raw, order)
    assert parsed.value == expected, f"{raw!r} -> {parsed.value} ({parsed.reason})"


def test_ambiguous_date_without_order_is_flagged():
    parsed = parse_date("03/04/2024", None)
    assert parsed.value is None
    assert parsed.reason is NullReason.AMBIGUOUS_DATE_ORDER
    assert parsed.ambiguous is True


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("2024-02-30", NullReason.INVALID_DATE),
        ("13/13/2024", NullReason.INVALID_DATE),
        ("0007-01-01", NullReason.OUT_OF_RANGE),
        ("not a date", NullReason.UNPARSEABLE),
        ("", NullReason.EMPTY),
        ("N/A", NullReason.NOT_APPLICABLE),
    ],
)
def test_bad_dates_fail_loudly(raw, reason):
    parsed = parse_date(raw)
    assert parsed.value is None
    assert parsed.reason is reason


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("01/02/69", date(2069, 1, 2)), ("01/02/70", date(1970, 1, 2)), ("01/02/87", date(1987, 1, 2))],
)
def test_two_digit_year_pivot(raw, expected):
    assert parse_date(raw, "mdy").value == expected


def test_infer_date_order_from_a_day_above_twelve():
    inference = infer_date_order(["03/04/2024", "17/05/2024", "01/02/2024"])
    assert inference.order == "dmy"
    assert inference.confident is True
    assert parse_date("03/04/2024", inference.for_parsing).value == date(2024, 4, 3)


def test_infer_date_order_month_first():
    inference = infer_date_order(["03/14/2024", "01/02/2024"])
    assert inference.order == "mdy"
    assert inference.confident is True


def test_infer_date_order_without_evidence_is_unproven():
    inference = infer_date_order(["01/02/2024", "03/04/2024"])
    assert inference.confident is False
    assert inference.for_parsing is None
    assert parse_date("01/02/2024", inference.for_parsing).reason is (
        NullReason.AMBIGUOUS_DATE_ORDER
    )


def test_conflicting_date_evidence_is_not_confident():
    inference = infer_date_order(["13/04/2024", "04/13/2024"])
    assert inference.confident is False


def test_eu_locale_hints_day_first_but_stays_unproven():
    inference = infer_date_order(["01/02/2024"], locale="eu")
    assert inference.order == "dmy"
    assert inference.confident is False


def test_date_passthrough():
    assert parse_date(date(2024, 3, 4)).value == date(2024, 3, 4)


# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("O", ClaimStatus.OPEN),
        ("OP", ClaimStatus.OPEN),
        ("Open", ClaimStatus.OPEN),
        ("OPEN", ClaimStatus.OPEN),
        ("  open  ", ClaimStatus.OPEN),
        ("C", ClaimStatus.CLOSED),
        ("CL", ClaimStatus.CLOSED),
        ("Closed", ClaimStatus.CLOSED),
        ("CLSD", ClaimStatus.CLOSED),
        ("R", ClaimStatus.REOPENED),
        ("RE-OPENED", ClaimStatus.REOPENED),
        ("Reopened", ClaimStatus.REOPENED),
        ("", ClaimStatus.UNKNOWN),
        ("whatever", ClaimStatus.UNKNOWN),
        (None, ClaimStatus.UNKNOWN),
    ],
)
def test_status_vocabulary(raw, expected):
    assert parse_status(raw) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Y", True), ("yes", True), ("TRUE", True), ("1", True),
     ("N", False), ("no", False), ("0", False), ("", None), ("maybe", None)],
)
def test_litigation_flags(raw, expected):
    assert parse_bool(raw) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("  Acme  Corp ", "Acme Corp"), ("N/A", None), ("--", None), ("", None), (None, None)],
)
def test_text_cells(raw, expected):
    assert parse_text(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Total claims: 12", 12), ("12", 12), ("1,234", 1234), ("none", None), ("", None)],
)
def test_parse_int(raw, expected):
    assert parse_int(raw) == expected


def test_clean_text_folds_exotic_whitespace():
    assert clean_text("a  b c") == "a b c"


def test_normalize_label():
    assert normalize_label("  Paid   Indemnity ($) ") == "paid indemnity"
    assert normalize_label("TOTAL_INCURRED") == "total incurred"
