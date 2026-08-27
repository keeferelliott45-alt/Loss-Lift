"""Canonical data model (spec section 3).

Every carrier format normalises into these models.

Money is always ``Decimal``.  Floats are rejected at the validation boundary
rather than silently coerced: a float round-trip produces reconciliation
failures that are not real, and reconciliation is the product.

Nothing here imports any other LossLift module, so the schema can be used
from any stage without circular imports.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Iterable
from uuid import uuid4

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
)

# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------


class ExtractionMethod(str, Enum):
    """How a *document* was extracted."""

    DIGITAL = "digital"
    VISION = "vision"
    MIXED = "mixed"


class SourceMethod(str, Enum):
    """How a *single claim row* was extracted."""

    DIGITAL = "digital"
    VISION = "vision"
    MANUAL = "manual"


class ClaimStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    REOPENED = "REOPENED"
    UNKNOWN = "UNKNOWN"


class LineOfBusiness(str, Enum):
    WC = "WC"
    GL = "GL"
    AUTO = "AUTO"
    PROP = "PROP"
    UMB = "UMB"
    OTHER = "OTHER"


class Severity(str, Enum):
    ERROR = "ERROR"
    WARN = "WARN"
    INFO = "INFO"


class DocumentStatus(str, Enum):
    """The badge at the top of the review screen."""

    CLEAN = "CLEAN"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class NullReason(str, Enum):
    """Why a field is null.

    A null is never a zero.  Every null carries one of these so the review
    screen can tell the user what happened and how to fix it.
    """

    EMPTY = "EMPTY"                               # blank cell: no data
    NOT_APPLICABLE = "NOT_APPLICABLE"             # N/A, NA, n/a
    DASH_PLACEHOLDER = "DASH_PLACEHOLDER"         # "--": zero or null per carrier
    AMBIGUOUS_SEPARATOR = "AMBIGUOUS_SEPARATOR"   # 1.234 with no locale evidence
    AMBIGUOUS_DATE_ORDER = "AMBIGUOUS_DATE_ORDER" # 03/04/2024 with no date evidence
    UNPARSEABLE = "UNPARSEABLE"                   # text where a number belongs
    INVALID_DATE = "INVALID_DATE"                 # 2024-02-30
    OUT_OF_RANGE = "OUT_OF_RANGE"                 # year 0007
    MISSING_COLUMN = "MISSING_COLUMN"             # the source has no such column


#: Reasons that mean "a human should look at this" (drives R-15).
#: EMPTY and NOT_APPLICABLE are unambiguous statements of *no data* rather
#: than parse failures, so they do not raise a finding on their own.
REVIEW_REASONS: frozenset[NullReason] = frozenset(
    {
        NullReason.DASH_PLACEHOLDER,
        NullReason.AMBIGUOUS_SEPARATOR,
        NullReason.AMBIGUOUS_DATE_ORDER,
        NullReason.UNPARSEABLE,
        NullReason.INVALID_DATE,
        NullReason.OUT_OF_RANGE,
    }
)


# --------------------------------------------------------------------------
# Money
# --------------------------------------------------------------------------


def _reject_float(value: Any) -> Any:
    """Refuse floats for money fields.

    ``Decimal(0.1)`` is not ``Decimal("0.1")``; letting a float in here is how
    a reconciliation engine starts reporting one-cent errors that do not exist.
    """
    if isinstance(value, float):
        raise ValueError(
            "money fields must be Decimal, int or str — never float "
            f"(got {value!r}); use Decimal(str(value))"
        )
    return value


Money = Annotated[Decimal, BeforeValidator(_reject_float)]


# --------------------------------------------------------------------------
# Claim
# --------------------------------------------------------------------------


class Claim(BaseModel):
    """One row of a loss run, normalised.

    ``field_issues`` and ``raw_cells`` together are the audit trail: for any
    field an underwriter can see the text that was on the page and, when the
    value is null, why.
    """

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    claim_number: str
    date_of_loss: date | None = None
    date_reported: date | None = None
    claim_status: ClaimStatus = ClaimStatus.UNKNOWN
    claimant_name: str | None = None
    loss_description: str | None = None
    cause_of_loss: str | None = None

    paid_indemnity: Money | None = None
    paid_medical: Money | None = None
    paid_expense: Money | None = None
    paid_total: Money | None = None
    reserve_indemnity: Money | None = None
    reserve_medical: Money | None = None
    reserve_expense: Money | None = None
    reserve_total: Money | None = None
    recovery_total: Money | None = None
    incurred_total: Money | None = None

    litigation_flag: bool | None = None

    # Provenance — spec section 2, principle 2.
    source_page: int = 1
    source_method: SourceMethod = SourceMethod.DIGITAL
    source_row: int | None = None
    source_bbox: tuple[float, float, float, float] | None = None
    field_confidence: dict[str, float] = Field(default_factory=dict)
    field_issues: dict[str, NullReason] = Field(default_factory=dict)
    raw_cells: dict[str, str] = Field(default_factory=dict)
    currency: str | None = None

    @field_validator("claim_number")
    @classmethod
    def _claim_number_not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("claim_number may not be blank")
        return text

    @field_validator("field_confidence")
    @classmethod
    def _confidence_in_unit_interval(cls, value: dict[str, float]) -> dict[str, float]:
        for field, score in value.items():
            if not 0.0 <= float(score) <= 1.0:
                raise ValueError(
                    f"field_confidence[{field!r}] must be between 0 and 1, got {score!r}"
                )
        return value

    def issue(self, field: str) -> NullReason | None:
        """The reason ``field`` is null, if it is null for a recorded reason."""
        return self.field_issues.get(field)

    def confidence(self, field: str, default: float = 1.0) -> float:
        return float(self.field_confidence.get(field, default))

    def needs_review(self) -> bool:
        return any(reason in REVIEW_REASONS for reason in self.field_issues.values())


# --------------------------------------------------------------------------
# Document
# --------------------------------------------------------------------------


class LossRunDocument(BaseModel):
    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    document_id: str = Field(default_factory=lambda: str(uuid4()))
    source_filename: str
    file_sha256: str

    carrier: str | None = None
    named_insured: str | None = None
    policy_number: str | None = None
    policy_period_start: date | None = None
    policy_period_end: date | None = None
    line_of_business: LineOfBusiness | None = None
    valuation_date: date | None = None

    currency: str = "USD"
    locale_hint: str = "us"
    page_count: int = 0
    extraction_method: ExtractionMethod = ExtractionMethod.DIGITAL

    printed_totals: dict[str, Money | None] = Field(default_factory=dict)
    printed_claim_count: int | None = None

    claims: list[Claim] = Field(default_factory=list)

    # Parsing context, carried so the UI and the rules can explain themselves.
    locale_confident: bool = True
    date_order: str | None = None
    date_order_confident: bool = True
    currencies_seen: list[str] = Field(default_factory=list)
    document_issues: dict[str, NullReason] = Field(default_factory=dict)
    profile_fingerprint: str | None = None
    profile_name: str | None = None
    scanned_pages: list[int] = Field(default_factory=list)
    extracted_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @field_validator("currency")
    @classmethod
    def _iso_4217(cls, value: str) -> str:
        code = value.strip().upper()
        if len(code) != 3 or not code.isalpha():
            raise ValueError(f"currency must be a 3-letter ISO 4217 code, got {value!r}")
        return code

    @field_validator("locale_hint")
    @classmethod
    def _known_locale(cls, value: str) -> str:
        locale = value.strip().lower()
        if locale not in {"us", "eu"}:
            raise ValueError(f"locale_hint must be 'us' or 'eu', got {value!r}")
        return locale

    @field_validator("page_count")
    @classmethod
    def _non_negative_pages(cls, value: int) -> int:
        if value < 0:
            raise ValueError("page_count may not be negative")
        return value

    def column_total(self, field: str) -> Decimal:
        """Sum of a money column across claims, ignoring nulls.

        Nulls are skipped rather than treated as zero; R-04 reports the sum
        alongside the printed total so a skipped null shows up as a mismatch
        instead of quietly changing the answer.
        """
        total = Decimal("0")
        for claim in self.claims:
            value = getattr(claim, field, None)
            if value is not None:
                total += value
        return total

    def claims_by_number(self) -> dict[str, list[Claim]]:
        grouped: dict[str, list[Claim]] = {}
        for claim in self.claims:
            grouped.setdefault(claim.claim_number, []).append(claim)
        return grouped


# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------


class Finding(BaseModel):
    """One reconciliation result (spec section 6)."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    severity: Severity
    message: str
    claim_number: str | None = None
    field: str | None = None
    expected: Decimal | int | str | None = None
    actual: Decimal | int | str | None = None
    delta: Decimal | None = None
    page: int | None = None

    def __str__(self) -> str:  # pragma: no cover - convenience only
        where = f" [{self.claim_number}]" if self.claim_number else ""
        return f"{self.rule_id} {self.severity.value}{where}: {self.message}"


class ReconciliationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: DocumentStatus
    findings: list[Finding] = Field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.WARN]

    @property
    def infos(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.INFO]

    def by_claim(self, claim_number: str) -> list[Finding]:
        return [f for f in self.findings if f.claim_number == claim_number]

    def rule_ids(self) -> list[str]:
        return [f.rule_id for f in self.findings]


# --------------------------------------------------------------------------
# Field groups — used by mapping, reconciliation, and export
# --------------------------------------------------------------------------

PAID_COMPONENT_FIELDS: tuple[str, ...] = (
    "paid_indemnity",
    "paid_medical",
    "paid_expense",
)

RESERVE_COMPONENT_FIELDS: tuple[str, ...] = (
    "reserve_indemnity",
    "reserve_medical",
    "reserve_expense",
)

MONEY_FIELDS: tuple[str, ...] = (
    *PAID_COMPONENT_FIELDS,
    "paid_total",
    *RESERVE_COMPONENT_FIELDS,
    "reserve_total",
    "recovery_total",
    "incurred_total",
)

DATE_FIELDS: tuple[str, ...] = ("date_of_loss", "date_reported")

TEXT_FIELDS: tuple[str, ...] = (
    "claim_number",
    "claimant_name",
    "loss_description",
    "cause_of_loss",
)

