"""Number, date and vocabulary normalisation (spec section 4).

This is the single highest-bug-density area in the product, so the rules are
written out explicitly rather than left to a permissive regex.

Two ideas run through the whole module:

* **A null is not a zero.** Every failure returns ``None`` plus a
  :class:`~core.schema.NullReason`, never a silent ``0``.
* **Never guess.** ``1.234`` is 1234 in Europe and 1.234 in the US.  When the
  document itself provides no evidence either way the value comes back null
  with ``AMBIGUOUS_SEPARATOR`` and the review screen asks a human.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Iterable, Literal

from core.schema import ClaimStatus, NullReason

Locale = Literal["us", "eu"]
DateOrder = Literal["mdy", "dmy", "ymd"]

# --------------------------------------------------------------------------
# Character-level cleaning
# --------------------------------------------------------------------------

#: Dash-ish characters that mean "minus" or "placeholder" in carrier reports.
_DASHES = "−–—‐‑‒⁃"
_DASH_TRANSLATION = {ord(ch): "-" for ch in _DASHES}

#: Currency symbol -> ISO 4217.  Two-character symbols are matched first.
CURRENCY_SYMBOLS: dict[str, str] = {
    "C$": "CAD",
    "A$": "AUD",
    "US$": "USD",
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₤": "GBP",
    "₹": "INR",
    "₽": "RUB",
}

CURRENCY_CODES: frozenset[str] = frozenset(
    {"USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF", "MXN", "NZD", "SEK", "NOK", "DKK"}
)

_NA_TOKENS: frozenset[str] = frozenset(
    {"N/A", "NA", "N.A.", "N/A.", "NIL", "NONE", "NULL", "N\\A", "NOT APPLICABLE"}
)

#: Thousands separators that are never decimal separators.
_HARD_THOUSANDS = " '’"


def clean_text(raw: object) -> str:
    """Canonicalise whitespace and exotic punctuation, without changing meaning."""
    if raw is None:
        return ""
    text = raw if isinstance(raw, str) else str(raw)
    # NFKC folds non-breaking/thin spaces and full-width digits to ASCII.
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_DASH_TRANSLATION)
    text = text.replace(" ", " ").replace(" ", " ").replace(" ", " ")
    return " ".join(text.split())


def normalize_label(raw: object) -> str:
    """Fold a column header for comparison: lowercase, alphanumerics only.

    "#" becomes the word it stands for. Dropping it turned "Claim # / OneClaim
    #" into "claim oneclaim", which carries no token saying it is a number, so
    the column that identifies every claim mapped to nothing. The symbol means
    "number" wherever a loss run uses it.
    """
    text = clean_text(raw).lower().replace("#", " number ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


# --------------------------------------------------------------------------
# Number parsing
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class NumberParse:
    """The outcome of parsing one numeric cell."""

    value: Decimal | None
    reason: NullReason | None = None
    raw: str = ""
    currency: str | None = None
    ambiguous: bool = False

    @property
    def ok(self) -> bool:
        return self.value is not None

    def __bool__(self) -> bool:  # pragma: no cover - guard against `if parse:`
        raise TypeError(
            "NumberParse has no truth value; a parsed 0 is not falsey. Use .ok"
        )


def _fail(raw: str, reason: NullReason, currency: str | None = None) -> NumberParse:
    return NumberParse(
        value=None,
        reason=reason,
        raw=raw,
        currency=currency,
        ambiguous=reason is NullReason.AMBIGUOUS_SEPARATOR,
    )


def _strip_currency(text: str) -> tuple[str, str | None]:
    """Remove a currency symbol or ISO code, returning what it was."""
    found: str | None = None
    for symbol in sorted(CURRENCY_SYMBOLS, key=len, reverse=True):
        if symbol in text:
            found = CURRENCY_SYMBOLS[symbol]
            text = text.replace(symbol, " ")
            break
    match = re.search(r"(?<![A-Z])(%s)(?![A-Z])" % "|".join(CURRENCY_CODES), text.upper())
    if match:
        code = match.group(1)
        if found is None:
            found = code
        start, end = match.span()
        text = text[:start] + " " + text[end:]
    return " ".join(text.split()), found


def _grouping_is_valid(integer_part: str, separator: str) -> bool:
    """``1,234,567`` yes; ``12,34,567`` no; ``1234,567`` no."""
    if separator not in integer_part:
        return True
    pattern = r"^\d{1,3}(%s\d{3})+$" % re.escape(separator)
    return re.match(pattern, integer_part) is not None


def _to_decimal(text: str, decimal_sep: str | None, raw: str) -> NumberParse:
    """Strip thousands separators and build the Decimal."""
    if decimal_sep:
        integer_part, _, fraction = text.rpartition(decimal_sep)
    else:
        integer_part, fraction = text, ""

    stripped_integer = re.sub(r"[.,\s'’]", "", integer_part)
    if fraction and not fraction.isdigit():
        return _fail(raw, NullReason.UNPARSEABLE)
    if stripped_integer and not stripped_integer.isdigit():
        return _fail(raw, NullReason.UNPARSEABLE)
    if not stripped_integer and not fraction:
        return _fail(raw, NullReason.UNPARSEABLE)

    literal = (stripped_integer or "0") + ("." + fraction if fraction else "")
    try:
        return NumberParse(value=Decimal(literal), raw=raw)
    except InvalidOperation:  # pragma: no cover - defensive
        return _fail(raw, NullReason.UNPARSEABLE)


def parse_money(
    raw: object,
    locale: Locale | None = None,
    *,
    dash_means_zero: bool = False,
) -> NumberParse:
    """Parse one money cell.

    ``locale`` is the *document-level* locale once it has been established by
    :func:`infer_locale`, or ``None`` when the document gave no evidence.  With
    ``None``, genuinely ambiguous tokens fail rather than guess.

    ``dash_means_zero`` reflects a per-carrier convention: ``--`` is zero for
    some carriers and "no data" for others, so the profile decides.
    """
    original = raw if isinstance(raw, str) else ("" if raw is None else str(raw))

    if isinstance(raw, Decimal):
        return NumberParse(value=raw, raw=original)
    if isinstance(raw, bool):
        return _fail(original, NullReason.UNPARSEABLE)
    if isinstance(raw, int):
        return NumberParse(value=Decimal(raw), raw=original)
    if isinstance(raw, float):
        raise TypeError(
            "parse_money refuses floats: money read as float has already lost "
            "precision. Pass the source string or a Decimal."
        )

    text = clean_text(raw)
    if not text:
        return _fail(original, NullReason.EMPTY)

    upper = text.upper()
    if upper in _NA_TOKENS:
        return _fail(original, NullReason.NOT_APPLICABLE)

    # Mainframe zero: -0-, -00-
    if re.fullmatch(r"-0+-", text):
        return NumberParse(value=Decimal("0"), raw=original)

    # Dash placeholders: "--", "-", "---"
    if re.fullmatch(r"-+", text):
        if dash_means_zero:
            return NumberParse(value=Decimal("0"), raw=original)
        return _fail(original, NullReason.DASH_PLACEHOLDER)

    sign = Decimal(1)

    # Credit / debit suffix or prefix: "1,234.56 CR"
    credit = re.search(r"(?:^|\s)(CR|DR)(?:\s|$|\.)", upper)
    if credit:
        if credit.group(1) == "CR":
            sign = -sign
        start, end = credit.span(1)
        text = text[:start] + " " + text[end:]
        text = " ".join(text.split())

    # Accounting negative: (1,234.56)
    if text.startswith("(") and text.endswith(")"):
        sign = -sign
        text = text[1:-1].strip()
    elif "(" in text or ")" in text:
        return _fail(original, NullReason.UNPARSEABLE)

    text, currency = _strip_currency(text)
    if not text:
        return _fail(original, NullReason.UNPARSEABLE, currency)

    # Trailing minus (mainframe) then leading minus.
    if text.endswith("-"):
        sign = -sign
        text = text[:-1].strip()
    if text.startswith("-"):
        sign = -sign
        text = text[1:].strip()
    elif text.startswith("+"):
        text = text[1:].strip()

    if not text:
        return _fail(original, NullReason.UNPARSEABLE, currency)

    # Percentages and free text are not money.
    if not re.fullmatch(r"[\d.,\s'’]+", text):
        return _fail(original, NullReason.UNPARSEABLE, currency)
    if not any(ch.isdigit() for ch in text):
        return _fail(original, NullReason.UNPARSEABLE, currency)

    comma_count = text.count(",")
    dot_count = text.count(".")

    decimal_sep: str | None = None

    if comma_count and dot_count:
        # Rule 1: the last separator encountered is the decimal separator.
        decimal_sep = "," if text.rfind(",") > text.rfind(".") else "."
        thousands_sep = "." if decimal_sep == "," else ","
        integer_part = text.rpartition(decimal_sep)[0]
        if not _grouping_is_valid(integer_part.replace(" ", ""), thousands_sep):
            return _fail(original, NullReason.UNPARSEABLE, currency)
        if decimal_sep in integer_part:
            return _fail(original, NullReason.UNPARSEABLE, currency)

    elif comma_count or dot_count:
        separator = "," if comma_count else "."
        count = comma_count or dot_count
        head, _, tail = text.rpartition(separator)
        digits_before = re.sub(r"\D", "", head)
        digits_after = re.sub(r"\D", "", tail)

        if count > 1:
            # Repeated separator can only be grouping: 1,234,567
            if not _grouping_is_valid(text.replace(" ", ""), separator):
                return _fail(original, NullReason.UNPARSEABLE, currency)
            decimal_sep = None
        elif len(digits_after) != 3:
            # Rule 2 does not apply: a lone separator with anything other than
            # three digits after it is a decimal point in both conventions.
            decimal_sep = separator
        elif not digits_before or len(digits_before) > 3:
            # 12345.678 cannot be grouping, so it is a decimal.
            decimal_sep = separator
        else:
            # Genuinely ambiguous: 1,234 / 1.234
            if locale is None:
                return _fail(original, NullReason.AMBIGUOUS_SEPARATOR, currency)
            if locale == "us":
                decimal_sep = "." if separator == "." else None
            else:
                decimal_sep = "," if separator == "," else None
    else:
        decimal_sep = None

    parsed = _to_decimal(text, decimal_sep, original)
    if not parsed.ok:
        return NumberParse(
            value=None, reason=parsed.reason, raw=original, currency=currency
        )
    return NumberParse(value=sign * parsed.value, raw=original, currency=currency)


def parse_int(raw: object) -> int | None:
    """Pull an integer out of a cell such as ``"Total claims: 12"``."""
    text = clean_text(raw)
    if not text:
        return None
    match = re.search(r"\d[\d,.\s']*", text)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(0))
    return int(digits) if digits else None


# --------------------------------------------------------------------------
# Document-level locale inference
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LocaleInference:
    """What the document itself says about its number convention."""

    locale: Locale = "us"
    confident: bool = False
    evidence: str | None = None
    us_votes: int = 0
    eu_votes: int = 0

    @property
    def for_parsing(self) -> Locale | None:
        """The locale to pass to :func:`parse_money` — ``None`` if unproven."""
        return self.locale if self.confident else None


def _locale_vote(token: str) -> tuple[Locale | None, str | None]:
    """What, if anything, one token proves about the document's convention."""
    text = clean_text(token)
    if not re.search(r"\d", text):
        return None, None
    text = re.sub(r"[^\d.,]", "", text)
    comma, dot = text.count(","), text.count(".")

    if comma and dot:
        # The last separator is the decimal one; that names the convention.
        return ("us", text) if text.rfind(".") > text.rfind(",") else ("eu", text)
    if comma > 1:
        return "us", text
    if dot > 1:
        return "eu", text
    if comma == 1 and len(re.sub(r"\D", "", text.rpartition(",")[2])) != 3:
        return "eu", text
    if dot == 1 and len(re.sub(r"\D", "", text.rpartition(".")[2])) != 3:
        return "us", text
    return None, None


