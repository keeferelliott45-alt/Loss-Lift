"""Exhaustive tests for core/normalize.py — every format in CLAUDE.md §4.

Money parsing is the single highest-bug-density area of the product; every
case in the spec's format table appears here, plus the ambiguous cases under
each resolution path (no evidence / us / eu).
"""

from datetime import date
from decimal import Decimal

import pytest

from core.normalize import (
    classify_token_date_order,
    classify_token_locale,
    infer_date_order,
    infer_locale,
    normalize_status,
    parse_count,
    parse_date,
    parse_money,
)
from core.schema import ClaimStatus, NullReason


def D(s: str) -> Decimal:
    return Decimal(s)


# ---------------------------------------------------------------------------
# parse_money — unambiguous formats from the §4 table
# ---------------------------------------------------------------------------


class TestMoneyUnambiguous:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1,234.56", D("1234.56")),  # US thousands + decimal
            ("1.234,56", D("1234.56")),  # EU thousands + decimal
            ("1 234,56", D("1234.56")),  # French space grouping
            ("1 234,56", D("1234.56")),  # non-breaking space
            ("1 234,56", D("1234.56")),  # narrow no-break space
            ("(1,234.56)", D("-1234.56")),  # accounting negative
            ("1,234.56-", D("-1234.56")),  # mainframe trailing minus
            ("-1,234.56", D("-1234.56")),  # leading minus
            ("-0-", D("0")),  # mainframe zero
            ("-0.00-", D("0")),
            ("1,234.56 CR", D("-1234.56")),  # credit = negative
            ("1,234.56CR", D("-1234.56")),
            ("0.00", D("0")),  # zero is a fact, not null
            ("0", D("0")),
            ("1234", D("1234")),
            ("1234567", D("1234567")),
            ("1,23", D("1.23")),  # 2 digits after comma: decimal
            ("1.5", D("1.5")),
            ("12,3456", D("12.3456")),  # 4 digits after: decimal
            ("1,234,567.89", D("1234567.89")),
            ("1.234.567,89", D("1234567.89")),
            ("1,234,567", D("1234567")),  # repeated separator: grouping
            ("1.234.567", D("1234567")),
            ("1234,567", D("1234.567")),  # 4-digit lead can't be grouping
            ("1'234.56", D("1234.56")),  # Swiss apostrophe grouping
            ("(1.234,56)", D("-1234.56")),
            ("1 234,56-", D("-1234.56")),
        ],
    )
    def test_parses_without_locale(self, raw, expected):
        result = parse_money(raw)
        assert result.reason is None
        assert result.value == expected

    def test_zero_is_not_null(self):
        assert parse_money("0.00").value == D("0")
        assert parse_money("0.00").reason is None
        assert parse_money("").value is None  # the two facts stay distinct


class TestMoneyCurrency:
    def test_currency_prefix(self):
        result = parse_money("$1,234", locale="us")
        assert result.value == D("1234")
        assert result.currency_symbol == "$"

    def test_currency_suffix_eu(self):
        result = parse_money("1.234 €", locale="eu")
        assert result.value == D("1234")
        assert result.currency_symbol == "€"

    def test_currency_with_unambiguous_number(self):
        result = parse_money("$1,234.56")
        assert result.value == D("1234.56")
        assert result.currency_symbol == "$"

    def test_currency_inside_parens(self):
        result = parse_money("($1,234.56)")
        assert result.value == D("-1234.56")
        assert result.currency_symbol == "$"

    def test_currency_with_leading_minus(self):
        result = parse_money("$ -1,234.56")
        assert result.value == D("-1234.56")
        assert result.currency_symbol == "$"

    @pytest.mark.parametrize("raw,symbol", [("£500.25", "£"), ("¥1,000.50", "¥")])
    def test_other_symbols(self, raw, symbol):
        result = parse_money(raw)
        assert result.reason is None
        assert result.currency_symbol == symbol