#: Everything a source column may be mapped onto.
CANONICAL_FIELDS: tuple[str, ...] = (
    "claim_number",
    "date_of_loss",
    "date_reported",
    "claim_status",
    "claimant_name",
    "loss_description",
    "cause_of_loss",
    *MONEY_FIELDS,
    "litigation_flag",
)

#: Personal data dropped by the export redaction toggle (spec section 9).
REDACTED_FIELDS: tuple[str, ...] = ("claimant_name", "loss_description")


def sum_present(values: Iterable[Decimal | None]) -> Decimal | None:
    """Sum only the values that exist; ``None`` if none of them do.

    Returning ``None`` rather than ``0`` keeps "no components" distinct from
    "components that add to zero".
    """
    present = [v for v in values if v is not None]
    if not present:
        return None
    total = Decimal("0")
    for value in present:
        total += value
    return total


# --------------------------------------------------------------------------
# Extraction artefacts — what stage 2 hands to stage 3
# --------------------------------------------------------------------------


class RawRow(BaseModel):
    """One physical line of a printed table, split into columns.

    Cells are still *text*: normalisation happens in stage 4, after the column
    mapping is known, so nothing is parsed twice or parsed the wrong way.
    """

    model_config = ConfigDict(extra="forbid")

    cells: list[str] = Field(default_factory=list)
    page: int = 1
    line_index: int = 0
    bbox: tuple[float, float, float, float] | None = None
    kind: str = "data"  # data | total | header

    def cell(self, index: int) -> str:
        return self.cells[index] if 0 <= index < len(self.cells) else ""

    def is_blank(self) -> bool:
        return not any(cell.strip() for cell in self.cells)


class RawTable(BaseModel):
    """The table found on one page, before any value is interpreted."""

    model_config = ConfigDict(extra="forbid")

    page: int
    headers: list[str] = Field(default_factory=list)
    rows: list[RawRow] = Field(default_factory=list)
    total_rows: list[RawRow] = Field(default_factory=list)
    strategy: str = "words"  # words | ruled | vision
    header_line_index: int | None = None
    column_bounds: list[tuple[float, float]] = Field(default_factory=list)

    #: Facts printed on the page itself. A fully scanned document has no text
    #: layer to read these from, so the vision pass reports them here.
    printed_claim_count: int | None = None
    valuation_date_text: str | None = None

    @property
    def column_count(self) -> int:
        return len(self.headers)