def infer_locale(tokens: Iterable[object], default: Locale = "us") -> LocaleInference:
    """Derive the document's number convention from its own numbers.

    Conflicting evidence is reported as *not confident* rather than resolved by
    majority: a document that uses both conventions is a document a human needs
    to look at.
    """
    us_votes = eu_votes = 0
    us_evidence = eu_evidence = None
    for token in tokens:
        vote, evidence = _locale_vote(token if isinstance(token, str) else str(token))
        if vote == "us":
            us_votes += 1
            us_evidence = us_evidence or evidence
        elif vote == "eu":
            eu_votes += 1
            eu_evidence = eu_evidence or evidence

    if us_votes and not eu_votes:
        return LocaleInference("us", True, us_evidence, us_votes, eu_votes)
    if eu_votes and not us_votes:
        return LocaleInference("eu", True, eu_evidence, us_votes, eu_votes)
    return LocaleInference(
        default, False, us_evidence or eu_evidence, us_votes, eu_votes
    )


# --------------------------------------------------------------------------
# Date parsing
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DateParse:
    value: date | None
    reason: NullReason | None = None
    raw: str = ""
    ambiguous: bool = False

    @property
    def ok(self) -> bool:
        return self.value is not None


_MONTH_NAMES: dict[str, int] = {}
for _index, _name in enumerate(
    [
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    ],
    start=1,
):
    _MONTH_NAMES[_name] = _index
    _MONTH_NAMES[_name[:3]] = _index
