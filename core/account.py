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

Two rules keep the merged history from being less trustworthy than the runs
it is built from:

* **A claim is its number within one carrier and one policy.** Every source is
  a logical run -- a whole document, or one run of a packet -- with its own
  carrier, policy, term and valuation date, never another run's. Two
  appearances are one claim only when both runs name the same carrier and the
  same policy (by number, or by the term that covers the date of loss). Named
  differently, they are two claims that share a number. Where a run does not
  say, the two are kept apart and the account says it cannot tell -- a
  number shared across an unknown carrier is never merged, and never
  silently counted twice either.
* **The account is only as trusted as its least trusted run.** Each source
  carries the canonical status of the run it came from
  (``core.review``). Claims from a run that needs review stay in the history,
  marked, and the account reads NEEDS_REVIEW with the reasons -- it is never
  presented as reconciled while any part of it is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Mapping, Sequence

from core.review import canonical_run_status, canonical_status
from core.schema import Claim, DocumentStatus, LossRunDocument, ReconciliationResult
from core.summary import PeriodSummary, summarise_periods

UNNAMED_ACCOUNT = "Insured not named"


def _name(value: str | None) -> str | None:
    """A printed name or number, compared without case or spacing."""
    if not value or not value.strip():
        return None
    return " ".join(value.upper().split())


@dataclass(frozen=True)
class Source:
    """One logical run an account draws on: a document, or one run of a packet.

    Every fact is the run's own. ``status`` is the canonical status of that run
    (None when no reconciliation was supplied, which is not trusted).
    """

    document_id: str
    source_filename: str
    file_sha256: str
    run_id: str | None
    carrier: str | None
    named_insured: str | None
    policy_number: str | None
    valuation_date: date | None
    terms: tuple[tuple[date, date], ...]
    claims: tuple[Claim, ...]
    status: DocumentStatus | None

    @property
    def trusted(self) -> bool:
        return self.status is DocumentStatus.CLEAN

    def term_of(self, claim: Claim) -> tuple[date, date] | None:
        """The one policy term this run states that covers the claim's loss."""
        loss = claim.date_of_loss
        if loss is None:
            return None
        covering = [term for term in self.terms if term[0] <= loss <= term[1]]
        return covering[0] if len(covering) == 1 else None


@dataclass(frozen=True)
class Appearance:
    """One claim as one run recorded it."""

    document_id: str
    source_filename: str
    carrier: str | None
    valuation_date: date | None
    claim: Claim
    run_id: str | None = None
    policy_number: str | None = None
    term: tuple[date, date] | None = None
    trusted: bool = False


SAME, DISTINCT, UNKNOWN = "same", "distinct", "unknown"


def same_claim(one: Appearance, other: Appearance) -> str:
    """Whether two appearances of one claim number are one claim.

    ``same`` when both runs name one carrier and one policy (number, or the
    term covering the loss); ``distinct`` when they name different carriers
    or different policies; ``unknown`` when either leaves it unsaid.
    """
    if one.claim.claim_number != other.claim.claim_number:
        return DISTINCT
    carriers = (_name(one.carrier), _name(other.carrier))
    policies = (_name(one.policy_number), _name(other.policy_number))
    if None not in carriers and carriers[0] != carriers[1]:
        return DISTINCT
    if None not in policies and policies[0] != policies[1]:
        return DISTINCT
    if None not in (one.term, other.term) and one.term != other.term:
        return DISTINCT
    if None in carriers:
        return UNKNOWN
    if None not in policies or None not in (one.term, other.term):
        return SAME
    return UNKNOWN