class TestMoneyNulls:
    @pytest.mark.parametrize("raw", ["N/A", "NA", "n/a", "na", "N.A.", "none"])
    def test_na_tokens_are_null_not_zero(self, raw):
        result = parse_money(raw)
        assert result.value is None
        assert result.reason == NullReason.NA_TOKEN

    @pytest.mark.parametrize("raw", ["", "   ", None, " ", "-"])
    def test_blank_is_null_not_zero(self, raw):
        result = parse_money(raw)
        assert result.value is None
        assert result.reason == NullReason.BLANK

    def test_double_dash_defaults_to_null_with_reason(self):
        result = parse_money("--")
        assert result.value is None
        assert result.reason == NullReason.DOUBLE_DASH

    def test_double_dash_zero_when_profile_says_so(self):
        result = parse_money("--", double_dash_is_zero=True)
        assert result.value == D("0")
        assert result.reason is None

    @pytest.mark.parametrize(
        "raw",
        ["abc", "12..34", "1,23,45", "1,2345.00", "..", "$", "()", "1,2,3", "--1--"],
    )
    def test_garbage_fails_loud(self, raw):
        result = parse_money(raw)
        assert result.value is None
        assert result.reason is not None


class TestMoneyAmbiguous:
    """One separator, exactly three digits after it: never guess."""

    @pytest.mark.parametrize("raw", ["1,234", "1.234", "$1,234", "1.234 €", "12,345", "123.456"])
    def test_flagged_without_evidence(self, raw):
        result = parse_money(raw)  # locale=None: no evidence
        assert result.value is None
        assert result.reason == NullReason.AMBIGUOUS_SEPARATOR

    @pytest.mark.parametrize(
        ("raw", "locale", "expected"),
        [
            ("1,234", "us", D("1234")),  # comma is grouping in US
            ("1,234", "eu", D("1.234")),  # comma is decimal in EU
            ("1.234", "us", D("1.234")),
            ("1.234", "eu", D("1234")),
            ("$1,234", "us", D("1234")),
            ("1.234 €", "eu", D("1234")),
            ("(12,345)", "us", D("-12345")),
        ],
    )
    def test_resolved_by_evidenced_locale(self, raw, locale, expected):
        result = parse_money(raw, locale=locale)
        assert result.reason is None
        assert result.value == expected

    def test_invalid_locale_rejected(self):
        with pytest.raises(ValueError):
            parse_money("1,234", locale="fr")


class TestLocaleInference:
    @pytest.mark.parametrize(
        ("token", "verdict"),
        [
            ("1,234.56", "us"),
            ("1.234,56", "eu"),
            ("1,234,567", "us"),
            ("1.234.567", "eu"),
            ("1,23", "eu"),  # comma decimal
            ("1.5", "us"),  # dot decimal
            ("0.00", "us"),
            ("0,00", "eu"),
            ("1,234", None),  # the ambiguous shape
            ("1.234", None),
            ("1234", None),  # no separator, no evidence
            ("N/A", None),
            ("", None),
            (None, None),
        ],
    )
    def test_single_token_classification(self, token, verdict):
        assert classify_token_locale(token) == verdict

    def test_document_level_us(self):
        assert infer_locale(["1,234", "5,678.90", "12"]) == "us"

    def test_document_level_eu(self):
        assert infer_locale(["1.234", "5.678,90", "12"]) == "eu"

    def test_no_evidence_returns_none(self):
        assert infer_locale(["1,234", "5,678", "12"]) is None

    def test_conflicting_evidence_returns_none(self):
        assert infer_locale(["1,234.56", "1.234,56"]) is None

    def test_empty_returns_none(self):
        assert infer_locale([]) is None


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


class TestDateUnambiguous:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("2024-03-04", date(2024, 3, 4)),  # ISO always unambiguous
            ("2024/03/04", date(2024, 3, 4)),
            ("13/04/2024", date(2024, 4, 13)),  # first > 12: day-first
            ("04/13/2024", date(2024, 4, 13)),  # second > 12: month-first
            ("31/12/2024", date(2024, 12, 31)),
            ("12/31/2024", date(2024, 12, 31)),
            ("03/03/2024", date(2024, 3, 3)),  # readings coincide
            ("Mar 4, 2024", date(2024, 3, 4)),
            ("March 4, 2024", date(2024, 3, 4)),
            ("4 Mar 2024", date(2024, 3, 4)),
            ("4 March 2024", date(2024, 3, 4)),
            ("Feb 29, 2024", date(2024, 2, 29)),  # leap day
            ("25/12/23", date(2023, 12, 25)),  # two-digit year
        ],
    )
    def test_parses_without_order_hint(self, raw, expected):
        result = parse_date(raw)
        assert result.reason is None
        assert result.value == expected