_MONTH_NAMES["sept"] = 9

#: Two-digit years at or below this map to 2000s, above to 1900s.  Loss runs
#: carry claims decades old, so 87 must not become 2087.
YEAR_PIVOT = 69


def _expand_year(value: int) -> int:
    if value >= 100:
        return value
    return 2000 + value if value <= YEAR_PIVOT else 1900 + value


def _build_date(year: int, month: int, day: int, raw: str) -> DateParse:
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return DateParse(None, NullReason.INVALID_DATE, raw)
    if not 1900 <= year <= 2100:
        return DateParse(None, NullReason.OUT_OF_RANGE, raw)
    try:
        return DateParse(date(year, month, day), None, raw)
    except ValueError:
        return DateParse(None, NullReason.INVALID_DATE, raw)


def parse_date(raw: object, order: DateOrder | None = None) -> DateParse:
    """Parse one date cell.

    ``order`` is the document-level day/month order once established, or
    ``None``.  Tokens that resolve themselves (a component above 12, a spelled
    month, an ISO year) never need it.
    """
    original = raw if isinstance(raw, str) else ("" if raw is None else str(raw))
    if isinstance(raw, date):
        return DateParse(raw, None, original)

    text = clean_text(raw)
    if not text:
        return DateParse(None, NullReason.EMPTY, original)
    if text.upper() in _NA_TOKENS or re.fullmatch(r"-+", text):
        return DateParse(None, NullReason.NOT_APPLICABLE, original)

    # Drop a trailing time component: "03/04/2024 00:00:00"
    text = re.sub(r"\s+\d{1,2}:\d{2}(:\d{2})?(\s*[AaPp][Mm])?$", "", text).strip()

    # Spelled month: 4-Mar-24, March 4, 2024, Mar 04 2024
    spelled = re.match(
        r"^(\d{1,2})[\s\-/.]+([A-Za-z]{3,9})\.?[\s\-/.,]+(\d{2,4})$", text
    ) or re.match(r"^([A-Za-z]{3,9})\.?[\s\-/.,]+(\d{1,2})[\s\-/.,]+(\d{2,4})$", text)
    if spelled:
        groups = spelled.groups()
        if groups[0].isdigit():
            day_text, month_text, year_text = groups
        else:
            month_text, day_text, year_text = groups
        month = _MONTH_NAMES.get(month_text.lower())
        if month is None:
            return DateParse(None, NullReason.UNPARSEABLE, original)
        return _build_date(_expand_year(int(year_text)), month, int(day_text), original)

    # Compact ISO: 20240304
    if re.fullmatch(r"\d{8}", text):
        year = int(text[:4])
        if 1900 <= year <= 2100:
            return _build_date(year, int(text[4:6]), int(text[6:8]), original)
        return DateParse(None, NullReason.UNPARSEABLE, original)

    parts = re.split(r"[\-/.\s]+", text)
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return DateParse(None, NullReason.UNPARSEABLE, original)

    first, second, third = (int(part) for part in parts)

    # ISO ordering is self-identifying.
    if len(parts[0]) == 4:
        return _build_date(first, second, third, original)

    if first > 31 or second > 31:
        return DateParse(None, NullReason.INVALID_DATE, original)

    # A component above 12 settles the order on its own.
    if first > 12 and second <= 12:
        return _build_date(_expand_year(third), second, first, original)
    if second > 12 and first <= 12:
        return _build_date(_expand_year(third), first, second, original)
    if first > 12 and second > 12:
        return DateParse(None, NullReason.INVALID_DATE, original)

    if order == "dmy":
        return _build_date(_expand_year(third), second, first, original)
    if order in ("mdy", "ymd"):
        return _build_date(_expand_year(third), first, second, original)

    return DateParse(None, NullReason.AMBIGUOUS_DATE_ORDER, original, ambiguous=True)


