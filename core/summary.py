"""Loss summary by policy term.

A submission asks for three to five years of claim history broken down by
policy term, not one flat list — that breakdown is what an underwriter prices
from and what goes on the application's loss history section.

Where the carrier printed a subtotal per term, each term is checked against it.
That is the same guarantee R-04 gives the document as a whole, applied one term
at a time, and it is the part a reviewer can act on: knowing which year is out
by how much is a different thing from knowing the document does not tie.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Sequence

from core.schema import Claim, ClaimStatus, LossRunDocument, PrintedSection

#: Money columns worth breaking out per term.
SUMMARY_FIELDS = ("paid_total", "reserve_total", "recovery_total", "incurred_total")


@dataclass(frozen=True)
class PeriodSummary:
    """One policy term's claims, and how they compare to what was printed."""

    label: str
    start: date | None
    end: date | None
    claims: int
    open_claims: int
    closed_claims: int
    totals: dict[str, Decimal]
    largest_loss: Decimal | None = None
    largest_claim_number: str | None = None
    printed_claims: int | None = None
    printed_totals: dict[str, Decimal] = field(default_factory=dict)

    @property
    def has_printed_check(self) -> bool:
        """Whether the carrier printed anything to check this term against."""
        return self.printed_claims is not None or bool(self.printed_totals)

    def difference(self, field_name: str) -> Decimal | None:
        """Extracted minus printed, or None when there is nothing to compare."""
        printed = self.printed_totals.get(field_name)
        if printed is None:
            return None
        return self.totals.get(field_name, Decimal("0")) - printed

    def ties(self, tolerance: Decimal = Decimal("0.01")) -> bool | None:
        """True when this term matches everything the carrier printed for it."""
        if not self.has_printed_check:
            return None
        if self.printed_claims is not None and self.printed_claims != self.claims:
            return False
        return all(
            abs(delta) <= tolerance
            for name in SUMMARY_FIELDS
            if (delta := self.difference(name)) is not None
        )


def _totals(claims: list[Claim]) -> dict[str, Decimal]:
    return {
        name: sum(
            (value for claim in claims if (value := getattr(claim, name)) is not None),
            Decimal("0"),
        )
        for name in SUMMARY_FIELDS
    }


def _largest(claims: list[Claim]) -> tuple[Decimal | None, str | None]:
    rated = [claim for claim in claims if claim.incurred_total is not None]
    if not rated:
        return None, None
    worst = max(rated, key=lambda claim: claim.incurred_total)
    return worst.incurred_total, worst.claim_number


def _section_for(
    start: date | None, sections: list[PrintedSection]
) -> PrintedSection | None:
    if start is None:
        return None
    return next((s for s in sections if s.period_start == start), None)


def _summarise(
    label: str,
    start: date | None,
    end: date | None,
    claims: list[Claim],
    section: PrintedSection | None,
) -> PeriodSummary:
    largest, largest_number = _largest(claims)
    printed = {
        name: value
        for name, value in (section.printed_totals if section else {}).items()
        if value is not None
    }
    return PeriodSummary(
        label=label,
        start=start,
        end=end,
        claims=len(claims),
        open_claims=sum(
            1
            for claim in claims
            if claim.claim_status in (ClaimStatus.OPEN, ClaimStatus.REOPENED)
        ),
        closed_claims=sum(
            1 for claim in claims if claim.claim_status is ClaimStatus.CLOSED
        ),
        totals=_totals(claims),
        largest_loss=largest,
        largest_claim_number=largest_number,
        printed_claims=section.printed_claim_count if section else None,
        printed_totals=printed,
    )


def summarise_by_period(document: LossRunDocument) -> list[PeriodSummary]:
    """Break one document's claims down by policy term."""
    return summarise_periods(
        document.claims, document.policy_periods, document.printed_sections
    )


def summarise_periods(
    claims: list[Claim],
    declared_periods: Sequence[tuple[date, date]] = (),
    printed_sections: Sequence[PrintedSection] = (),
) -> list[PeriodSummary]:
    """Break claims down by policy term, newest term last.

    Takes claims rather than a document so the same breakdown works for one
    loss run or for several merged into an account. Uses the terms declared;
    with none, falls back to the calendar year of each loss, which is how a
    reviewer would group them by hand.
    """
    if not claims:
        return []

    periods = sorted(declared_periods)
    sections = list(printed_sections)

    if periods:
        summaries: list[PeriodSummary] = []
        placed: set[int] = set()
        for start, end in periods:
            within: list[Claim] = []
            for index, claim in enumerate(claims):
                loss = claim.date_of_loss
                if loss is not None and start <= loss <= end:
                    within.append(claim)
                    placed.add(index)
            summaries.append(
                _summarise(
                    f"{start.isoformat()} to {end.isoformat()}",
                    start,
                    end,
                    within,
                    _section_for(start, sections),
                )
            )

        # A claim dated outside every declared term still belongs in the book;
        # dropping it would make the summary disagree with the claim table.
        strays = [claim for index, claim in enumerate(claims) if index not in placed]
        if strays:
            summaries.append(
                _summarise("Outside any stated term", None, None, strays, None)
            )
        return summaries

    by_year: dict[int | None, list[Claim]] = {}
    for claim in claims:
        year = claim.date_of_loss.year if claim.date_of_loss else None
        by_year.setdefault(year, []).append(claim)
    return [
        _summarise(
            str(year) if year else "No date of loss",
            date(year, 1, 1) if year else None,
            date(year, 12, 31) if year else None,
            grouped,
            None,
        )
        for year, grouped in sorted(by_year.items(), key=lambda item: (item[0] is None, item[0]))
    ]