@dataclass
class ClaimHistory:
    """One claim across every run that mentions it, oldest valuation first."""

    claim_number: str
    appearances: list[Appearance]
    #: Why this history may be the same claim as another one, or two claims
    #: counted as one -- set when a run left carrier or policy unsaid.
    uncertain: str | None = None

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
    def trusted(self) -> bool:
        """Every run it was read from is clean, and its identity is certain."""
        return self.uncertain is None and all(a.trusted for a in self.appearances)

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
    sources: list[Source] = field(default_factory=list)

    @property
    def claims(self) -> list[Claim]:
        """The current view of each claim, deduplicated across runs."""
        return [history.current for history in self.histories]

    @property
    def uncertain(self) -> list[ClaimHistory]:
        return [history for history in self.histories if history.uncertain]

    @property
    def status(self) -> DocumentStatus:
        """CLEAN only when every run is clean and every claim's identity certain."""
        return DocumentStatus.CLEAN if not self.reasons() else DocumentStatus.NEEDS_REVIEW

    def reasons(self) -> list[str]:
        """Why the merged history cannot be used without review, if it cannot."""
        reasons: list[str] = []
        for source in self.sources:
            where = source.source_filename + (f" {source.run_id}" if source.run_id else "")
            if source.status is None:
                reasons.append(f"{where}: not reconciled")
            elif source.status is not DocumentStatus.CLEAN:
                reasons.append(f"{where}: needs review")
            named = _name(source.named_insured)
            if named is not None and self.name != UNNAMED_ACCOUNT and named != _name(self.name):
                reasons.append(f"{where}: names another insured")
        numbers = sorted({history.claim_number for history in self.uncertain})
        if numbers:
            reasons.append(
                "carrier or policy not printed on every run for claim(s) "
                + ", ".join(numbers[:8])
                + ": one claim counted twice, or two claims sharing a number"
            )
        return reasons

    @property
    def periods(self) -> list[PeriodSummary]:
        """The merged book by policy term.

        No printed subtotals are passed in: a carrier's subtotal covers that
        carrier's claims, not the merged set, so checking the merged numbers
        against it would compare two different things. Each document keeps its
        own per-term check on its own review screen.
        """
        declared: set[tuple[date, date]] = set()
        for source in self.sources:
            declared.update(source.terms)
        return summarise_periods(self.claims, sorted(declared))

    @property
    def valuation_dates(self) -> list[date]:
        return sorted({s.valuation_date for s in self.sources if s.valuation_date})

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


def sources_of(
    document: LossRunDocument, reconciliation: ReconciliationResult | None = None
) -> list[Source]:
    """The logical runs a document contributes, each with its own facts."""
    if not document.is_packet:
        return [Source(
            document_id=document.document_id,
            source_filename=document.source_filename,
            file_sha256=document.file_sha256,
            run_id=None,
            carrier=document.carrier,
            named_insured=document.named_insured,
            policy_number=document.policy_number,
            valuation_date=document.valuation_date,
            terms=tuple(document.policy_periods),
            claims=tuple(document.claims),
            status=None if reconciliation is None else canonical_status(reconciliation),
        )]
    return [
        Source(
            document_id=document.document_id,
            source_filename=document.source_filename,
            file_sha256=document.file_sha256,
            run_id=run.run_id,
            carrier=run.carrier,
            named_insured=run.named_insured,
            policy_number=run.policy_number,
            valuation_date=run.valuation_date,
            terms=((run.policy_period_start, run.policy_period_end),)
            if run.policy_period_start and run.policy_period_end else (),
            claims=tuple(document.run_claims(run)),
            status=None if reconciliation is None
            else canonical_run_status(reconciliation, run.run_id),
        )
        for run in document.runs
    ]


def _order(source: Source) -> tuple[date, str, str]:
    """Runs oldest valuation first; undated ones sort earliest.

    A run with no valuation date cannot be the authority on a claim's current
    value, so it must never sort last and become the "current" appearance.
    """
    return (source.valuation_date or date.min, source.source_filename, source.run_id or "")


def _covers(source: Source, claim: Claim) -> bool:
    """Whether this run's terms include the claim's date of loss."""
    loss = claim.date_of_loss
    if loss is None or not source.terms:
        return False
    return any(start <= loss <= end for start, end in source.terms)