@dataclass(frozen=True)
class DateOrderInference:
    order: DateOrder = "mdy"
    confident: bool = False
    evidence: str | None = None
    mdy_votes: int = 0
    dmy_votes: int = 0
    #: evidence — a date settled it; locale — the number convention implies it;
    #: default — nothing did.
    source: str = "default"

    @property
    def for_parsing(self) -> DateOrder | None:
        """The order to parse with, or ``None`` to refuse.

        Spec section 4 says to fall back to ``locale_hint`` when no date proves
        the order, and to flag it — so a locale-derived order is used but is
        never reported as confident.
        """
        return self.order if self.source in ("evidence", "locale") else None


def infer_date_order(
    tokens: Iterable[object],
    locale: Locale | None = None,
    default: DateOrder = "mdy",
) -> DateOrderInference:
    """Find a date in the document whose first component is above 12.

    Falls back to the locale convention (``eu`` implies day-first) and, failing
    that, to ``default`` — flagged as unproven either way.
    """
    mdy = dmy = 0
    mdy_evidence = dmy_evidence = None
    for token in tokens:
        text = clean_text(token)
        parts = re.split(r"[\-/.\s]+", text)
        if len(parts) != 3 or not all(part.isdigit() for part in parts):
            continue
        if len(parts[0]) == 4:
            continue
        first, second = int(parts[0]), int(parts[1])
        if first > 31 or second > 31:
            continue
        if first > 12 and second <= 12:
            dmy += 1
            dmy_evidence = dmy_evidence or text
        elif second > 12 and first <= 12:
            mdy += 1
            mdy_evidence = mdy_evidence or text

    if dmy and not mdy:
        return DateOrderInference("dmy", True, dmy_evidence, mdy, dmy, "evidence")
    if mdy and not dmy:
        return DateOrderInference("mdy", True, mdy_evidence, mdy, dmy, "evidence")
    if not mdy and not dmy and locale is not None:
        order: DateOrder = "dmy" if locale == "eu" else "mdy"
        return DateOrderInference(order, False, None, mdy, dmy, "locale")
    return DateOrderInference(
        default, False, mdy_evidence or dmy_evidence, mdy, dmy, "default"
    )


