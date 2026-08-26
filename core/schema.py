"""Canonical data model (CLAUDE.md §3).

Every carrier format normalizes into these models. Two principles are load-bearing:

- Money is ``Decimal``, never ``float``. Floats produce reconciliation failures
  that aren't real.
- A field that failed to parse is ``None`` with a reason recorded in
  ``ClaimRecord.field_issues`` — never silently ``0``. ``0.00`` and "no data"
  are different facts.

Fields the spec marks "required" (``claim_number``, ``date_of_loss``) are still
Optional here: extraction can fail on any cell, and the failure must be
representable so reconciliation rule R-07 can flag it. Requiredness is enforced
by the rule engine, not by a crash in the pipeline.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class ExtractionMethod(str, Enum):
    DIGITAL = "digital"
    VISION = "vision"
    MIXED = "mixed"


class SourceMethod(str, Enum):
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


class NullReason:
    """Why a field is null. Stored per field in ``ClaimRecord.field_issues``."""

    BLANK = "BLANK"
    NA_TOKEN = "NA_TOKEN"
    DOUBLE_DASH = "DOUBLE_DASH"
    AMBIGUOUS_SEPARATOR = "AMBIGUOUS_SEPARATOR"
    AMBIGUOUS_DATE_ORDER = "AMBIGUOUS_DATE_ORDER"
    INVALID_DATE = "INVALID_DATE"
    UNPARSEABLE = "UNPARSEABLE"


MONEY_FIELDS: tuple[str, ...] = (
    "paid_indemnity",
    "paid_medical",
    "paid_expense",
    "paid_total",
    "reserve_indemnity",
    "reserve_medical",
    "reserve_expense",
    "reserve_total",
    "recovery_total",
    "incurred_total",
)

DATE_FIELDS: tuple[str, ...] = ("date_of_loss", "date_reported")

TEXT_FIELDS: tuple[str, ...] = (
    "claim_number",
    "claim_status",
    "claimant_name",
    "loss_description",
    "cause_of_loss",
)

# Fields dropped from export when the redaction toggle is on (§9).
PERSONAL_DATA_FIELDS: tuple[str, ...] = ("claimant_name", "loss_description")


class ClaimRecord(BaseModel):
    claim_number: Optional[str] = None
    date_of_loss: Optional[date] = None
    date_reported: Optional[date] = None
    claim_status: ClaimStatus = ClaimStatus.UNKNOWN
    claimant_name: Optional[str] = None
    loss_description: Optional[str] = None
    cause_of_loss: Optional[str] = None

    paid_indemnity: Optional[Decimal] = None
    paid_medical: Optional[Decimal] = None
    paid_expense: Optional[Decimal] = None
    paid_total: Optional[Decimal] = None
    reserve_indemnity: Optional[Decimal] = None
    reserve_medical: Optional[Decimal] = None
    reserve_expense: Optional[Decimal] = None
    reserve_total: Optional[Decimal] = None
    recovery_total: Optional[Decimal] = None
    incurred_total: Optional[Decimal] = None

    litigation_flag: Optional[bool] = None

    # Provenance — an underwriter cannot use a number without an audit trail.
    source_page: int = 1
    source_method: SourceMethod = SourceMethod.DIGITAL
    field_confidence: dict[str, float] = Field(default_factory=dict)
    # field name -> NullReason for every field that is null because parsing
    # failed (as opposed to the document simply not carrying the column).
    field_issues: dict[str, str] = Field(default_factory=dict)

    @field_validator("field_confidence")
    @classmethod
    def _confidence_in_range(cls, v: dict[str, float]) -> dict[str, float]:
        for field_name, score in v.items():
            if not 0.0 <= score <= 1.0:
                raise ValueError(
                    f"confidence for {field_name!r} is {score}; must be within 0–1"
                )
        return v

    def money_value(self, field_name: str) -> Optional[Decimal]:
        if field_name not in MONEY_FIELDS:
            raise KeyError(f"{field_name!r} is not a money field")
        return getattr(self, field_name)


class LossRunDocument(BaseModel):
    document_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_filename: str
    file_sha256: str
    carrier: Optional[str] = None
    named_insured: Optional[str] = None
    policy_number: Optional[str] = None
    policy_period_start: Optional[date] = None
    policy_period_end: Optional[date] = None
    line_of_business: Optional[LineOfBusiness] = None
    # A loss run without a valuation date is unusable — R-06 raises ERROR.
    valuation_date: Optional[date] = None
    currency: str = "USD"
    locale_hint: Literal["us", "eu"] = "us"
    page_count: int = 1
    extraction_method: ExtractionMethod = ExtractionMethod.DIGITAL
    # Totals scraped from the document footer, keyed by canonical field name.
    # These are what the carrier printed — R-04 ties our sums back to them.
    printed_totals: dict[str, Decimal] = Field(default_factory=dict)
    printed_claim_count: Optional[int] = None
    # Distinct currency symbols seen while parsing money cells (R-16).
    currency_symbols_seen: list[str] = Field(default_factory=list)

    claims: list[ClaimRecord] = Field(default_factory=list)

    @field_validator("printed_totals")
    @classmethod
    def _printed_totals_canonical(cls, v: dict[str, Decimal]) -> dict[str, Decimal]:
        unknown = set(v) - set(MONEY_FIELDS)
        if unknown:
            raise ValueError(
                f"printed_totals keys must be canonical money fields; got {sorted(unknown)}"
            )
        return v