def _joins(history: ClaimHistory, appearance: Appearance) -> str:
    """How an appearance relates to a whole history, not just its latest entry.

    Identity is not transitive: a run that names no policy number matches both
    policy A and policy B by term, yet A and B are two claims. An appearance
    joins a history only when it is the same claim as every appearance already
    in it; distinct from every one, it is another claim; anything in between
    is undecidable and must be reviewed.
    """
    relations = {same_claim(earlier, appearance) for earlier in history.appearances}
    if relations == {SAME}:
        return SAME
    if relations == {DISTINCT}:
        return DISTINCT
    return UNKNOWN


def build_account(
    name: str,
    documents: Sequence[LossRunDocument],
    reconciliations: Mapping[str, ReconciliationResult] | None = None,
) -> AccountRollup:
    """Merge an insured's loss runs into one history.

    A claim is its number within one carrier and policy (see the module
    docstring); the run with the latest valuation date wins as its current
    state. Earlier valuations are kept rather than discarded, because the
    difference between them is the development. ``reconciliations`` maps each
    document id to its reconciliation; without one a run is not trusted.
    """
    reconciliations = reconciliations or {}
    sources = sorted(
        (source for document in documents
         for source in sources_of(document, reconciliations.get(document.document_id))),
        key=_order,
    )

    histories: list[ClaimHistory] = []
    last_index: dict[int, int] = {}
    for index, source in enumerate(sources):
        for claim in source.claims:
            appearance = Appearance(
                document_id=source.document_id,
                source_filename=source.source_filename,
                carrier=source.carrier,
                valuation_date=source.valuation_date,
                claim=claim,
                run_id=source.run_id,
                policy_number=source.policy_number,
                term=source.term_of(claim),
                trusted=source.trusted,
            )
            relations = [(h, _joins(h, appearance)) for h in histories]
            same = [h for h, relation in relations if relation == SAME]
            unknown = [h for h, relation in relations if relation == UNKNOWN]
            if len(same) == 1 and not unknown:
                history = same[0]
                history.appearances.append(appearance)
            else:
                history = ClaimHistory(claim_number=claim.claim_number,
                                       appearances=[appearance])
                histories.append(history)
                if unknown or len(same) > 1:
                    why = ("another run lists the same claim number without saying "
                           "which carrier or policy it belongs to")
                    for other in [*unknown, *same, history]:
                        other.uncertain = why
            last_index[id(history)] = index

    histories.sort(key=lambda h: (h.current.date_of_loss or date.min, h.claim_number))

    # A claim that an older run lists and a newer run of the same carrier and
    # policy does not has either been purged or was left out. Only worth
    # raising when that later run covers the claim's term -- a 2019 claim
    # missing from the 2024 run is simply not that run's business, and a
    # claim missing from another carrier's run is not missing at all.
    def _later_run_omits(history: ClaimHistory) -> bool:
        latest = history.appearances[-1]
        for source in sources[last_index[id(history)] + 1:]:
            if not _covers(source, history.current):
                continue
            probe = Appearance(source.document_id, source.source_filename, source.carrier,
                               source.valuation_date, history.current, source.run_id,
                               source.policy_number, source.term_of(history.current))
            if same_claim(latest, probe) != DISTINCT:
                return True
        return False

    dropped = [history for history in histories if _later_run_omits(history)]

    ordered_documents = sorted(
        documents, key=lambda d: (d.valuation_date or date.min, d.source_filename)
    )
    return AccountRollup(
        name=name, documents=ordered_documents, histories=histories, dropped=dropped,
        sources=sources,
    )


def build_accounts(
    documents: Sequence[LossRunDocument],
    reconciliations: Mapping[str, ReconciliationResult] | None = None,
) -> list[AccountRollup]:
    """Every account represented in a set of documents, largest first."""
    return sorted(
        (
            build_account(name, grouped, reconciliations)
            for name, grouped in group_by_account(documents).items()
        ),
        key=lambda account: (-len(account.documents), account.name),
    )
