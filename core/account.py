"""One insured's loss history, assembled from several loss runs.

Loss runs arrive one per carrier per policy term, so the document an
underwriter prices from does not exist yet when the last PDF lands — it has to
be assembled. Doing that by hand is where the second half of the re-keying day
goes, and it is the step that turns a pile of extractions into a submission.

Two things only become visible once the runs sit together:

* **Development.** The same claim appears in successive runs at different
  valuation dates. The difference between those valuations is how the claim
  developed, which is the number that moves a loss ratio between renewals.
* **Claims that stop appearing.** A claim present in an older run of a term and
  absent from a newer run of the same term is either closed-and-purged or a
  gap in what the carrier sent. Either way it is the reviewer's call, not
  something to silently drop.

Nothing here estimates or projects. Every number is a sum or a difference of
values already extracted and reconciled per document.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Sequence

from core.schema import Claim, LossRunDocument
from core.summary import PeriodSummary, summarise_periods

UNNAMED_ACCOUNT = "Insured not named"


@dataclass(frozen=True)
class Appearance:
    """One claim as one document recorded it."""

    document_id: str
    source_filename: str
    carrier: str | None
    valuation_date: date | None
    claim: Claim


@dataclass(frozen=True)
class ClaimHistory:
    """One claim across every run that mentions it, oldest valuation first."""

    claim_number: str
    appearances: list[Appearance]

    @property
    def current(self) -> Claim:
        """The most recently valued view — the one to price from."""
        return self.appearances[-1].claim

    @property
    def valued_at(self) -> date | None:
        return self.appearances[-1].valuation_date

    @property
    def carriers(self) -> list[str]:
        seen = {a.carrier for a in self.appearances if a.carrier}
        return sorted(seen)

    @property
    def development(self) -> Decimal | None:
        """Change in incurred between the oldest and newest valuation.

        None when the claim was seen once, or when either valuation could not
        be read — an unknown movement is not a movement of zero.
        """
        if len(self.appearances) < 2:
            return None
        first = self.appearances[0].claim.incurred_total
        last = self.appearances[-1].claim.incurred_total
        if first is None or last is None:
            return None
        return last - first


@dataclass(frozen=True)
class AccountRollup:
    """Every loss run filed under one insured, merged."""

    name: str
    documents: list[LossRunDocument]
    histories: list[ClaimHistory]
    dropped: list[ClaimHistory]

    @property
    def claims(self) -> list[Claim]:
        """The current view of each claim, deduplicated across runs."""
        return [history.current for history in self.histories]

    @property
    def periods(self) -> list[PeriodSummary]:
        """The merged book by policy term.

        No printed subtotals are passed in: a carrier's subtotal covers that
        carrier's claims, not the merged set, so checking the merged numbers
        against it would compare two different things. Each document keeps its
        own per-term check on its own review screen.
        """
        declared: set[tuple[date, date]] = set()
        for document in self.documents:
            declared.update(document.policy_periods)
        return summarise_periods(self.claims, sorted(declared))

    @property
    def valuation_dates(self) -> list[date]:
        return sorted({d.valuation_date for d in self.documents if d.valuation_date})

    @property
    def developed(self) -> list[ClaimHistory]:
        """Claims seen at more than one valuation, biggest movement first."""
        moved = [h for h in self.histories if h.development not in (None, Decimal("0"))]
        return sorted(moved, key=lambda h: abs(h.development), reverse=True)


def account_name(document: LossRunDocument) -> str:
    """Which account a document belongs to.

    The insured names the account. Grouping on the carrier instead would file
    one insured's runs under three different headings, which is the pile the
    reviewer started with.
    """
    name = (document.named_insured or "").strip()
    return " ".join(name.split()) if name else UNNAMED_ACCOUNT


def group_by_account(
    documents: Sequence[LossRunDocument],
) -> dict[str, list[LossRunDocument]]:
    """File documents under their insured, preserving the order given."""
    grouped: dict[str, list[LossRunDocument]] = {}
    for document in documents:
        grouped.setdefault(account_name(document), []).append(document)
    return grouped


def _order(document: LossRunDocument) -> tuple[date, str]:
    """Documents oldest valuation first; undated ones sort earliest.

    A run with no valuation date cannot be the authority on a claim's current
    value, so it must never sort last and become the "current" appearance.
    """
    return (document.valuation_date or date.min, document.source_filename)


def _covers(document: LossRunDocument, claim: Claim) -> bool:
    """Whether this run's terms include the claim's date of loss."""
    loss = claim.date_of_loss
    if loss is None or not document.policy_periods:
        return False
    return any(start <= loss <= end for start, end in document.policy_periods)


def build_account(name: str, documents: Sequence[LossRunDocument]) -> AccountRollup:
    """Merge an insured's loss runs into one history.

    A claim number identifies the claim; the run with the latest valuation
    date wins as its current state. Earlier valuations are kept rather than
    discarded, because the difference between them is the development.
    """
    ordered = sorted(documents, key=_order)

    seen: dict[str, list[Appearance]] = {}
    last_index: dict[str, int] = {}
    for index, document in enumerate(ordered):
        for claim in document.claims:
            seen.setdefault(claim.claim_number, []).append(
                Appearance(
                    document_id=document.document_id,
                    source_filename=document.source_filename,
                    carrier=document.carrier,
                    valuation_date=document.valuation_date,
                    claim=claim,
                )
            )
            last_index[claim.claim_number] = index

    histories = [
        ClaimHistory(claim_number=number, appearances=appearances)
        for number, appearances in seen.items()
    ]
    histories.sort(
        key=lambda h: (
            h.current.date_of_loss or date.min,
            h.claim_number,
        )
    )

    # A claim that an older run lists and a newer run of the same term does not
    # has either been purged or was left out. Only worth raising when a later
    # run actually covers that claim's term — a 2019 claim missing from the
    # 2024 carrier's run is simply not that carrier's business.
    dropped = [
        history
        for history in histories
        if any(
            _covers(document, history.current)
            for document in ordered[last_index[history.claim_number] + 1 :]
        )
    ]

    return AccountRollup(
        name=name, documents=ordered, histories=histories, dropped=dropped
    )


def build_accounts(documents: Sequence[LossRunDocument]) -> list[AccountRollup]:
    """Every account represented in a set of documents, largest first."""
    return sorted(
        (
            build_account(name, grouped)
            for name, grouped in group_by_account(documents).items()
        ),
        key=lambda account: (-len(account.documents), account.name),
    )
