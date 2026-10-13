"""Absolute scoring of one extraction against its verified truth.

``score_result(result, truth)`` answers, for one document: which claims were
found, which were missed, which were invented; for each found claim, which
critical fields are right, wrong, missing or filled in where the page prints
nothing; and whether LossLift called the document or a run CLEAN when it was
not. It never looks at the file: every judgement is between the extraction
and the truth a person wrote.

**Matching** is one to one, by printed identifier and source anchor (page, and
row where given), never by amounts: matching on money would pair a wrong
claim with a right one whose amounts happen to agree, and then score its
fields as correct. Two occurrences of one identifier are two claims. Where
the identifier and page cannot tell occurrences apart and no row settles it,
the group is *ambiguous*: counted and shown, never scored.

**Counting.** Claim precision is matched / (matched + invented); recall is
matched / (matched + missing). For fields, a wrong value costs both, a value
the truth has and the extraction lacks costs recall, and a value the
extraction has where the page prints nothing costs precision. A zero filled
into a blank is also counted on its own (``null_as_zero``). A rate with no
denominator is ``None``, with the counts beside it, never 100%.

**Auto-accepted** means what a user would take without review: the canonical
document status (``core.review.canonical_status``) or the claim's run status
is CLEAN, with no column mapping pending. False CLEAN is an auto-accepted
document or run that the truth says needs review, or that holds an error.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields as dataclass_fields
from decimal import Decimal
from typing import Any, Iterable

from core.review import canonical_run_status, canonical_status
from core.schema import DocumentStatus
from tools.qualification.truth import (
    CRITICAL_FIELDS,
    MONEY_FIELDS,
    ClaimTruth,
    DocumentTruth,
    Label,
    LabelState,
    PrintedTruth,
    RunTruth,
)

CLEAN = DocumentStatus.CLEAN.value


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


@dataclass
class _Counts:
    """Additive counters; subclasses add fields and rates."""

    def add(self, other: "_Counts") -> None:
        for item in dataclass_fields(self):
            setattr(self, item.name, getattr(self, item.name) + getattr(other, item.name))

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {item.name: getattr(self, item.name)
                               for item in dataclass_fields(self)}
        out.update(self.rates())
        return out

    def rates(self) -> dict[str, float | None]:  # pragma: no cover - overridden
        return {}


@dataclass
class ClaimCounts(_Counts):
    truth: int = 0          # scored truth occurrences
    extracted: int = 0      # scored extracted claims
    matched: int = 0
    missing: int = 0        # truth occurrences with no extracted claim
    invented: int = 0       # extracted claims matching no truth occurrence
    duplicates: int = 0     # invented claims repeating a matched identifier
    misplaced: int = 0      # right identifier, wrong page: missing and invented both
    wrong_run: int = 0      # matched, but read into the wrong logical run
    ambiguous_truth: int = 0      # unscored: occurrences no anchor can tell apart
    ambiguous_extracted: int = 0  # unscored: extracted claims in those groups

    def rates(self) -> dict[str, float | None]:
        return {
            "precision": _rate(self.matched, self.matched + self.invented),
            "recall": _rate(self.matched, self.matched + self.missing),
            "scored_coverage": _rate(self.truth, self.truth + self.ambiguous_truth),
        }


@dataclass
class FieldCounts(_Counts):
    correct: int = 0          # printed value read exactly
    incorrect: int = 0        # a different value: costs precision and recall
    missing: int = 0          # printed, not read: costs recall
    extra: int = 0            # read where nothing is printed: costs precision
    correct_absent: int = 0   # blank on the page, blank in the extraction
    null_as_zero: int = 0     # a zero read where the page prints nothing
    zero_as_null: int = 0     # a printed zero read as nothing
    unscored: int = 0         # truth ambiguous or unscorable

    def rates(self) -> dict[str, float | None]:
        scored = self.correct + self.incorrect + self.missing + self.extra + self.correct_absent
        return {
            "precision": _rate(self.correct, self.correct + self.incorrect + self.extra),
            "recall": _rate(self.correct, self.correct + self.incorrect + self.missing),
            "scored_coverage": _rate(scored, scored + self.unscored),
        }

    @property
    def errors(self) -> int:
        return self.incorrect + self.missing + self.extra


@dataclass
class SubsetMetrics:
    claims: ClaimCounts = field(default_factory=ClaimCounts)
    money: FieldCounts = field(default_factory=FieldCounts)
    critical: FieldCounts = field(default_factory=FieldCounts)
    other: FieldCounts = field(default_factory=FieldCounts)

    def add(self, other: "SubsetMetrics") -> None:
        for name in ("claims", "money", "critical", "other"):
            getattr(self, name).add(getattr(other, name))

    def as_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name).as_dict()
                for name in ("claims", "money", "critical", "other")}


@dataclass
class StatusCounts(_Counts):
    documents: int = 0
    documents_auto_accepted: int = 0
    documents_agree: int = 0
    runs_compared: int = 0
    runs_auto_accepted: int = 0
    runs_agree: int = 0
    false_clean_documents: int = 0
    false_clean_documents_status: int = 0   # truth says the document needs review
    false_clean_documents_errors: int = 0   # an error inside a document read as CLEAN
    false_clean_runs: int = 0
    pending_mapping: int = 0

    def rates(self) -> dict[str, float | None]:
        return {
            "document_agreement": _rate(self.documents_agree, self.documents),
            "run_agreement": _rate(self.runs_agree, self.runs_compared),
            "document_review_rate": _rate(self.documents - self.documents_auto_accepted,
                                          self.documents),
            "run_review_rate": _rate(self.runs_compared - self.runs_auto_accepted,
                                     self.runs_compared),
        }


@dataclass
class AccountingCounts(_Counts):
    truth_runs: int = 0
    extracted_runs: int = 0
    runs_matched: int = 0      # identical page sets
    truth_run_pages: int = 0
    pages_in_right_run: int = 0
    claim_pages: int = 0
    claim_pages_unread: int = 0   # failed, unresolved or skipped by the reader

    def rates(self) -> dict[str, float | None]:
        return {
            "run_recall": _rate(self.runs_matched, self.truth_runs),
            "run_precision": _rate(self.runs_matched, self.extracted_runs),
            "page_run_agreement": _rate(self.pages_in_right_run, self.truth_run_pages),
        }


@dataclass
class QualificationMetrics:
    """One document's score, or several added together."""

    documents: int = 0
    qualified: int = 0          # adjudicated truth; only these are qualification
    overall: SubsetMetrics = field(default_factory=SubsetMetrics)
    auto_accepted: SubsetMetrics = field(default_factory=SubsetMetrics)
    printed: FieldCounts = field(default_factory=FieldCounts)
    status: StatusCounts = field(default_factory=StatusCounts)
    accounting: AccountingCounts = field(default_factory=AccountingCounts)

    def add(self, other: "QualificationMetrics") -> None:
        self.documents += other.documents
        self.qualified += other.qualified
        self.overall.add(other.overall)
        self.auto_accepted.add(other.auto_accepted)
        self.printed.add(other.printed)
        self.status.add(other.status)
        self.accounting.add(other.accounting)

    @property
    def must_be_zero(self) -> dict[str, int]:
        """Counts that a qualified extraction must hold at zero."""
        return {
            "false_clean_documents": self.status.false_clean_documents,
            "false_clean_runs": self.status.false_clean_runs,
            "null_as_zero": (self.overall.critical.null_as_zero
                             + self.overall.other.null_as_zero),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "documents": self.documents,
            "qualified": self.qualified,
            "overall": self.overall.as_dict(),
            "auto_accepted": self.auto_accepted.as_dict(),
            "printed": self.printed.as_dict(),
            "status": self.status.as_dict(),
            "accounting": self.accounting.as_dict(),
            "must_be_zero": self.must_be_zero,
        }