class TestDateAmbiguous:
    def test_flagged_without_evidence(self):
        result = parse_date("03/04/2024")  # Mar 4 or Apr 3
        assert result.value is None
        assert result.reason == NullReason.AMBIGUOUS_DATE_ORDER

    def test_resolved_month_first(self):
        result = parse_date("03/04/2024", day_first=False)
        assert result.value == date(2024, 3, 4)

    def test_resolved_day_first(self):
        result = parse_date("03/04/2024", day_first=True)
        assert result.value == date(2024, 4, 3)

    def test_two_digit_year_ambiguous(self):
        result = parse_date("3/4/24")
        assert result.reason == NullReason.AMBIGUOUS_DATE_ORDER
        assert parse_date("3/4/24", day_first=False).value == date(2024, 3, 4)

    def test_self_evident_overrides_wrong_hint(self):
        # 03/25 can only be March 25 even if the document is day-first.
        result = parse_date("03/25/2024", day_first=True)
        assert result.value == date(2024, 3, 25)


class TestDateInvalid:
    @pytest.mark.parametrize("raw", ["99/99/9999", "13/13/2024", "00/00/2024", "31/02/2024 "])
    def test_invalid_dates(self, raw):
        result = parse_date(raw.strip())
        assert result.value is None
        assert result.reason == NullReason.INVALID_DATE

    def test_feb_29_non_leap(self):
        result = parse_date("Feb 29, 2023")
        assert result.value is None
        assert result.reason == NullReason.INVALID_DATE

    @pytest.mark.parametrize("raw", ["not a date", "2024", "Marchtember 4, 2024"])
    def test_unparseable(self, raw):
        result = parse_date(raw)
        assert result.value is None
        assert result.reason == NullReason.UNPARSEABLE

    @pytest.mark.parametrize("raw,reason", [("", NullReason.BLANK), (None, NullReason.BLANK), ("N/A", NullReason.NA_TOKEN)])
    def test_null_inputs(self, raw, reason):
        result = parse_date(raw)
        assert result.value is None
        assert result.reason == reason


class TestDateOrderInference:
    @pytest.mark.parametrize(
        ("token", "verdict"),
        [
            ("25/06/2024", True),
            ("06/25/2024", False),
            ("03/04/2024", None),
            ("2024-03-04", None),  # ISO carries no order signal
            ("garbage", None),
            (None, None),
        ],
    )
    def test_single_token(self, token, verdict):
        assert classify_token_date_order(token) == verdict

    def test_document_day_first(self):
        assert infer_date_order(["03/04/2024", "25/06/2024"]) is True

    def test_document_month_first(self):
        assert infer_date_order(["03/04/2024", "06/25/2024"]) is False

    def test_no_evidence(self):
        assert infer_date_order(["03/04/2024", "01/02/2024"]) is None

    def test_conflicting_evidence(self):
        assert infer_date_order(["25/06/2024", "06/25/2024"]) is None


# ---------------------------------------------------------------------------
# Status vocabulary
# ---------------------------------------------------------------------------


class TestStatus:
    @pytest.mark.parametrize("raw", ["O", "OP", "Open", "OPEN", "open", " open "])
    def test_open(self, raw):
        assert normalize_status(raw) == ClaimStatus.OPEN

    @pytest.mark.parametrize("raw", ["C", "CL", "Closed", "CLOSED", "CLSD", "closed"])
    def test_closed(self, raw):
        assert normalize_status(raw) == ClaimStatus.CLOSED

    @pytest.mark.parametrize("raw", ["R", "RO", "Reopen", "REOPENED", "Re-Opened", "RE OPEN"])
    def test_reopened(self, raw):
        assert normalize_status(raw) == ClaimStatus.REOPENED

    @pytest.mark.parametrize("raw", ["", None, "pending", "XX", "42"])
    def test_unknown(self, raw):
        assert normalize_status(raw) == ClaimStatus.UNKNOWN


class TestParseCount:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("12", 12), ("1,234", 1234), (" 7 ", 7), ("", None), (None, None), ("abc", None), ("12.5", None)],
    )
    def test_counts(self, raw, expected):
        assert parse_count(raw) == expected
