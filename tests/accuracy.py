"""Golden-file accuracy harness (spec section 10).

Field-level accuracy = correct non-null fields / expected non-null fields,
reported separately for money and non-money fields.  Money accuracy is the
number that matters, so it is never averaged in with text.

A null that should have held a value counts as wrong.  So does a zero where a
null belongs — the "silent nulls-as-zeros" the spec forbids are counted
explicitly by :attr:`FieldReport.nulls_as_zeros`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Sequence

from core.pipeline import ExtractionResult, run_pipeline
from core.schema import Claim, LossRunDocument, MONEY_FIELDS
from tests.golden.generate import load_expected


@dataclass
class Mismatch:
    claim_number: str
    field: str
    expected: str
    actual: str
    kind: str = "wrong"  # wrong | missing | null_as_zero | extra_value

    def __str__(self) -> str:  # pragma: no cover - diagnostics
        return (
            f"{self.claim_number}.{self.field}: expected {self.expected!r}, "
            f"got {self.actual!r} ({self.kind})"
        )


@dataclass
class FieldReport:
    expected_non_null: int = 0
    correct: int = 0
    mismatches: list[Mismatch] = field(default_factory=list)
    nulls_as_zeros: int = 0

    @property
    def accuracy(self) -> float:
        if self.expected_non_null == 0:
            return 1.0
        return self.correct / self.expected_non_null

    def merge(self, other: "FieldReport") -> None:
        self.expected_non_null += other.expected_non_null
        self.correct += other.correct
        self.mismatches.extend(other.mismatches)
        self.nulls_as_zeros += other.nulls_as_zeros


@dataclass
class AccuracyReport:
    name: str
    money: FieldReport = field(default_factory=FieldReport)
    other: FieldReport = field(default_factory=FieldReport)
    expected_rows: int = 0
    extracted_rows: int = 0
    missing_claims: list[str] = field(default_factory=list)
    unexpected_claims: list[str] = field(default_factory=list)

    @property
    def rows_match(self) -> bool:
        return (
            self.expected_rows == self.extracted_rows
            and not self.missing_claims
            and not self.unexpected_claims
        )

    @property
    def all_mismatches(self) -> list[Mismatch]:
        return self.money.mismatches + self.other.mismatches

    def summary(self) -> str:
        return (
            f"{self.name}: money {self.money.accuracy:7.2%} "
            f"({self.money.correct}/{self.money.expected_non_null})  "
            f"other {self.other.accuracy:7.2%} "
            f"({self.other.correct}/{self.other.expected_non_null})  "
            f"rows {self.extracted_rows}/{self.expected_rows}"
        )


def _as_decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _actual_text(claim: Claim, field_name: str) -> str:
    value = getattr(claim, field_name, None)
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "Y" if value else "N"
    if hasattr(value, "value"):  # enum
        return str(value.value)
    return str(value)


def _values_match(field_name: str, expected: str, actual: str) -> bool:
    if field_name in MONEY_FIELDS:
        left, right = _as_decimal(expected), _as_decimal(actual)
        if left is None or right is None:
            return expected.strip() == actual.strip()
        return left == right
    return expected.strip() == actual.strip()


def compare(
    name: str,
    document: LossRunDocument,
    expected_rows: Sequence[dict[str, str]],
) -> AccuracyReport:
    """Compare an extracted document against its expected CSV, field by field."""
    report = AccuracyReport(
        name=name,
        expected_rows=len(expected_rows),
        extracted_rows=len(document.claims),
    )
    actual_by_number: dict[str, Claim] = {}
    for claim in document.claims:
        actual_by_number.setdefault(claim.claim_number, claim)

    seen: set[str] = set()
    for row in expected_rows:
        claim_number = row["claim_number"].strip()
        claim = actual_by_number.get(claim_number)
        seen.add(claim_number)
        if claim is None:
            report.missing_claims.append(claim_number)
            for field_name, expected in row.items():
                if not expected.strip():
                    continue
                bucket = report.money if field_name in MONEY_FIELDS else report.other
                bucket.expected_non_null += 1
                bucket.mismatches.append(
                    Mismatch(claim_number, field_name, expected, "", "missing")
                )
            continue

        for field_name, expected in row.items():
            actual = _actual_text(claim, field_name)
            bucket = report.money if field_name in MONEY_FIELDS else report.other

            if not expected.strip():
                # The document holds no value here. A zero would be a lie.
                if field_name in MONEY_FIELDS and actual.strip():
                    value = _as_decimal(actual)
                    kind = "null_as_zero" if value == 0 else "extra_value"
                    if kind == "null_as_zero":
                        bucket.nulls_as_zeros += 1
                    bucket.mismatches.append(
                        Mismatch(claim_number, field_name, "", actual, kind)
                    )
                continue

            bucket.expected_non_null += 1
            if _values_match(field_name, expected, actual):
                bucket.correct += 1
            else:
                bucket.mismatches.append(
                    Mismatch(claim_number, field_name, expected, actual)
                )

    report.unexpected_claims = sorted(set(actual_by_number) - seen)
    return report


def score_fixture(
    name: str, pdf_path: Path, **pipeline_kwargs: Any
) -> tuple[AccuracyReport, ExtractionResult]:
    """Run a fixture through the pipeline and score it."""
    pipeline_kwargs.setdefault("use_vision", False)
    result = run_pipeline(pdf_path, **pipeline_kwargs)
    return compare(name, result.document, load_expected(name)), result


def score_all(
    golden_dir: Path, names: Iterable[str], **pipeline_kwargs: Any
) -> dict[str, AccuracyReport]:
    reports: dict[str, AccuracyReport] = {}
    for name in names:
        report, _ = score_fixture(name, golden_dir / f"{name}.pdf", **pipeline_kwargs)
        reports[name] = report
    return reports


def aggregate(reports: Iterable[AccuracyReport]) -> AccuracyReport:
    total = AccuracyReport(name="ALL")
    for report in reports:
        total.money.merge(report.money)
        total.other.merge(report.other)
        total.expected_rows += report.expected_rows
        total.extracted_rows += report.extracted_rows
        total.missing_claims.extend(report.missing_claims)
        total.unexpected_claims.extend(report.unexpected_claims)
    return total