# --------------------------------------------------------------------------
# Runs: truth runs against extracted runs, by page sets
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _ExtractedRun:
    run_id: str | None      # None: a document read as a single report
    pages: frozenset[int]
    auto_accepted: bool
    status: str
    source: Any = None      # the LogicalRun, when there is one


def _extracted_runs(result: Any) -> list[_ExtractedRun]:
    document = result.document
    mapping = bool(result.needs_mapping)
    runs = list(getattr(document, "runs", None) or [])
    if not runs:
        status = canonical_status(result.reconciliation, needs_mapping=mapping).value
        pages = frozenset(range(1, int(document.page_count or 0) + 1))
        return [_ExtractedRun(None, pages, status == CLEAN, status)]
    out = []
    for run in runs:
        status = canonical_run_status(result.reconciliation, run.run_id,
                                      needs_mapping=mapping).value
        out.append(_ExtractedRun(run.run_id, frozenset(run.pages), status == CLEAN, status,
                                 run))
    return out


def _truth_partition(truth: DocumentTruth) -> list[tuple[RunTruth | None, frozenset[int]]]:
    if truth.runs:
        return [(run, frozenset(run.pages)) for run in truth.runs]
    # No runs declared: one report over every page that is part of the loss run.
    pages = frozenset(p.page for p in truth.pages if p.role != "not_loss_run")
    return [(None, pages)]


