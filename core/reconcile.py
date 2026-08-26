"""Reconciliation rule engine (CLAUDE.md §6) — the product's moat.

Every rule takes a ``LossRunDocument`` and returns zero or more ``Finding``s.
R-04 and R-05 are the rules that sell the product: they are the only ones that
check our extraction against something the *carrier printed* rather than
something the app computed.

Rules never mutate the document and never repair data. They report.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Callable, Optional

from core.schema import (
    MONEY_FIELDS,
    ClaimRecord,
    ClaimStatus,
    LossRunDocument,
    NullReason,
    SourceMethod,
)

DEFAULT_MONEY_TOLERANCE = Decimal("0.01")

# Reasons that mean "parsing gave up and a human must look" (R-15). A field
# that is simply absent from the carrier's layout is not an exception.
_REVIEW_REASONS = {
    NullReason.AMBIGUOUS_SEPARATOR,
    NullReason.AMBIGUOUS_DATE_ORDER,
    NullReason.DOUBLE_DASH,
    NullReason.INVALID_DATE,
    NullReason.UNPARSEABLE,
}


class Severity(str, Enum):
    ERROR = "ERROR"
    WARN = "WARN"
    INFO = "INFO"


class DocumentStatus(str, Enum):
    CLEAN = "CLEAN"
    NEEDS_REVIEW = "NEEDS_REVIEW"


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: Severity
    message: str
    claim_number: Optional[str] = None
    field: Optional[str] = None
    expected: Optional[Decimal | int | str] = None
    actual: Optional[Decimal | int | str] = None
    delta: Optional[Decimal] = None
    # Row index within document.claims, so the UI can scroll to the row even
    # when claim_number itself is the thing that's missing.
    row_index: Optional[int] = None


@dataclass
class ReconcileConfig:
    money_tolerance: Decimal = DEFAULT_MONEY_TOLERANCE
    # R-13 sensitivity: flag values above this multiple of the column median.
    outlier_multiple: Decimal = Decimal("100")

    def __post_init__(self) -> None:
        self.money_tolerance = Decimal(str(self.money_tolerance))
        self.outlier_multiple = Decimal(str(self.outlier_multiple))


@dataclass
class ReconcileResult:
    findings: list[Finding] = field(default_factory=list)

    @property
    def status(self) -> DocumentStatus:
        return (
            DocumentStatus.NEEDS_REVIEW
            if any(f.severity is Severity.ERROR for f in self.findings)
            else DocumentStatus.CLEAN
        )

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.WARN]

    @property
    def infos(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.INFO]

    def for_claim(self, claim_number: Optional[str]) -> list[Finding]:
        return [f for f in self.findings if f.claim_number == claim_number]

    def rule_ids(self) -> list[str]:
        return [f.rule_id for f in self.findings]


def _label(claim: ClaimRecord, index: int) -> str:
    return claim.claim_number or f"row {index + 1}"


def _within(a: Decimal, b: Decimal, tolerance: Decimal) -> bool:
    return abs(a - b) <= tolerance


# ---------------------------------------------------------------------------
# Row arithmetic — R-01, R-02, R-03
# ---------------------------------------------------------------------------


def rule_r01(doc: LossRunDocument, cfg: ReconcileConfig) -> list[Finding]:
    """paid_total + reserve_total - recovery_total == incurred_total."""
    out: list[Finding] = []
    for i, c in enumerate(doc.claims):
        if c.paid_total is None or c.reserve_total is None or c.incurred_total is None:
            continue  # missing inputs are R-07/R-15's business, not an arithmetic failure
        recovery = c.recovery_total or Decimal("0")
        expected = c.paid_total + c.reserve_total - recovery
        if not _within(expected, c.incurred_total, cfg.money_tolerance):
            out.append(
                Finding(
                    rule_id="R-01",
                    severity=Severity.ERROR,
                    claim_number=c.claim_number,
                    row_index=i,
                    field="incurred_total",
                    message=(
                        f"Claim {_label(c, i)}: paid + reserve - recovery does not equal "
                        f"incurred total."
                    ),
                    expected=expected,
                    actual=c.incurred_total,
                    delta=c.incurred_total - expected,
                )
            )
    return out


def _component_sum_rule(
    doc: LossRunDocument,
    cfg: ReconcileConfig,
    rule_id: str,
    components: tuple[str, ...],
    total_field: str,
    noun: str,
) -> list[Finding]:
    out: list[Finding] = []
    for i, c in enumerate(doc.claims):
        total = getattr(c, total_field)
        if total is None:
            continue
        present = [getattr(c, f) for f in components if getattr(c, f) is not None]
        if not present:
            continue  # carrier doesn't break this total down
        expected = sum(present, Decimal("0"))
        if not _within(expected, total, cfg.money_tolerance):
            out.append(
                Finding(
                    rule_id=rule_id,
                    severity=Severity.ERROR,
                    claim_number=c.claim_number,
                    row_index=i,
                    field=total_field,
                    message=(
                        f"Claim {_label(c, i)}: {noun} components do not sum to "
                        f"{total_field.replace('_', ' ')}."
                    ),
                    expected=expected,
                    actual=total,
                    delta=total - expected,
                )
            )
    return out


def rule_r02(doc: LossRunDocument, cfg: ReconcileConfig) -> list[Finding]:
    return _component_sum_rule(
        doc,
        cfg,
        "R-02",
        ("paid_indemnity", "paid_medical", "paid_expense"),
        "paid_total",
        "paid",
    )


def rule_r03(doc: LossRunDocument, cfg: ReconcileConfig) -> list[Finding]:
    return _component_sum_rule(
        doc,
        cfg,
        "R-03",
        ("reserve_indemnity", "reserve_medical", "reserve_expense"),
        "reserve_total",
        "reserve",
    )


# ---------------------------------------------------------------------------
# The rules that sell the product — R-04, R-05
# ---------------------------------------------------------------------------


def rule_r04(doc: LossRunDocument, cfg: ReconcileConfig) -> list[Finding]:
    """Column sum ties to the printed footer total, per money column."""
    out: list[Finding] = []
    for money_field, printed in doc.printed_totals.items():
        if printed is None:
            continue
        values = [getattr(c, money_field) for c in doc.claims]
        if any(v is None for v in values):
            # A null in the column makes the column sum meaningless — say so
            # rather than summing what's left and reporting a false delta.
            missing = [
                _label(c, i)
                for i, c in enumerate(doc.claims)
                if getattr(c, money_field) is None
            ]
            out.append(
                Finding(
                    rule_id="R-04",
                    severity=Severity.ERROR,
                    field=money_field,
                    message=(
                        f"Cannot tie {money_field.replace('_', ' ')} to the printed total: "
                        f"{len(missing)} row(s) have no value ({', '.join(missing[:5])}"
                        f"{'…' if len(missing) > 5 else ''})."
                    ),
                    expected=printed,
                    actual=None,
                )
            )
            continue
        extracted = sum(values, Decimal("0"))
        if not _within(extracted, printed, cfg.money_tolerance):
            out.append(
                Finding(
                    rule_id="R-04",
                    severity=Severity.ERROR,
                    field=money_field,
                    message=(
                        f"Extracted {money_field.replace('_', ' ')} does not tie to the "
                        f"total printed on the document."
                    ),
                    expected=printed,
                    actual=extracted,
                    delta=extracted - printed,
                )
            )
    return out


def rule_r05(doc: LossRunDocument, cfg: ReconcileConfig) -> list[Finding]:
    """Extracted row count equals the printed claim count."""
    if doc.printed_claim_count is None:
        return []
    extracted = len(doc.claims)
    if extracted == doc.printed_claim_count:
        return []
    return [
        Finding(
            rule_id="R-05",
            severity=Severity.ERROR,
            message=(
                f"Extracted {extracted} claim(s) but the document states "
                f"{doc.printed_claim_count}."
            ),
            expected=doc.printed_claim_count,
            actual=extracted,
            delta=Decimal(extracted - doc.printed_claim_count),
        )
    ]


# ---------------------------------------------------------------------------
# Completeness — R-06, R-07
# ---------------------------------------------------------------------------


def rule_r06(doc: LossRunDocument, cfg: ReconcileConfig) -> list[Finding]:
    if doc.valuation_date is not None:
        return []
    return [
        Finding(
            rule_id="R-06",
            severity=Severity.ERROR,
            field="valuation_date",
            message=(
                "No valuation date found. A loss run without one cannot be used for "
                "underwriting — set it in the review screen."
            ),
        )
    ]


def rule_r07(doc: LossRunDocument, cfg: ReconcileConfig) -> list[Finding]:
    out: list[Finding] = []
    for i, c in enumerate(doc.claims):
        for field_name in ("claim_number", "date_of_loss", "incurred_total"):
            if getattr(c, field_name) is None:
                reason = c.field_issues.get(field_name)
                detail = f" ({reason})" if reason else ""
                out.append(
                    Finding(
                        rule_id="R-07",
                        severity=Severity.ERROR,
                        claim_number=c.claim_number,
                        row_index=i,
                        field=field_name,
                        message=(
                            f"Claim {_label(c, i)}: {field_name.replace('_', ' ')} is "
                            f"missing{detail}."
                        ),
                    )
                )
    return out


# ---------------------------------------------------------------------------
# Plausibility — R-08 … R-16
# ---------------------------------------------------------------------------


def rule_r08(doc: LossRunDocument, cfg: ReconcileConfig) -> list[Finding]:
    """Closed claim carrying a non-zero reserve."""
    out: list[Finding] = []
    for i, c in enumerate(doc.claims):
        if c.claim_status is not ClaimStatus.CLOSED or c.reserve_total is None:
            continue
        if abs(c.reserve_total) > cfg.money_tolerance:
            out.append(
                Finding(
                    rule_id="R-08",
                    severity=Severity.WARN,
                    claim_number=c.claim_number,
                    row_index=i,
                    field="reserve_total",
                    message=f"Claim {_label(c, i)} is closed but still carries a reserve.",
                    expected=Decimal("0"),
                    actual=c.reserve_total,
                    delta=c.reserve_total,
                )
            )
    return out


def rule_r09(doc: LossRunDocument, cfg: ReconcileConfig) -> list[Finding]:
    """date_of_loss outside the policy period."""
    if doc.policy_period_start is None and doc.policy_period_end is None:
        return []
    out: list[Finding] = []
    for i, c in enumerate(doc.claims):
        if c.date_of_loss is None:
            continue
        start, end = doc.policy_period_start, doc.policy_period_end
        if (start and c.date_of_loss < start) or (end and c.date_of_loss > end):
            window = f"{start or '?'} to {end or '?'}"
            out.append(
                Finding(
                    rule_id="R-09",
                    severity=Severity.WARN,
                    claim_number=c.claim_number,
                    row_index=i,
                    field="date_of_loss",
                    message=(
                        f"Claim {_label(c, i)}: date of loss falls outside the policy "
                        f"period ({window})."
                    ),
                    expected=window,
                    actual=str(c.date_of_loss),
                )
            )
    return out


def rule_r10(doc: LossRunDocument, cfg: ReconcileConfig) -> list[Finding]:
    """date_of_loss <= date_reported <= valuation_date."""
    out: list[Finding] = []
    for i, c in enumerate(doc.claims):
        if c.date_of_loss and c.date_reported and c.date_reported < c.date_of_loss:
            out.append(
                Finding(
                    rule_id="R-10",
                    severity=Severity.WARN,
                    claim_number=c.claim_number,
                    row_index=i,
                    field="date_reported",
                    message=(
                        f"Claim {_label(c, i)}: reported before the loss occurred."
                    ),
                    expected=f"on or after {c.date_of_loss}",
                    actual=str(c.date_reported),
                )
            )
        if doc.valuation_date:
            for field_name in ("date_of_loss", "date_reported"):
                value = getattr(c, field_name)
                if value and value > doc.valuation_date:
                    out.append(
                        Finding(
                            rule_id="R-10",
                            severity=Severity.WARN,
                            claim_number=c.claim_number,
                            row_index=i,
                            field=field_name,
                            message=(
                                f"Claim {_label(c, i)}: {field_name.replace('_', ' ')} is "
                                f"after the valuation date."
                            ),
                            expected=f"on or before {doc.valuation_date}",
                            actual=str(value),
                        )
                    )
    return out


def rule_r11(doc: LossRunDocument, cfg: ReconcileConfig) -> list[Finding]:
    """Duplicate claim_number within one policy period."""
    seen: dict[str, list[int]] = {}
    for i, c in enumerate(doc.claims):
        if c.claim_number:
            seen.setdefault(c.claim_number, []).append(i)
    out: list[Finding] = []
    for claim_number, rows in seen.items():
        if len(rows) < 2:
            continue
        pages = {doc.claims[i].source_page for i in rows}
        if len(pages) > 1:
            continue  # cross-page repetition is R-12's finding, not a duplicate row
        out.append(
            Finding(
                rule_id="R-11",
                severity=Severity.WARN,
                claim_number=claim_number,
                row_index=rows[1],
                field="claim_number",
                message=(
                    f"Claim {claim_number} appears {len(rows)} times on page "
                    f"{pages.pop()}."
                ),
                expected=1,
                actual=len(rows),
            )
        )
    return out


def rule_r12(doc: LossRunDocument, cfg: ReconcileConfig) -> list[Finding]:
    """Same claim on two pages — usually a continuation-header artifact."""
    pages_by_claim: dict[str, set[int]] = {}
    rows_by_claim: dict[str, list[int]] = {}
    for i, c in enumerate(doc.claims):
        if c.claim_number:
            pages_by_claim.setdefault(c.claim_number, set()).add(c.source_page)
            rows_by_claim.setdefault(c.claim_number, []).append(i)
    out: list[Finding] = []
    for claim_number, pages in pages_by_claim.items():
        if len(pages) < 2:
            continue
        listed = ", ".join(str(p) for p in sorted(pages))
        out.append(
            Finding(
                rule_id="R-12",
                severity=Severity.WARN,
                claim_number=claim_number,
                row_index=rows_by_claim[claim_number][1],
                field="claim_number",
                message=(
                    f"Claim {claim_number} appears on pages {listed}; it may have been "
                    f"counted twice across a page break."
                ),
                actual=len(pages),
            )
        )
    return out


def rule_r13(doc: LossRunDocument, cfg: ReconcileConfig) -> list[Finding]:
    """Value exceeds outlier_multiple × the column median — usually a decimal
    misread (12,345.67 read as 1234567)."""
    out: list[Finding] = []
    for money_field in MONEY_FIELDS:
        values = [
            (i, v)
            for i, c in enumerate(doc.claims)
            if (v := getattr(c, money_field)) is not None and v != 0
        ]
        if len(values) < 3:
            continue  # a median over one or two rows says nothing
        magnitudes = sorted(abs(v) for _, v in values)
        median = Decimal(str(statistics.median(magnitudes)))
        if median <= 0:
            continue
        threshold = median * cfg.outlier_multiple
        for i, v in values:
            if abs(v) > threshold:
                c = doc.claims[i]
                out.append(
                    Finding(
                        rule_id="R-13",
                        severity=Severity.WARN,
                        claim_number=c.claim_number,
                        row_index=i,
                        field=money_field,
                        message=(
                            f"Claim {_label(c, i)}: {money_field.replace('_', ' ')} is far "
                            f"above the column median — check for a misread decimal."
                        ),
                        expected=f"near {median}",
                        actual=v,
                    )
                )
    return out


def rule_r14(doc: LossRunDocument, cfg: ReconcileConfig) -> list[Finding]:
    """Negative paid_total — legitimate when a recovery exceeds payments."""
    out: list[Finding] = []
    for i, c in enumerate(doc.claims):
        if c.paid_total is not None and c.paid_total < 0:
            out.append(
                Finding(
                    rule_id="R-14",
                    severity=Severity.INFO,
                    claim_number=c.claim_number,
                    row_index=i,
                    field="paid_total",
                    message=(
                        f"Claim {_label(c, i)}: paid total is negative, which usually "
                        f"means a recovery exceeded payments."
                    ),
                    actual=c.paid_total,
                )
            )
    return out


def rule_r15(doc: LossRunDocument, cfg: ReconcileConfig) -> list[Finding]:
    """Any field that parsing gave up on."""
    out: list[Finding] = []
    for i, c in enumerate(doc.claims):
        for field_name, reason in sorted(c.field_issues.items()):
            if reason not in _REVIEW_REASONS:
                continue
            out.append(
                Finding(
                    rule_id="R-15",
                    severity=Severity.WARN,
                    claim_number=c.claim_number,
                    row_index=i,
                    field=field_name,
                    message=(
                        f"Claim {_label(c, i)}: {field_name.replace('_', ' ')} could not be "
                        f"read with confidence ({reason}). Enter it to clear this."
                    ),
                    actual=reason,
                )
            )
    return out


def rule_r16(doc: LossRunDocument, cfg: ReconcileConfig) -> list[Finding]:
    """Mixed currency symbols within one document."""
    symbols = sorted(set(doc.currency_symbols_seen))
    if len(symbols) < 2:
        return []
    return [
        Finding(
            rule_id="R-16",
            severity=Severity.WARN,
            field="currency",
            message=(
                f"The document mixes currency symbols ({', '.join(symbols)}). Totals "
                f"across different currencies do not add up."
            ),
            actual=", ".join(symbols),
        )
    ]


RULES: tuple[Callable[[LossRunDocument, ReconcileConfig], list[Finding]], ...] = (
    rule_r01,
    rule_r02,
    rule_r03,
    rule_r04,
    rule_r05,
    rule_r06,
    rule_r07,
    rule_r08,
    rule_r09,
    rule_r10,
    rule_r11,
    rule_r12,
    rule_r13,
    rule_r14,
    rule_r15,
    rule_r16,
)


def reconcile(
    doc: LossRunDocument, config: Optional[ReconcileConfig] = None
) -> ReconcileResult:
    """Run every rule. Ordered ERROR first, then WARN, then INFO, so the
    exceptions panel leads with what blocks a clean export."""
    cfg = config or ReconcileConfig()
    findings: list[Finding] = []
    for rule in RULES:
        findings.extend(rule(doc, cfg))
    order = {Severity.ERROR: 0, Severity.WARN: 1, Severity.INFO: 2}
    findings.sort(key=lambda f: (order[f.severity], f.rule_id, f.row_index or 0))
    return ReconcileResult(findings)


def vision_fields(doc: LossRunDocument) -> int:
    """Count of claims extracted by vision — the UI marks these subtly."""
    return sum(1 for c in doc.claims if c.source_method is SourceMethod.VISION)