# --------------------------------------------------------------------------
# Status vocabulary
# --------------------------------------------------------------------------

_STATUS_VOCAB: dict[str, ClaimStatus] = {
    "o": ClaimStatus.OPEN,
    "op": ClaimStatus.OPEN,
    "opn": ClaimStatus.OPEN,
    "open": ClaimStatus.OPEN,
    "active": ClaimStatus.OPEN,
    "pending": ClaimStatus.OPEN,
    "c": ClaimStatus.CLOSED,
    "cl": ClaimStatus.CLOSED,
    "cld": ClaimStatus.CLOSED,
    "clsd": ClaimStatus.CLOSED,
    "closed": ClaimStatus.CLOSED,
    "close": ClaimStatus.CLOSED,
    "final": ClaimStatus.CLOSED,
    "settled": ClaimStatus.CLOSED,
    "r": ClaimStatus.REOPENED,
    "ro": ClaimStatus.REOPENED,
    "reop": ClaimStatus.REOPENED,
    "reopen": ClaimStatus.REOPENED,
    "reopened": ClaimStatus.REOPENED,
    "re open": ClaimStatus.REOPENED,
    "re opened": ClaimStatus.REOPENED,
    # Closed with payment, printed separately by carriers that distinguish it
    # from a closed-without-payment claim.
    "closed paid": ClaimStatus.CLOSED_PAID,
    "closed with payment": ClaimStatus.CLOSED_PAID,
    "closed pd": ClaimStatus.CLOSED_PAID,
    "cwp": ClaimStatus.CLOSED_PAID,
    "paid and closed": ClaimStatus.CLOSED_PAID,
    # Reported, never opened. Carries no money and is not a loss.
    "report only": ClaimStatus.REPORT_ONLY,
    "record only": ClaimStatus.REPORT_ONLY,
    "incident only": ClaimStatus.REPORT_ONLY,
    "notice only": ClaimStatus.REPORT_ONLY,
    "info only": ClaimStatus.REPORT_ONLY,
    "ro only": ClaimStatus.REPORT_ONLY,
}


