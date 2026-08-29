"""The reconciliation engine (spec section 6) — the moat.

Nineteen rules, each returning zero or more :class:`~core.schema.Finding`
objects.  R-04 and R-05 are the ones that sell the product: they are the only
rules that check the extraction against something the *carrier* printed rather
than something this app computed.

Rules never mutate the document.  Reconciliation is pure so the review screen
can re-run it on every cell edit.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Sequence

from core.schema import (
    MONEY_FIELDS,
    AlaeTreatment,
    DeductibleBasis,
    PAID_COMPONENT_FIELDS,
    RESERVE_COMPONENT_FIELDS,
    REVIEW_REASONS,
    ClaimStatus,
    DocumentStatus,
    Finding,
    LossRunDocument,
    NullReason,
    ReconciliationResult,
    Severity,
    sum_present,
)

DEFAULT_TOLERANCE = Decimal("0.01")

#: R-13: a value this many times the column median is almost always a
#: mis-parse (a stray thousands separator, a merged cell).
OUTLIER_MULTIPLE = Decimal("100")


@dataclass(frozen=True)
class ReconcileConfig:
    """Per-profile reconciliation settings.

    Some carriers round to whole units, so the money tolerance is configurable
    rather than hard-coded.
    """

    money_tolerance: Decimal = DEFAULT_TOLERANCE
    outlier_multiple: Decimal = OUTLIER_MULTIPLE
    review_reasons: frozenset[NullReason] = REVIEW_REASONS
    #: Rule IDs to skip entirely, for carriers whose format makes one moot.
    disabled_rules: frozenset[str] = frozenset()

    def within_tolerance(self, delta: Decimal) -> bool:
        return abs(delta) <= self.money_tolerance


RuleFn = Callable[[LossRunDocument, ReconcileConfig], list[Finding]]

_RULES: list[tuple[str, RuleFn]] = []


def rule(rule_id: str) -> Callable[[RuleFn], RuleFn]:
    """Register a rule so ``reconcile`` runs it in ID order."""

    def register(fn: RuleFn) -> RuleFn:
        _RULES.append((rule_id, fn))
        _RULES.sort(key=lambda pair: pair[0])
        return fn

    return register


def _money(value: Decimal | None) -> Decimal:
    return Decimal("0") if value is None else value


def _fmt(value: Decimal | None) -> str:
    return "null" if value is None else f"{value:,.2f}"


def _label(field_name: str) -> str:
    return field_name.replace("_", " ")


# --------------------------------------------------------------------------
# Row arithmetic
# --------------------------------------------------------------------------


@rule("R-01")
def r01_incurred_identity(
    doc: LossRunDocument, config: ReconcileConfig
) -> list[Finding]:
    """paid_total + reserve_total - recovery_total == incurred_total."""
    findings: list[Finding] = []
    for claim in doc.claims:
        if claim.incurred_total is None:
            continue
        if claim.paid_total is None and claim.reserve_total is None:
            continue
        expected = (
            _money(claim.paid_total)
            + _money(claim.reserve_total)
            - _money(claim.recovery_total)
        )
        delta = claim.incurred_total - expected
        if config.within_tolerance(delta):
            continue
        findings.append(
            Finding(
                rule_id="R-01",
                severity=Severity.ERROR,
                claim_number=claim.claim_number,
                field="incurred_total",
                message=(
                    f"Incurred does not equal paid + reserve - recovery: "
                    f"{_fmt(claim.paid_total)} + {_fmt(claim.reserve_total)} - "
                    f"{_fmt(claim.recovery_total)} = {_fmt(expected)}, "
                    f"but the document shows {_fmt(claim.incurred_total)}."
                ),
                expected=expected,
                actual=claim.incurred_total,
                delta=delta,
                page=claim.source_page,
            )
        )
    return findings


def _component_sum_rule(
    doc: LossRunDocument,
    config: ReconcileConfig,
    rule_id: str,
    components: Sequence[str],
    total_field: str,
) -> list[Finding]:
    findings: list[Finding] = []
    for claim in doc.claims:
        total = getattr(claim, total_field)
        if total is None:
            continue
        parts = [getattr(claim, name) for name in components]
        expected = sum_present(parts)
        if expected is None:
            continue  # no components on this carrier's format
        delta = total - expected
        if config.within_tolerance(delta):
            continue
        breakdown = " + ".join(
            f"{_label(name)} {_fmt(getattr(claim, name))}" for name in components
        )
        findings.append(
            Finding(
                rule_id=rule_id,
                severity=Severity.ERROR,
                claim_number=claim.claim_number,
                field=total_field,
                message=(
                    f"{_label(total_field).capitalize()} does not equal its parts: "
                    f"{breakdown} = {_fmt(expected)}, "
                    f"but the document shows {_fmt(total)}."
                ),
                expected=expected,
                actual=total,
                delta=delta,
                page=claim.source_page,
            )
        )
    return findings


@rule("R-02")
def r02_paid_components(doc: LossRunDocument, config: ReconcileConfig) -> list[Finding]:
    """paid_indemnity + paid_medical + paid_expense == paid_total."""
    return _component_sum_rule(doc, config, "R-02", PAID_COMPONENT_FIELDS, "paid_total")


@rule("R-03")
def r03_reserve_components(
    doc: LossRunDocument, config: ReconcileConfig
) -> list[Finding]:
    """reserve_indemnity + reserve_medical + reserve_expense == reserve_total."""
    return _component_sum_rule(
        doc, config, "R-03", RESERVE_COMPONENT_FIELDS, "reserve_total"
    )


# --------------------------------------------------------------------------
# The two rules that check against what the carrier printed
# --------------------------------------------------------------------------


@rule("R-04")
def r04_footer_totals(doc: LossRunDocument, config: ReconcileConfig) -> list[Finding]:
    """Column sum equals the printed footer total, per money column."""
    findings: list[Finding] = []
    for field_name, printed in sorted(doc.printed_totals.items()):
        if printed is None or field_name not in MONEY_FIELDS:
            continue
        extracted = doc.column_total(field_name)
        delta = extracted - printed
        if config.within_tolerance(delta):
            continue
        missing = sum(
            1 for claim in doc.claims if getattr(claim, field_name, None) is None
        )
        hint = (
            f" {missing} row(s) have no value in this column."
            if missing
            else ""
        )
        findings.append(
            Finding(
                rule_id="R-04",
                severity=Severity.ERROR,
                field=field_name,
                message=(
                    f"Extracted {_label(field_name)} sums to {_fmt(extracted)}, "
                    f"but the document's printed total is {_fmt(printed)} "
                    f"(off by {_fmt(delta)}).{hint}"
                ),
                expected=printed,
                actual=extracted,
                delta=delta,
            )
        )
    return findings


@rule("R-05")
def r05_claim_count(doc: LossRunDocument, config: ReconcileConfig) -> list[Finding]:
    """Extracted row count equals the printed claim count."""
    printed = doc.printed_claim_count
    if printed is None:
        return []
    extracted = len(doc.claims)
    if extracted == printed:
        return []
    direction = "more" if extracted > printed else "fewer"
    return [
        Finding(
            rule_id="R-05",
            severity=Severity.ERROR,
            field="claim_count",
            message=(
                f"Extracted {extracted} claims but the document says {printed}. "
                f"That is {abs(extracted - printed)} {direction} row(s) than expected."
            ),
            expected=printed,
            actual=extracted,
            delta=Decimal(extracted - printed),
        )
    ]


# --------------------------------------------------------------------------
# Required fields
# --------------------------------------------------------------------------


@rule("R-06")
def r06_valuation_date(doc: LossRunDocument, config: ReconcileConfig) -> list[Finding]:
    """A loss run without a valuation date is unusable."""
    if doc.valuation_date is not None:
        return []
    return [
        Finding(
            rule_id="R-06",
            severity=Severity.ERROR,
            field="valuation_date",
            message=(
                "No valuation date found. Set it on the review screen — without "
                "one the loss run cannot be used for pricing."
            ),
            expected="a date",
            actual=None,
        )
    ]


@rule("R-07")
def r07_required_claim_fields(
    doc: LossRunDocument, config: ReconcileConfig
) -> list[Finding]:
    """claim_number, date_of_loss and incurred_total must be present."""
    findings: list[Finding] = []
    for claim in doc.claims:
        for field_name in ("date_of_loss", "incurred_total"):
            if getattr(claim, field_name) is not None:
                continue
            reason = claim.issue(field_name)
            because = f" ({reason.value})" if reason else ""
            findings.append(
                Finding(
                    rule_id="R-07",
                    severity=Severity.ERROR,
                    claim_number=claim.claim_number,
                    field=field_name,
                    message=(
                        f"{_label(field_name).capitalize()} is missing{because}. "
                        f"Fill it in on the review screen."
                    ),
                    expected="a value",
                    actual=None,
                    page=claim.source_page,
                )
            )
    return findings


# --------------------------------------------------------------------------
# Warnings
# --------------------------------------------------------------------------


@rule("R-08")
def r08_closed_with_reserve(
    doc: LossRunDocument, config: ReconcileConfig
) -> list[Finding]:
    """A closed claim holding reserve is usually a stale row."""
    findings: list[Finding] = []
    for claim in doc.claims:
        if claim.claim_status is not ClaimStatus.CLOSED:
            continue
        if claim.reserve_total is None or config.within_tolerance(claim.reserve_total):
            continue
        findings.append(
            Finding(
                rule_id="R-08",
                severity=Severity.WARN,
                claim_number=claim.claim_number,
                field="reserve_total",
                message=(
                    f"Claim is closed but still carries reserve of "
                    f"{_fmt(claim.reserve_total)}."
                ),
                expected=Decimal("0"),
                actual=claim.reserve_total,
                delta=claim.reserve_total,
                page=claim.source_page,
            )
        )
    return findings


@rule("R-09")
def r09_loss_outside_policy_period(
    doc: LossRunDocument, config: ReconcileConfig
) -> list[Finding]:
    """Date of loss outside the policy period."""
    start, end = doc.policy_period_start, doc.policy_period_end
    if start is None and end is None:
        return []
    findings: list[Finding] = []
    for claim in doc.claims:
        loss = claim.date_of_loss
        if loss is None:
            continue
        if (start and loss < start) or (end and loss > end):
            window = f"{start or '?'} to {end or '?'}"
            findings.append(
                Finding(
                    rule_id="R-09",
                    severity=Severity.WARN,
                    claim_number=claim.claim_number,
                    field="date_of_loss",
                    message=(
                        f"Date of loss {loss} falls outside the policy period "
                        f"({window})."
                    ),
                    expected=window,
                    actual=str(loss),
                    page=claim.source_page,
                )
            )
    return findings


@rule("R-10")
def r10_date_ordering(doc: LossRunDocument, config: ReconcileConfig) -> list[Finding]:
    """date_of_loss <= date_reported <= valuation_date."""
    findings: list[Finding] = []
    for claim in doc.claims:
        loss, reported = claim.date_of_loss, claim.date_reported
        if loss and reported and reported < loss:
            findings.append(
                Finding(
                    rule_id="R-10",
                    severity=Severity.WARN,
                    claim_number=claim.claim_number,
                    field="date_reported",
                    message=(
                        f"Reported {reported} is before the loss date {loss}."
                    ),
                    expected=f"on or after {loss}",
                    actual=str(reported),
                    page=claim.source_page,
                )
            )
        if doc.valuation_date:
            for field_name, value in (("date_reported", reported), ("date_of_loss", loss)):
                if value and value > doc.valuation_date:
                    findings.append(
                        Finding(
                            rule_id="R-10",
                            severity=Severity.WARN,
                            claim_number=claim.claim_number,
                            field=field_name,
                            message=(
                                f"{_label(field_name).capitalize()} {value} is after "
                                f"the valuation date {doc.valuation_date}."
                            ),
                            expected=f"on or before {doc.valuation_date}",
                            actual=str(value),
                            page=claim.source_page,
                        )
                    )
                    break
    return findings


@rule("R-11")
def r11_duplicate_claim_numbers(
    doc: LossRunDocument, config: ReconcileConfig
) -> list[Finding]:
    """The same claim number twice within one carrier and policy.

    A hard fail rather than a flag: the document is scoped to one carrier and
    policy, so a repeated number is the same claim counted twice, and every
    total built from it is wrong by that claim.
    """
    findings: list[Finding] = []
    for number, claims in doc.claims_by_number().items():
        if len(claims) < 2:
            continue
        pages = sorted({claim.source_page for claim in claims})
        if len(pages) > 1:
            continue  # R-12 owns the cross-page case
        findings.append(
            Finding(
                rule_id="R-11",
                severity=Severity.ERROR,
                claim_number=number,
                field="claim_number",
                message=(
                    f"Claim number appears {len(claims)} times on page {pages[0]}. "
                    f"Delete the duplicate row or correct the number."
                ),
                expected=1,
                actual=len(claims),
                page=pages[0],
            )
        )
    return findings


@rule("R-12")
def r12_cross_page_duplicate(
    doc: LossRunDocument, config: ReconcileConfig
) -> list[Finding]:
    """The same claim on two pages — usually a repeated continuation header."""
    findings: list[Finding] = []
    for number, claims in doc.claims_by_number().items():
        pages = sorted({claim.source_page for claim in claims})
        if len(claims) < 2 or len(pages) < 2:
            continue
        findings.append(
            Finding(
                rule_id="R-12",
                severity=Severity.WARN,
                claim_number=number,
                field="claim_number",
                message=(
                    f"Claim appears on pages {', '.join(str(p) for p in pages)}. "
                    f"This is usually a row continued across a page break — keep "
                    f"one row and delete the other."
                ),
                expected=1,
                actual=len(claims),
                page=pages[0],
            )
        )
    return findings


@rule("R-13")
def r13_outliers(doc: LossRunDocument, config: ReconcileConfig) -> list[Finding]:
    """A value far above the column median: a large loss, or a mis-parse.

    Both readings are common — a book of medical-only claims with two serious
    indemnity losses trips this legitimately — so the finding asks for a look
    rather than asserting the number is wrong.
    """
    findings: list[Finding] = []
    for field_name in MONEY_FIELDS:
        values = [
            abs(getattr(claim, field_name))
            for claim in doc.claims
            if getattr(claim, field_name) is not None
        ]
        non_zero = [value for value in values if value > 0]
        if len(non_zero) < 3:
            continue  # too few rows for a median to mean anything
        median = Decimal(str(statistics.median(sorted(non_zero))))
        if median <= 0:
            continue
        ceiling = median * config.outlier_multiple
        for claim in doc.claims:
            value = getattr(claim, field_name)
            if value is None or abs(value) <= ceiling:
                continue
            findings.append(
                Finding(
                    rule_id="R-13",
                    severity=Severity.WARN,
                    claim_number=claim.claim_number,
                    field=field_name,
                    message=(
                        f"{_label(field_name).capitalize()} {_fmt(value)} is far "
                        f"above the rest of the book — over "
                        f"{config.outlier_multiple:g}x the median of "
                        f"{_fmt(median)}. Confirm it is a large loss and not a "
                        f"misread amount."
                    ),
                    expected=f"<= {_fmt(ceiling)}",
                    actual=value,
                    page=claim.source_page,
                )
            )
    return findings


@rule("R-14")
def r14_negative_paid(doc: LossRunDocument, config: ReconcileConfig) -> list[Finding]:
    """Negative paid total — legitimate after a recovery, worth noting."""
    findings: list[Finding] = []
    for claim in doc.claims:
        if claim.paid_total is None or claim.paid_total >= 0:
            continue
        findings.append(
            Finding(
                rule_id="R-14",
                severity=Severity.INFO,
                claim_number=claim.claim_number,
                field="paid_total",
                message=(
                    f"Paid total is negative ({_fmt(claim.paid_total)}). This is "
                    f"normal after a recovery."
                ),
                actual=claim.paid_total,
                page=claim.source_page,
            )
        )
    return findings


@rule("R-15")
def r15_unresolved_fields(
    doc: LossRunDocument, config: ReconcileConfig
) -> list[Finding]:
    """Anything the parser could not resolve, surfaced for a human."""
    findings: list[Finding] = []
    for field_name, reason in sorted(doc.document_issues.items()):
        if reason not in config.review_reasons:
            continue
        findings.append(
            Finding(
                rule_id="R-15",
                severity=Severity.WARN,
                field=field_name,
                message=(
                    f"{_label(field_name).capitalize()} could not be read "
                    f"({reason.value}). Enter it by hand."
                ),
                actual=reason.value,
            )
        )
    for claim in doc.claims:
        for field_name, reason in sorted(claim.field_issues.items()):
            if reason not in config.review_reasons:
                continue
            raw = claim.raw_cells.get(field_name)
            shown = f" The document shows {raw!r}." if raw else ""
            findings.append(
                Finding(
                    rule_id="R-15",
                    severity=Severity.WARN,
                    claim_number=claim.claim_number,
                    field=field_name,
                    message=(
                        f"{_label(field_name).capitalize()} could not be read "
                        f"({reason.value}).{shown} Enter it by hand."
                    ),
                    actual=reason.value,
                    page=claim.source_page,
                )
            )
    return findings


@rule("R-16")
def r16_mixed_currency(doc: LossRunDocument, config: ReconcileConfig) -> list[Finding]:
    """More than one currency symbol in a single document."""
    seen = {code for code in doc.currencies_seen if code}
    seen.update(claim.currency for claim in doc.claims if claim.currency)
    if len(seen) < 2:
        return []
    listed = ", ".join(sorted(seen))
    return [
        Finding(
            rule_id="R-16",
            severity=Severity.WARN,
            field="currency",
            message=(
                f"More than one currency appears in this document ({listed}). "
                f"Totals across mixed currencies do not mean anything — split the "
                f"document or correct the rows."
            ),
            expected=doc.currency,
            actual=listed,
        )
    ]


@rule("R-17")
def r17_no_money_either_side(
    doc: LossRunDocument, config: ReconcileConfig
) -> list[Finding]:
    """A claim with nothing paid and nothing reserved.

    Legitimate — a report-only claim or one closed without payment — but it is
    also what a row looks like when its money column failed to map, so it is
    worth a reviewer's glance rather than silent acceptance.
    """
    findings: list[Finding] = []
    for claim in doc.claims:
        if claim.paid_total is None and claim.reserve_total is None:
            continue  # no figures at all is R-07's business, not this rule
        if _money(claim.paid_total) != 0 or _money(claim.reserve_total) != 0:
            continue
        if claim.claim_status is ClaimStatus.REPORT_ONLY:
            continue  # a report-only claim is meant to carry no money
        findings.append(
            Finding(
                rule_id="R-17",
                severity=Severity.WARN,
                claim_number=claim.claim_number,
                field="paid_total",
                message=(
                    "Nothing paid and nothing reserved. Expected for a "
                    "report-only or closed-without-payment claim; otherwise "
                    "check the amounts were read from the right columns."
                ),
                expected="a paid or reserve amount",
                actual="0.00 and 0.00",
                page=claim.source_page,
            )
        )
    return findings


@rule("R-18")
def r18_unstated_basis(doc: LossRunDocument, config: ReconcileConfig) -> list[Finding]:
    """The document never says whether it is gross or net, or where ALAE sits.

    Neither is ever inferred (spec section 3). Read a net loss run as gross and
    every claim is understated by the deductible while the sheet looks fine, so
    the unknown is surfaced rather than quietly defaulted.
    """
    findings: list[Finding] = []
    unknowns = {
        "deductible_basis": (
            DeductibleBasis.UNKNOWN,
            "Whether these amounts are gross or net of the deductible is not "
            "stated on the document. Confirm with the carrier before pricing.",
        ),
        "alae_treatment": (
            AlaeTreatment.UNKNOWN,
            "Whether ALAE is included in the indemnity figures or sits beside "
            "them is not stated on the document. Confirm before comparing to "
            "another carrier's run.",
        ),
    }
    for field_name, (unknown_value, message) in unknowns.items():
        if all(getattr(claim, field_name) is unknown_value for claim in doc.claims):
            findings.append(
                Finding(
                    rule_id="R-18",
                    severity=Severity.WARN,
                    field=field_name,
                    message=message,
                    expected="stated on the document",
                    actual="unknown",
                )
            )
    return findings


@rule("R-19")
def r19_stitching_row_count(
    doc: LossRunDocument, config: ReconcileConfig
) -> list[Finding]:
    """Claims recovered after stitching pages differ from the rows seen.

    Multi-page tables are stitched into one before validation, dropping
    repeated headers and per-page subtotals. If that drops more than it should,
    the count is the first place it shows.
    """
    seen = doc.rows_seen_per_page
    if not seen:
        return []
    expected = sum(seen.values())
    actual = len(doc.claims)
    if expected == actual:
        return []
    return [
        Finding(
            rule_id="R-19",
            severity=Severity.WARN,
            message=(
                f"Pages held {expected} claim row(s) but {actual} came through "
                f"stitching. Repeated headers and per-page subtotals are meant "
                f"to be dropped; check none of the claims went with them."
            ),
            expected=expected,
            actual=actual,
            delta=Decimal(actual - expected),
        )
    ]


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------

#: Findings sort by severity first so the exceptions panel leads with errors.
_SEVERITY_ORDER = {Severity.ERROR: 0, Severity.WARN: 1, Severity.INFO: 2}


def reconcile(
    doc: LossRunDocument, config: ReconcileConfig | None = None
) -> ReconciliationResult:
    """Run every rule and return the findings plus the document badge."""
    config = config or ReconcileConfig()
    findings: list[Finding] = []
    for rule_id, fn in _RULES:
        if rule_id in config.disabled_rules:
            continue
        findings.extend(fn(doc, config))

    findings.sort(
        key=lambda f: (
            _SEVERITY_ORDER[f.severity],
            f.rule_id,
            f.claim_number or "",
            f.field or "",
        )
    )
    status = (
        DocumentStatus.NEEDS_REVIEW
        if any(f.severity is Severity.ERROR for f in findings)
        else DocumentStatus.CLEAN
    )
    return ReconciliationResult(status=status, findings=findings)


def registered_rule_ids() -> list[str]:
    return [rule_id for rule_id, _ in _RULES]