def _match_runs(truth: DocumentTruth, extracted: list[_ExtractedRun]):
    """Truth runs paired with the extracted run that has exactly their pages.

    Pages the truth places outside every run (a cover sheet, a foreign page)
    are ignored on the extracted side, so a run that swallowed a cover sheet
    still matches; a run that split or merged reports does not.
    """
    partition = _truth_partition(truth)
    in_runs = frozenset().union(*(pages for _run, pages in partition))
    pairs: dict[int, _ExtractedRun] = {}
    used: set[int] = set()
    for index, (_run, pages) in enumerate(partition):
        for position, candidate in enumerate(extracted):
            if position not in used and candidate.pages & in_runs == pages:
                pairs[index] = candidate
                used.add(position)
                break
    return partition, pairs


# --------------------------------------------------------------------------
# Claims: one-to-one occurrence matching
# --------------------------------------------------------------------------


def _identifier(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return " ".join(value.split())


def _field_errors(occurrence: ClaimTruth, claim: Any) -> int:
    probe = SubsetMetrics()
    _score_fields(occurrence, claim, probe)
    return probe.critical.errors + probe.other.errors


def _match_claims(truth: DocumentTruth, claims: list[Any]):
    """(pairs, missing, invented, ambiguous_truth, ambiguous_extracted), by index.

    One printed occurrence read more than once is one match and the rest
    invented duplicates, whichever copy is taken; the copy scored is the one
    with the most field errors, so a duplicate can never hide a wrong read.
    """
    groups_truth: dict[tuple[str, int], list[ClaimTruth]] = {}
    ambiguous_identity: list[ClaimTruth] = []
    for occurrence in truth.claims:
        if occurrence.identity is LabelState.AMBIGUOUS:
            ambiguous_identity.append(occurrence)
            continue
        groups_truth.setdefault((occurrence.claim_number, occurrence.anchor.page),
                                []).append(occurrence)
    groups_extracted: dict[tuple[str | None, int | None], list[int]] = {}
    for index, claim in enumerate(claims):
        key = (_identifier(claim.claim_number), claim.source_page)
        groups_extracted.setdefault(key, []).append(index)

    pairs: list[tuple[ClaimTruth, int]] = []
    missing: list[ClaimTruth] = []
    ambiguous_truth: list[ClaimTruth] = []
    ambiguous_extracted: set[int] = set()
    taken: set[int] = set()
    for key, occurrences in groups_truth.items():
        found = groups_extracted.get(key, [])
        if len(occurrences) == 1 and occurrences[0].anchor.row is None:
            if found:
                worst = max(found, key=lambda i: (_field_errors(occurrences[0], claims[i]),
                                                  -i))
                pairs.append((occurrences[0], worst))
                taken.add(worst)
            else:
                missing.append(occurrences[0])
            continue
        rows = [o.anchor.row for o in occurrences]
        if all(row is not None for row in rows):
            # Rows tell the occurrences apart; each takes the claim on its row.
            by_row: dict[int, list[int]] = {}
            for index in found:
                by_row.setdefault(claims[index].source_row, []).append(index)
            for occurrence in occurrences:
                candidates = [i for i in by_row.get(occurrence.anchor.row, []) if i not in taken]
                if candidates:
                    pairs.append((occurrence, candidates[0]))
                    taken.add(candidates[0])
                else:
                    missing.append(occurrence)
            continue
        # Nothing on the page separates them: visible, unscored.
        ambiguous_truth.extend(occurrences)
        ambiguous_extracted.update(found)
        taken.update(found)

    leftover = [i for i in range(len(claims)) if i not in taken]
    # An occurrence whose identifier could not be read may be any leftover
    # claim on its page -- at most one each, never more.
    for occurrence in ambiguous_identity:
        ambiguous_truth.append(occurrence)
        same_page = [i for i in leftover if claims[i].source_page == occurrence.anchor.page
                     and i not in ambiguous_extracted]
        if same_page:
            ambiguous_extracted.add(same_page[0])
    invented = [i for i in leftover if i not in ambiguous_extracted]
    return pairs, missing, invented, ambiguous_truth, sorted(ambiguous_extracted)


# --------------------------------------------------------------------------
# Fields
# --------------------------------------------------------------------------


def _extracted_value(claim: Any, name: str) -> Any:
    value = getattr(claim, name, None)
    if name == "claim_status":
        value = getattr(value, "value", value)
        return None if value in (None, "UNKNOWN") else value
    return value


def _same(expected: Any, actual: Any) -> bool:
    if isinstance(expected, Decimal):
        try:
            return Decimal(str(actual)) == expected
        except Exception:  # noqa: BLE001 - not a number is not equal
            return False
    return expected == actual


def _is_zero(value: Any) -> bool:
    try:
        return Decimal(str(value)) == 0
    except Exception:  # noqa: BLE001
        return False


def _score_label(label: Label, actual: Any, counts: FieldCounts) -> None:
    if label.state in (LabelState.AMBIGUOUS, LabelState.UNSCORABLE):
        counts.unscored += 1
        return
    if label.state is LabelState.ABSENT:
        if actual is None:
            counts.correct_absent += 1
        else:
            counts.extra += 1
            if _is_zero(actual):
                counts.null_as_zero += 1
        return
    if actual is None:
        counts.missing += 1
        if _is_zero(label.value):
            counts.zero_as_null += 1
    elif _same(label.value, actual):
        counts.correct += 1
    else:
        counts.incorrect += 1


def _score_fields(occurrence: ClaimTruth, claim: Any, subset: SubsetMetrics) -> None:
    for name, label in occurrence.fields.items():
        actual = _extracted_value(claim, name)
        if name in CRITICAL_FIELDS:
            _score_label(label, actual, subset.critical)
            if name in MONEY_FIELDS:
                _score_label(label, actual, subset.money)
        else:
            _score_label(label, actual, subset.other)


def _score_printed(expected: PrintedTruth, actual_count: Any, actual_totals: dict,
                   counts: FieldCounts) -> None:
    _score_label(expected.claim_count, actual_count, counts)
    for name, label in expected.totals.items():
        _score_label(label, (actual_totals or {}).get(name), counts)


# --------------------------------------------------------------------------
# One document
# --------------------------------------------------------------------------


def score_result(result: Any, truth: DocumentTruth) -> QualificationMetrics:
    """Score one ``ExtractionResult`` against the truth for the same document."""
    metrics = QualificationMetrics(documents=1, qualified=1 if truth.qualifies else 0)
    document = result.document
    claims = list(document.claims)
    extracted_runs = _extracted_runs(result)
    partition, run_pairs = _match_runs(truth, extracted_runs)

    def run_holding(page: int | None) -> _ExtractedRun | None:
        return next((run for run in extracted_runs if page in run.pages), None)

    def truth_run_for(page: int) -> int | None:
        for index, (_run, pages) in enumerate(partition):
            if page in pages:
                return index
        return None

    # --- runs and pages ------------------------------------------------------
    acc = metrics.accounting
    acc.truth_runs = len(partition)
    acc.extracted_runs = len(extracted_runs)
    acc.runs_matched = len(run_pairs)
    unread = set(getattr(document, "failed_pages", None) or []) \
        | set(getattr(document, "unresolved_pages", None) or []) \
        | set(getattr(document, "skipped_pages", None) or [])
    for index, (_run, pages) in enumerate(partition):
        acc.truth_run_pages += len(pages)
        if index in run_pairs:
            acc.pages_in_right_run += len(pages)
    for page in truth.pages:
        if page.role == "claims":
            acc.claim_pages += 1
            if page.page in unread:
                acc.claim_pages_unread += 1

    # --- claims --------------------------------------------------------------
    pairs, missing, invented, ambiguous_truth, ambiguous_extracted = _match_claims(
        truth, claims)
    errors_in: dict[int | None, int] = {}  # truth-run index (None: no run) -> error count

    def note_error(page: int | None) -> None:
        key = truth_run_for(page) if page is not None else None
        errors_in[key] = errors_in.get(key, 0) + 1

    def subsets(auto: bool) -> list[SubsetMetrics]:
        return [metrics.overall, metrics.auto_accepted] if auto else [metrics.overall]

    matched_numbers = {occurrence.claim_number for occurrence, _ in pairs}
    missing_numbers = {occurrence.claim_number for occurrence in missing}
    for occurrence, index in pairs:
        claim = claims[index]
        holder = run_holding(claim.source_page)
        auto = bool(holder and holder.auto_accepted)
        expected_run = truth_run_for(occurrence.anchor.page)
        right_run = expected_run is not None and run_pairs.get(expected_run) is holder
        for subset in subsets(auto):
            subset.claims.truth += 1
            subset.claims.extracted += 1
            subset.claims.matched += 1
            if not right_run:
                subset.claims.wrong_run += 1
            before = subset.critical.errors
            _score_fields(occurrence, claim, subset)
            if subset is metrics.overall and (subset.critical.errors > before or not right_run):
                note_error(occurrence.anchor.page)
    for occurrence in missing:
        holder = run_holding(occurrence.anchor.page)
        for subset in subsets(bool(holder and holder.auto_accepted)):
            subset.claims.truth += 1
            subset.claims.missing += 1
        note_error(occurrence.anchor.page)
    for index in invented:
        claim = claims[index]
        holder = run_holding(claim.source_page)
        number = _identifier(claim.claim_number)
        for subset in subsets(bool(holder and holder.auto_accepted)):
            subset.claims.extracted += 1
            subset.claims.invented += 1
            if number in matched_numbers:
                subset.claims.duplicates += 1
            elif number in missing_numbers:
                subset.claims.misplaced += 1
        note_error(claim.source_page)
    for occurrence in ambiguous_truth:
        holder = run_holding(occurrence.anchor.page)
        for subset in subsets(bool(holder and holder.auto_accepted)):
            subset.claims.ambiguous_truth += 1
    for index in ambiguous_extracted:
        holder = run_holding(claims[index].source_page)
        for subset in subsets(bool(holder and holder.auto_accepted)):
            subset.claims.ambiguous_extracted += 1

    # --- printed facts --------------------------------------------------------
    _score_printed(truth.printed, document.printed_claim_count, document.printed_totals,
                   metrics.printed)
    extracted_sections = {s.page: s for s in (getattr(document, "printed_sections", None) or [])}
    for section in truth.sections:
        found = extracted_sections.get(section.page)
        _score_printed(section.printed, getattr(found, "printed_claim_count", None),
                       getattr(found, "printed_totals", None) or {}, metrics.printed)
    for index, (run, _pages) in enumerate(partition):
        if run is None:
            continue
        holder = run_pairs.get(index)
        source = holder.source if holder is not None else None
        _score_printed(run.printed, getattr(source, "printed_claim_count", None),
                       getattr(source, "printed_totals", None) or {}, metrics.printed)

    # --- status and false CLEAN -----------------------------------------------
    st = metrics.status
    st.documents = 1
    document_status = canonical_status(result.reconciliation,
                                       needs_mapping=bool(result.needs_mapping)).value
    st.pending_mapping = 1 if result.needs_mapping else 0
    auto_document = document_status == CLEAN
    st.documents_auto_accepted = 1 if auto_document else 0
    st.documents_agree = 1 if document_status == truth.status else 0
    wrong_runs = len(partition) - len(run_pairs)
    has_errors = bool(sum(errors_in.values())) or wrong_runs > 0
    if auto_document and (truth.status != CLEAN or has_errors):
        st.false_clean_documents = 1
        st.false_clean_documents_status = 1 if truth.status != CLEAN else 0
        st.false_clean_documents_errors = 1 if has_errors else 0
    for index, (run, _pages) in enumerate(partition):
        holder = run_pairs.get(index)
        if holder is None:
            continue
        expected = run.status if run is not None else truth.status
        st.runs_compared += 1
        st.runs_auto_accepted += 1 if holder.auto_accepted else 0
        st.runs_agree += 1 if holder.status == expected else 0
        if holder.auto_accepted and (expected != CLEAN or errors_in.get(index, 0)):
            st.false_clean_runs += 1
    # An auto-accepted extracted run that matches no truth run is a split
    # nobody printed, accepted without review.
    matched_ids = {id(run) for run in run_pairs.values()}
    for run in extracted_runs:
        if run.auto_accepted and id(run) not in matched_ids and run.run_id is not None:
            st.false_clean_runs += 1
    return metrics


def combine(items: Iterable[QualificationMetrics]) -> QualificationMetrics:
    total = QualificationMetrics()
    for item in items:
        total.add(item)
    return total
