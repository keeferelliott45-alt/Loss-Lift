"""Number, date, and status normalization (CLAUDE.md §4).

The contract for every parser here: a value that cannot be parsed with
confidence comes back as ``None`` plus a reason — never ``0``, never a guess.

Locale handling
---------------
``parse_money`` takes ``locale``:

- ``"us"`` / ``"eu"``: an *evidenced* convention — either inferred from an
  unambiguous token elsewhere in the same document (``infer_locale``) or pinned
  by a human-confirmed carrier profile. Ambiguous tokens (one separator with
  exactly three digits after it, e.g. ``1,234``) are resolved with it.
- ``None``: no evidence. Ambiguous tokens return ``None`` with reason
  ``AMBIGUOUS_SEPARATOR`` and are surfaced for human review (R-15).
  Unambiguous tokens still parse.

The document's stored ``locale_hint`` field defaults to ``"us"`` for display,
but a *defaulted* locale is not evidence, so it never resolves an ambiguous
token — that would be guessing. Same scheme for date order via ``day_first``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

from core.schema import ClaimStatus, NullReason

# Characters treated as digit-grouping only — never a decimal separator:
# NBSP, narrow NBSP, thin space, plain space, and Swiss-style apostrophes.
_GROUPING_ONLY = ("\u00a0", "\u202f", "\u2009", " ", "'", "\u2019")

_CURRENCY_SYMBOLS = "$\u20ac\u00a3\u00a5"  # $ € £ ¥

# Exotic space characters normalized to plain spaces before parsing.
_SPACE_VARIANTS = ("\u00a0", "\u202f", "\u2009")

_NA_TOKENS = {"N/A", "NA", "N.A.", "NONE"}


@dataclass(frozen=True)
class ParsedMoney:
    value: Optional[Decimal]
    reason: Optional[str] = None
    currency_symbol: Optional[str] = None


@dataclass(frozen=True)
class ParsedDate:
    value: Optional[date]
    reason: Optional[str] = None


def parse_money(
    raw: Optional[str],
    locale: Optional[str] = None,
    *,
    double_dash_is_zero: bool = False,
) -> ParsedMoney:
    """Parse one money cell into a Decimal.

    ``double_dash_is_zero`` reflects the carrier convention for ``--`` (some
    mainframe reports print it for zero, others for "no data"). It comes from
    the carrier profile; the default is the fail-loud choice: null + reason.
    """
    if locale not in (None, "us", "eu"):
        raise ValueError(f"locale must be 'us', 'eu' or None, got {locale!r}")
    if raw is None:
        return ParsedMoney(None, NullReason.BLANK)

    s = str(raw).strip()
    for ch in _SPACE_VARIANTS:
        s = s.replace(ch, " ")
    s = s.strip()
    if not s:
        return ParsedMoney(None, NullReason.BLANK)

    upper = s.upper()
    if upper in _NA_TOKENS:
        return ParsedMoney(None, NullReason.NA_TOKEN)
    if s == "-":
        return ParsedMoney(None, NullReason.BLANK)
    if re.fullmatch(r"-0(?:[.,]0+)?-", s):
        return ParsedMoney(Decimal("0"))
    if s == "--":
        if double_dash_is_zero:
            return ParsedMoney(Decimal("0"))
        return ParsedMoney(None, NullReason.DOUBLE_DASH)

    neg_marks = 0
    currency_symbol: Optional[str] = None

    # Peel negativity markers and currency symbols from the outside in until
    # the token stabilizes; real cells combine them freely ("($1,234.56)",
    # "$ -1,234.56", "1,234.56 CR").
    changed = True
    while changed:
        changed = False
        s = s.strip()
        if len(s) >= 2 and s.startswith("(") and s.endswith(")"):
            neg_marks += 1
            s = s[1:-1]
            changed = True
            continue
        m = re.match(r"^(.*\S)\s*(CR)$", s, flags=re.IGNORECASE)
        if m:
            neg_marks += 1
            s = m.group(1)
            changed = True
            continue
        if len(s) > 1 and s.endswith("-"):
            neg_marks += 1
            s = s[:-1]
            changed = True
            continue
        if len(s) > 1 and s.startswith("-"):
            neg_marks += 1
            s = s[1:]
            changed = True
            continue
        if s and s[0] in _CURRENCY_SYMBOLS:
            currency_symbol = s[0]
            s = s[1:]
            changed = True
            continue
        if s and s[-1] in _CURRENCY_SYMBOLS:
            currency_symbol = s[-1]
            s = s[:-1]
            changed = True
            continue

    s = s.strip()
    if not s:
        return ParsedMoney(None, NullReason.UNPARSEABLE, currency_symbol)
    if neg_marks > 1:
        # More than one negativity marker ("--1--") is not a number a carrier
        # prints; flag it rather than guessing at the sign.
        return ParsedMoney(None, NullReason.UNPARSEABLE, currency_symbol)
    negative = neg_marks == 1

    for ch in _GROUPING_ONLY:
        s = s.replace(ch, "")

    if not re.fullmatch(r"[0-9.,]+", s):
        return ParsedMoney(None, NullReason.UNPARSEABLE, currency_symbol)
    if not s[0].isdigit() or not s[-1].isdigit():
        return ParsedMoney(None, NullReason.UNPARSEABLE, currency_symbol)

    has_comma = "," in s
    has_dot = "." in s

    if has_comma and has_dot:
        # Rule 1: the last separator encountered is the decimal separator.
        decimal_sep = "," if s.rfind(",") > s.rfind(".") else "."
        group_sep = "." if decimal_sep == "," else ","
        int_part, _, frac_part = s.rpartition(decimal_sep)
        if decimal_sep in int_part or not _valid_grouping(int_part, group_sep):
            return ParsedMoney(None, NullReason.UNPARSEABLE, currency_symbol)
        number = int_part.replace(group_sep, "") + "." + frac_part
    elif has_comma or has_dot:
        sep = "," if has_comma else "."
        parts = s.split(sep)
        if len(parts) > 2:
            # Multiple occurrences of one separator: grouping.
            if not _valid_grouping(s, sep):
                return ParsedMoney(None, NullReason.UNPARSEABLE, currency_symbol)
            number = s.replace(sep, "")
        else:
            head, tail = parts
            if len(tail) == 3 and 1 <= len(head) <= 3:
                # Rule 2: one separator, exactly three digits after it, and a
                # plausible leading group — genuinely ambiguous.
                if locale is None:
                    return ParsedMoney(
                        None, NullReason.AMBIGUOUS_SEPARATOR, currency_symbol
                    )
                treat_as_decimal = (locale == "us") == (sep == ".")
                number = head + "." + tail if treat_as_decimal else head + tail
            elif len(tail) == 3:
                # Leading group longer than 3 digits can't be grouping.
                number = head + "." + tail
            else:
                number = head + "." + tail
    else:
        number = s

    try:
        value = Decimal(number)
    except InvalidOperation:
        return ParsedMoney(None, NullReason.UNPARSEABLE, currency_symbol)

    if negative:
        value = -value
    return ParsedMoney(value, None, currency_symbol)


def _valid_grouping(int_part: str, group_sep: str) -> bool:
    """True when digit groups form a valid grouped integer (1–3 digits first,
    exactly 3 in every later group). ``1,23,45`` and ``12..34`` fail loud."""
    groups = int_part.split(group_sep)
    if any(not g for g in groups):
        return False
    if len(groups) == 1:
        return groups[0].isdigit()
    if not (1 <= len(groups[0]) <= 3):
        return False
    return all(len(g) == 3 for g in groups[1:])


def classify_token_locale(raw: Optional[str]) -> Optional[str]:
    """Return "us"/"eu" when a single token unambiguously shows its number
    convention, else None."""
    if raw is None:
        return None
    s = str(raw).strip()
    for ch in _SPACE_VARIANTS:
        s = s.replace(ch, " ")
    # Strip decoration the same way parse_money does, minus sign handling.
    s = re.sub(r"[()\-]|(?i:CR)$", "", s.strip()).strip()
    for ch in _CURRENCY_SYMBOLS:
        s = s.replace(ch, "")
    for ch in _GROUPING_ONLY:
        s = s.replace(ch, "")
    s = s.strip()
    if not re.fullmatch(r"[0-9.,]+", s or ""):
        return None
    has_comma = "," in s
    has_dot = "." in s
    if has_comma and has_dot:
        return "us" if s.rfind(".") > s.rfind(",") else "eu"
    if has_comma or has_dot:
        sep = "," if has_comma else "."
        parts = s.split(sep)
        if len(parts) > 2:
            # Repeated separator = grouping: 1,234,567 is US, 1.234.567 is EU.
            if _valid_grouping(s, sep):
                return "us" if sep == "," else "eu"
            return None
        head, tail = parts
        if len(tail) == 3 and 1 <= len(head) <= 3:
            return None  # the ambiguous case
        # Any other shape makes the separator a decimal point.
        return "us" if sep == "." else "eu"
    return None


def infer_locale(tokens: Iterable[Optional[str]]) -> Optional[str]:
    """Document-level locale inference (§4 step 3).

    Returns the convention when every unambiguous token agrees on it.
    Conflicting evidence or no evidence returns None — never guess.
    """
    verdicts = {v for v in (classify_token_locale(t) for t in tokens) if v}
    if len(verdicts) == 1:
        return verdicts.pop()
    return None


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

_MONTHS = {
    "JAN": 1, "JANUARY": 1, "FEB": 2, "FEBRUARY": 2, "MAR": 3, "MARCH": 3,
    "APR": 4, "APRIL": 4, "MAY": 5, "JUN": 6, "JUNE": 6, "JUL": 7, "JULY": 7,
    "AUG": 8, "AUGUST": 8, "SEP": 9, "SEPT": 9, "SEPTEMBER": 9, "OCT": 10,
    "OCTOBER": 10, "NOV": 11, "NOVEMBER": 11, "DEC": 12, "DECEMBER": 12,
}

_NUMERIC_DATE_RE = re.compile(r"^(\d{1,4})[/\-.](\d{1,2})[/\-.](\d{1,4})$")
_MONTH_NAME_MDY_RE = re.compile(r"^([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})$")
_MONTH_NAME_DMY_RE = re.compile(r"^(\d{1,2})\s+([A-Za-z]+)\.?,?\s+(\d{4})$")


def _expand_year(y: int) -> int:
    if y >= 100:
        return y
    return 2000 + y if y < 50 else 1900 + y


def _make_date(year: int, month: int, day: int) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_date(raw: Optional[str], day_first: Optional[bool] = None) -> ParsedDate:
    """Parse one date cell.

    ``day_first`` mirrors the money locale contract: True/False is evidenced
    (document-level inference via ``infer_date_order`` or a confirmed
    profile); None means no evidence, so an ambiguous numeric date returns
    ``AMBIGUOUS_DATE_ORDER`` instead of a guess.
    """
    if raw is None:
        return ParsedDate(None, NullReason.BLANK)
    s = str(raw).strip()
    if not s:
        return ParsedDate(None, NullReason.BLANK)
    if s.upper() in _NA_TOKENS:
        return ParsedDate(None, NullReason.NA_TOKEN)

    m = _MONTH_NAME_MDY_RE.match(s)
    if m:
        month = _MONTHS.get(m.group(1).upper())
        if month is None:
            return ParsedDate(None, NullReason.UNPARSEABLE)
        d = _make_date(int(m.group(3)), month, int(m.group(2)))
        return ParsedDate(d) if d else ParsedDate(None, NullReason.INVALID_DATE)

    m = _MONTH_NAME_DMY_RE.match(s)
    if m:
        month = _MONTHS.get(m.group(2).upper())
        if month is None:
            return ParsedDate(None, NullReason.UNPARSEABLE)
        d = _make_date(int(m.group(3)), month, int(m.group(1)))
        return ParsedDate(d) if d else ParsedDate(None, NullReason.INVALID_DATE)

    m = _NUMERIC_DATE_RE.match(s)
    if not m:
        return ParsedDate(None, NullReason.UNPARSEABLE)
    a, b, c = (int(g) for g in m.groups())

    # ISO: a four-digit leading component is always the year.
    if len(m.group(1)) == 4:
        d = _make_date(a, b, c)
        return ParsedDate(d) if d else ParsedDate(None, NullReason.INVALID_DATE)

    year = _expand_year(c)
    month_first = _make_date(year, a, b)
    day_first_reading = _make_date(year, b, a)

    if month_first is None and day_first_reading is None:
        return ParsedDate(None, NullReason.INVALID_DATE)
    if month_first is not None and day_first_reading is None:
        return ParsedDate(month_first)
    if day_first_reading is not None and month_first is None:
        return ParsedDate(day_first_reading)
    # Both readings are valid dates — genuinely ambiguous (03/04/2024).
    if month_first == day_first_reading:  # e.g. 03/03/2024
        return ParsedDate(month_first)
    if day_first is None:
        return ParsedDate(None, NullReason.AMBIGUOUS_DATE_ORDER)
    return ParsedDate(day_first_reading if day_first else month_first)


def classify_token_date_order(raw: Optional[str]) -> Optional[bool]:
    """True (day-first) / False (month-first) when one numeric date betrays
    its order, else None."""
    if raw is None:
        return None
    m = _NUMERIC_DATE_RE.match(str(raw).strip())
    if not m or len(m.group(1)) == 4:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    if a > 12 and b <= 12:
        return True
    if b > 12 and a <= 12:
        return False
    return None


def infer_date_order(tokens: Iterable[Optional[str]]) -> Optional[bool]:
    """Document-level date-order inference (§4). All evidence must agree."""
    verdicts = {
        v for v in (classify_token_date_order(t) for t in tokens) if v is not None
    }
    if len(verdicts) == 1:
        return verdicts.pop()
    return None


# --------------------------------------------------------------------------
# Status vocabulary
# --------------------------------------------------------------------------

_STATUS_EXACT = {
    "O": ClaimStatus.OPEN,
    "OP": ClaimStatus.OPEN,
    "OPEN": ClaimStatus.OPEN,
    "C": ClaimStatus.CLOSED,
    "CL": ClaimStatus.CLOSED,
    "CLSD": ClaimStatus.CLOSED,
    "CLOSED": ClaimStatus.CLOSED,
    "R": ClaimStatus.REOPENED,
    "RO": ClaimStatus.REOPENED,
    "REOPEN": ClaimStatus.REOPENED,
    "REOPENED": ClaimStatus.REOPENED,
    "RE-OPEN": ClaimStatus.REOPENED,
    "RE-OPENED": ClaimStatus.REOPENED,
}


def normalize_status(raw: Optional[str]) -> ClaimStatus:
    if raw is None:
        return ClaimStatus.UNKNOWN
    s = str(raw).strip().upper()
    if not s:
        return ClaimStatus.UNKNOWN
    if s in _STATUS_EXACT:
        return _STATUS_EXACT[s]
    if s.startswith(("REOP", "RE-OP", "RE OP")):
        return ClaimStatus.REOPENED
    if s.startswith("OPEN"):
        return ClaimStatus.OPEN
    if s.startswith(("CLOSED", "CLSD", "CLOSE")):
        return ClaimStatus.CLOSED
    return ClaimStatus.UNKNOWN


def parse_count(raw: Optional[str]) -> Optional[int]:
    """Parse a printed claim count ("Total Claims: 12" style values arrive
    here already reduced to their numeric token)."""
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "")
    if not s.isdigit():
        return None
    return int(s)