def parse_status(raw: object) -> ClaimStatus:
    """Fold a carrier's status vocabulary onto the canonical set.

    Anything unrecognised is UNKNOWN, never a guess at the nearest match.

    A status column is narrow and the column beside it is not, so on two of the
    corpus documents the cell arrives as "Closed GIANCARLO DE ANGELIS" or
    "Closed INSD, IV DID NOT HIT OV" -- the status, then whatever ran into it.
    The status leads and the intrusion trails, so a cell whose first word is a
    status word states that status. A cell that merely mentions one later does
    not: a description reading "vehicle was open at the time" says nothing
    about whether the claim is.
    """
    text = normalize_label(raw)
    if not text:
        return ClaimStatus.UNKNOWN
    if text in _STATUS_VOCAB:
        return _STATUS_VOCAB[text]
    leading, _, rest = text.partition(" ")
    return _STATUS_VOCAB.get(leading, ClaimStatus.UNKNOWN) if rest else ClaimStatus.UNKNOWN


_TRUE_TOKENS = frozenset({"y", "yes", "true", "t", "1", "lit", "litigated", "in suit", "suit"})
_FALSE_TOKENS = frozenset({"n", "no", "false", "f", "0", "none", "not litigated"})


def parse_bool(raw: object) -> bool | None:
    """Carrier litigation flags: Y/N, Yes/No, True/False, 1/0."""
    if isinstance(raw, bool):
        return raw
    text = normalize_label(raw)
    if not text:
        return None
    if text in _TRUE_TOKENS:
        return True
    if text in _FALSE_TOKENS:
        return False
    return None


def parse_text(raw: object) -> str | None:
    """Trim a text cell; blank and N/A come back as ``None``, never ``""``."""
    text = clean_text(raw)
    if not text or text.upper() in _NA_TOKENS or re.fullmatch(r"-+", text):
        return None
    return text


# --------------------------------------------------------------------------
# Recovery sign convention
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RecoverySignInference:
    """Whether this carrier prints recoveries as credits.

    Carriers disagree about the sign of a recovery column. Most print the
    amount recovered as a positive number; mainframe and European reports
    often print it as a credit — ``250.00-`` or ``(250.00)`` — because that is
    how it lands in the ledger.

    R-01 subtracts recovery from paid plus reserve, so reading a credit at
    face value turns a subtraction into an addition and every row fails.
    Taking the absolute value would hide that, but it would also destroy a
    genuinely negative recovery (a reversal or chargeback, which is real) and
    quietly repair a document that is actually wrong — the opposite of what
    this product is for. So the convention is *inferred from the document's
    own arithmetic* and only applied when the evidence is unanimous.
    """

    credit_convention: bool = False
    confident: bool = False
    evidence: str | None = None
    positive_votes: int = 0
    credit_votes: int = 0

    @property
    def should_negate(self) -> bool:
        return self.credit_convention and self.confident


def infer_recovery_sign(
    rows: Iterable[
        tuple[str, Decimal | None, Decimal | None, Decimal | None, Decimal | None]
    ],
    tolerance: Decimal = Decimal("0.01"),
) -> RecoverySignInference:
    """Read the sign convention off the rows' own arithmetic.

    Each row is ``(claim_number, paid_total, reserve_total, recovery_total,
    incurred_total)``. A row votes only when it can settle the question: it
    needs a non-zero recovery, an incurred total, and at least one of paid or
    reserve.

    Conflicting votes mean the document is not internally consistent, which is
    exactly when guessing would be worst — so nothing is applied and R-01
    reports the rows that do not tie.
    """
    positive = credit = 0
    evidence: str | None = None

    for claim_number, paid, reserve, recovery, incurred in rows:
        if recovery is None or incurred is None or recovery == 0:
            continue
        if paid is None and reserve is None:
            continue
        base = (paid or Decimal("0")) + (reserve or Decimal("0"))
        subtracts = abs((base - recovery) - incurred) <= tolerance
        adds = abs((base + recovery) - incurred) <= tolerance
        if subtracts and not adds:
            positive += 1
        elif adds and not subtracts:
            credit += 1
            evidence = evidence or claim_number

    if credit and not positive:
        return RecoverySignInference(True, True, evidence, positive, credit)
    return RecoverySignInference(False, positive > 0 and not credit, None, positive, credit)
