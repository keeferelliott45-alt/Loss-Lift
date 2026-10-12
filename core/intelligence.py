"""Post-extraction underwriter triage over a merged account.

This module is a read-only layer that sits *after* extraction and
reconciliation. It consumes an already-built
:class:`core.account.AccountRollup` -- data that has already been read,
reconciled and trust-marked -- and answers the underwriter's first questions
about it: is this account inside appetite, what grew between valuations, what
stopped appearing, and what could not be evaluated at all.

It alters nothing upstream. It never changes a
:class:`~core.schema.DocumentStatus`, never adds or removes a claim, never
touches reconciliation output, and never injects findings into a
per-document reconciliation. Its rule ids (``T-01`` .. ``T-06``) are
triage-local and deliberately distinct from the reconciliation rule ids
(``R-01`` ...): a triage flag is an appetite observation for a person
deciding whether to write the account, not a statement that a document was
read wrongly.

Every flag carries the provenance it was derived from, or says plainly that
it could not be evaluated and why. An unconfigured threshold is reported as
not configured -- never treated as a threshold of infinity that everything
passes. Nothing here estimates, projects or rounds a number into looking
better than it is, and nothing in this module is an accuracy claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
import json

from core.schema import (
    AlaeTreatment,
    ClaimStatus,
    DeductibleBasis,
    DocumentStatus,
    LineOfBusiness,
    Severity,
)
from core.account import AccountRollup, Appearance, ClaimHistory
from core.summary import PeriodSummary

__all__ = [
    "AppetiteConfigError",
    "LobOverride",
    "AppetiteRules",
    "FlagSource",
    "TriageFlag",
    "TriageSummary",
    "load_appetite_rules",
    "evaluate_appetite",
    "triage_summary",
]

#: Triage-local rule ids. Distinct from reconciliation's R-xx on purpose:
#: these say "the account looks like this", not "the document was misread".
T01_OPEN_INCURRED = "T-01 open-incurred-ceiling"
T02_TERM_COUNT = "T-02 term-claim-count-ceiling"
T03_DEVELOPMENT = "T-03 development-spike"
T04_UNCERTAIN_IDENTITY = "T-04 uncertain-identity"
T05_DROPPED_CLAIM = "T-05 dropped-claim"
T06_UNKNOWN_STATUS = "T-06 unknown-claim-status"

_NOT_CONFIGURED_T01 = "per-claim open-incurred ceiling is not configured"
_NOT_CONFIGURED_T02 = "per-term claim-count ceiling is not configured"
_NOT_CONFIGURED_T03 = "development alert thresholds are not configured"
_NO_OPEN_STATUSES = "no statuses configured as open - open-claim checks cannot evaluate"
_LOB_NOT_ATTRIBUTED = (
    "per-LOB override(s) present but line of business is not attributed "
    "in the extracted data - overrides not applied"
)

# Stable labels summary.py gives the two undated buckets, used only to hand a
# flag its provenance back. If they ever change, attribution degrades to no
# sources rather than to invented ones.
_OUTSIDE_LABEL = "Outside any stated term"
_UNRESOLVED_LABEL = "Unresolved policy term - review required"


class AppetiteConfigError(ValueError):
    """Raised when an appetite rules file is malformed or contains unknown keys."""


@dataclass(frozen=True)
class LobOverride:
    """Per-line-of-business override of individual ceilings.

    ``None`` = inherit the account-level setting. An override refines a rule
    the account level has configured; it does not configure a rule the
    account level leaves unset -- an unconfigured account ceiling is
    reported as not configured rather than quietly rescued per LOB.
    """

    open_incurred_ceiling: Decimal | None = None
    term_claim_count_ceiling: int | None = None
    development_alert_amount: Decimal | None = None
    development_alert_pct: Decimal | None = None  # percent, e.g. Decimal("50") means 50%


@dataclass(frozen=True)
class AppetiteRules:
    """The appetite an underwriter will write against.

    Every threshold defaults to ``None``, which means NOT CONFIGURED: a rule
    without its threshold reports itself as not evaluated instead of passing
    everything or inventing a boundary.
    """

    open_incurred_ceiling: Decimal | None = None        # per-claim open-incurred ceiling; None = NOT CONFIGURED
    term_claim_count_ceiling: int | None = None         # per-term claim-count ceiling; None = NOT CONFIGURED
    development_alert_amount: Decimal | None = None     # absolute $ development alert; None = NOT CONFIGURED
    development_alert_pct: Decimal | None = None        # % development alert; None = NOT CONFIGURED
    open_statuses: frozenset[ClaimStatus] = frozenset(
        {ClaimStatus.OPEN, ClaimStatus.REOPENED}
    )
    lob_overrides: dict[str, LobOverride] = field(default_factory=dict)  # keyed by LineOfBusiness value


@dataclass(frozen=True)
class FlagSource:
    """Provenance for a flag, derived from an Appearance + its Claim.

    Never invented: every field is copied from the appearance the flag was
    computed from, so a reviewer can open the exact row.
    """

    document_id: str
    source_filename: str
    run_id: str | None
    page: int | None        # claim.source_page
    row_id: str
    claim_number: str


@dataclass(frozen=True)
class TriageFlag:
    """One triage observation, or an honest statement that there cannot be one."""

    rule: str                             # "T-01 open-incurred-ceiling", etc. (triage-local ids, distinct from reconciliation R-xx)
    severity: Severity
    claim_numbers: tuple[str, ...]
    reason: str
    sources: tuple[FlagSource, ...]
    evaluated: bool = True                # False = cannot evaluate
    not_evaluated_reason: str | None = None


@dataclass(frozen=True)
class TriageSummary:
    """The account as the underwriter sees it, plus what triage could and could not say."""

    account_name: str
    status: DocumentStatus                # account.status (canonical; CLEAN only when sources clean and identity certain)
    reasons: tuple[str, ...]              # account.reasons() verbatim
    periods: tuple[PeriodSummary, ...]    # account.periods verbatim — NEVER re-derived
    flags: tuple[TriageFlag, ...]
    period_gaps: tuple[str, ...]          # coverage-period gaps between declared terms
    not_evaluated: tuple[str, ...]        # explicit "what could not be evaluated" items


# --------------------------------------------------------------------------
# Configuration loading
# --------------------------------------------------------------------------

_TOP_LEVEL_KEYS = {
    "open_incurred_ceiling",
    "term_claim_count_ceiling",
    "development_alert_amount",
    "development_alert_pct",
    "open_statuses",
    "lob_overrides",
}

_LOB_OVERRIDE_KEYS = {
    "open_incurred_ceiling",
    "term_claim_count_ceiling",
    "development_alert_amount",
    "development_alert_pct",
}


def _number(value: object, key: str) -> Decimal:
    """One threshold as a Decimal, refusing bools, nulls and junk."""
    if isinstance(value, bool) or value is None:
        raise AppetiteConfigError(f"{key}: expected a number, got {value!r}")
    if not isinstance(value, (int, float, str)):
        raise AppetiteConfigError(f"{key}: expected a number, got {value!r}")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation:
        raise AppetiteConfigError(f"{key}: {value!r} is not a number") from None
    if not parsed.is_finite():
        raise AppetiteConfigError(f"{key}: {value!r} is not a finite number")
    if parsed < 0:
        raise AppetiteConfigError(f"{key}: negative values are not allowed (got {value!r})")
    return parsed


def _count(value: object, key: str) -> int:
    """One whole-number threshold; ``40`` and ``"40"`` are one count."""
    parsed = _number(value, key)
    if parsed != parsed.to_integral_value():
        raise AppetiteConfigError(f"{key}: expected a whole number, got {value!r}")
    return int(parsed)


def _statuses(value: object, key: str) -> frozenset[ClaimStatus]:
    if not isinstance(value, list):
        raise AppetiteConfigError(f"{key}: expected a list of claim status strings")
    parsed: list[ClaimStatus] = []
    for item in value:
        if not isinstance(item, str):
            raise AppetiteConfigError(f"{key}: expected a claim status string, got {item!r}")
        try:
            parsed.append(ClaimStatus(item))
        except ValueError:
            raise AppetiteConfigError(f"{key}: unknown claim status {item!r}") from None
    return frozenset(parsed)


def _lob_overrides(value: object, key: str) -> dict[str, LobOverride]:
    if not isinstance(value, dict):
        raise AppetiteConfigError(f"{key}: expected an object keyed by line of business")
    parsed: dict[str, LobOverride] = {}
    for lob_key, inner in value.items():
        try:
            LineOfBusiness(lob_key)
        except ValueError:
            raise AppetiteConfigError(
                f"{key}: unknown line of business {lob_key!r}"
            ) from None
        if not isinstance(inner, dict):
            raise AppetiteConfigError(f"{key}.{lob_key}: expected an object of override fields")
        unknown = set(inner) - _LOB_OVERRIDE_KEYS
        if unknown:
            raise AppetiteConfigError(
                f"{key}.{lob_key}: unknown key(s): {', '.join(sorted(unknown))}"
            )
        parsed[lob_key] = LobOverride(
            open_incurred_ceiling=(
                _number(inner["open_incurred_ceiling"], f"{key}.{lob_key}.open_incurred_ceiling")
                if "open_incurred_ceiling" in inner else None
            ),
            term_claim_count_ceiling=(
                _count(inner["term_claim_count_ceiling"], f"{key}.{lob_key}.term_claim_count_ceiling")
                if "term_claim_count_ceiling" in inner else None
            ),
            development_alert_amount=(
                _number(inner["development_alert_amount"], f"{key}.{lob_key}.development_alert_amount")
                if "development_alert_amount" in inner else None
            ),
            development_alert_pct=(
                _number(inner["development_alert_pct"], f"{key}.{lob_key}.development_alert_pct")
                if "development_alert_pct" in inner else None
            ),
        )
    return parsed


def load_appetite_rules(path: str | Path) -> AppetiteRules:
    """Read an appetite rules file.

    JSON is the supported format and needs only the standard library. A
    ``.yaml``/``.yml`` file is read with PyYAML when it happens to be
    installed, and refused with a clear message when it is not -- YAML is
    never silently half-supported. Unknown keys are refused rather than
    ignored, because a typo'd threshold would otherwise configure nothing
    while looking configured.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            raise AppetiteConfigError(
                "YAML config requires PyYAML, which is not a project dependency - use JSON"
            ) from None
        data = yaml.safe_load(text)
    else:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AppetiteConfigError(f"{path.name}: invalid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise AppetiteConfigError("appetite rules must be a JSON object")

    unknown = set(data) - _TOP_LEVEL_KEYS
    if unknown:
        raise AppetiteConfigError(f"unknown key(s): {', '.join(sorted(unknown))}")

    return AppetiteRules(
        open_incurred_ceiling=(
            _number(data["open_incurred_ceiling"], "open_incurred_ceiling")
            if "open_incurred_ceiling" in data else None
        ),
        term_claim_count_ceiling=(
            _count(data["term_claim_count_ceiling"], "term_claim_count_ceiling")
            if "term_claim_count_ceiling" in data else None
        ),
        development_alert_amount=(
            _number(data["development_alert_amount"], "development_alert_amount")
            if "development_alert_amount" in data else None
        ),
        development_alert_pct=(
            _number(data["development_alert_pct"], "development_alert_pct")
            if "development_alert_pct" in data else None
        ),
        open_statuses=(
            _statuses(data["open_statuses"], "open_statuses")
            if "open_statuses" in data else frozenset(
                {ClaimStatus.OPEN, ClaimStatus.REOPENED}
            )
        ),
        lob_overrides=(
            _lob_overrides(data["lob_overrides"], "lob_overrides")
            if "lob_overrides" in data else {}
        ),
    )


# --------------------------------------------------------------------------
# Attribution helpers
# --------------------------------------------------------------------------


def _lob_by_document(account: AccountRollup) -> dict[str, LineOfBusiness | None]:
    """Each document's stated line of business, keyed by document id."""
    return {document.document_id: document.line_of_business for document in account.documents}


def _history_lob(
    history: ClaimHistory, lob_by_document: dict[str, LineOfBusiness | None]
) -> LineOfBusiness | None:
    """The one line of business a history belongs to, or None when it cannot say.

    A history is attributable only when every run that recorded it names the
    same line of business. Mixed or unstated, it falls back to the
    account-level thresholds -- never to a guess.
    """
    lobs = {lob_by_document.get(appearance.document_id) for appearance in history.appearances}
    lobs.discard(None)
    return lobs.pop() if len(lobs) == 1 else None


def _override_for(
    rules: AppetiteRules, lob: LineOfBusiness | None
) -> LobOverride | None:
    return rules.lob_overrides.get(lob.value) if lob is not None else None


def _effective(account_value, override_value):
    """The override where it speaks, the account setting where it does not."""
    return override_value if override_value is not None else account_value


def _flag_source(appearance: Appearance) -> FlagSource:
    claim = appearance.claim
    return FlagSource(
        document_id=appearance.document_id,
        source_filename=appearance.source_filename,
        run_id=appearance.run_id,
        page=claim.source_page,
        row_id=claim.row_id,
        claim_number=claim.claim_number,
    )


def _sources_of_history(history: ClaimHistory) -> tuple[FlagSource, ...]:
    return tuple(
        sorted(
            (_flag_source(appearance) for appearance in history.appearances),
            key=lambda s: (s.document_id, s.run_id or "", s.row_id, s.claim_number),
        )
    )


def _current_source(history: ClaimHistory) -> tuple[FlagSource, ...]:
    return (_flag_source(history.appearances[-1]),)


def _sort_sources(sources: tuple[FlagSource, ...]) -> tuple[FlagSource, ...]:
    return tuple(sorted(
        sources, key=lambda s: (s.document_id, s.run_id or "", s.row_id, s.claim_number)
    ))


def _period_members(
    account: AccountRollup, periods: list[PeriodSummary]
) -> list[list[ClaimHistory]]:
    """Which histories' current claims each period bucket holds.

    Mirrors how ``summarise_periods`` places a current claim: inside exactly
    one dated term, or else in the outside / unresolved buckets it labels.
    Used only to attach provenance to a T-02 flag, never to re-count.
    """
    dated = [(i, p) for i, p in enumerate(periods) if p.start is not None and p.end is not None]
    label_index = {p.label: i for i, p in enumerate(periods)}
    members: list[list[ClaimHistory]] = [[] for _ in periods]
    for history in account.histories:
        loss = history.current.date_of_loss
        matches = [
            i for i, p in dated if loss is not None and p.start <= loss <= p.end
        ]
        if len(matches) == 1:
            members[matches[0]].append(history)
        else:
            label = _OUTSIDE_LABEL if len(matches) == 0 else _UNRESOLVED_LABEL
            index = label_index.get(label)
            if index is not None:
                members[index].append(history)
    return members


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------


def _flag_sort_key(flag: TriageFlag) -> tuple[str, str, str]:
    first_claim = flag.claim_numbers[0] if flag.claim_numbers else ""
    first_document = flag.sources[0].document_id if flag.sources else ""
    return (flag.rule, first_claim, first_document)


def _finish(flags: list[TriageFlag]) -> list[TriageFlag]:
    """Deterministic order, and one flag per (rule, claim).

    Two histories can share a claim number -- two carriers printing the same
    one. Rather than drop the second flag's provenance, duplicates are merged:
    claim numbers and sources unite, the first reason stands.
    """
    ordered = sorted(flags, key=_flag_sort_key)
    merged: dict[tuple[str, str], TriageFlag] = {}
    for flag in ordered:
        first_claim = flag.claim_numbers[0] if flag.claim_numbers else ""
        key = (flag.rule, first_claim)
        existing = merged.get(key)
        if existing is None:
            merged[key] = flag
            continue
        numbers = tuple(sorted(set(existing.claim_numbers) | set(flag.claim_numbers)))
        sources = _sort_sources(existing.sources + flag.sources)
        merged[key] = TriageFlag(
            rule=existing.rule,
            severity=existing.severity,
            claim_numbers=numbers,
            reason=existing.reason,
            sources=sources,
            evaluated=existing.evaluated,
            not_evaluated_reason=existing.not_evaluated_reason,
        )
    return list(merged.values())


def evaluate_appetite(account: AccountRollup, rules: AppetiteRules) -> list[TriageFlag]:
    """Apply the appetite rules to a built account.

    Every rule either evaluates against data the account already carries, or
    reports that it could not and why. A missing ceiling is never a pass and
    a missing amount is never a zero.
    """
    flags: list[TriageFlag] = []
    lob_by_document = _lob_by_document(account)
    histories = account.histories

    # --- T-01 / T-06: the open-claim checks -------------------------------
    # With no statuses configured as open, no claim can be classified as
    # open, so both open-based rules are disabled -- stated once, plainly,
    # rather than implied by an empty flag list.
    if not rules.open_statuses:
        flags.append(TriageFlag(
            rule=T01_OPEN_INCURRED,
            severity=Severity.INFO,
            claim_numbers=(),
            reason=_NO_OPEN_STATUSES,
            sources=(),
            evaluated=False,
            not_evaluated_reason=_NO_OPEN_STATUSES,
        ))
    else:
        open_histories = [
            history for history in histories
            if history.current.claim_status in rules.open_statuses
        ]
        override_supplies_ceiling = any(
            override.open_incurred_ceiling is not None
            for override in rules.lob_overrides.values()
        )
        if rules.open_incurred_ceiling is None and not override_supplies_ceiling:
            flags.append(TriageFlag(
                rule=T01_OPEN_INCURRED,
                severity=Severity.INFO,
                claim_numbers=(),
                reason=_NOT_CONFIGURED_T01,
                sources=(),
                evaluated=False,
                not_evaluated_reason=_NOT_CONFIGURED_T01,
            ))
        else:
            for history in open_histories:
                number = history.claim_number
                override = _override_for(rules, _history_lob(history, lob_by_document))
                ceiling = _effective(
                    rules.open_incurred_ceiling,
                    override.open_incurred_ceiling if override else None,
                )
                incurred = history.current.incurred_total
                if ceiling is None:
                    why = (
                        f"open claim {number} has no configured open-incurred "
                        "ceiling - cannot evaluate against ceiling"
                    )
                    flags.append(TriageFlag(
                        rule=T01_OPEN_INCURRED, severity=Severity.WARN,
                        claim_numbers=(number,), reason=why,
                        sources=_current_source(history),
                        evaluated=False, not_evaluated_reason=why,
                    ))
                elif incurred is None:
                    why = (
                        f"open claim {number} carries no incurred total - "
                        "cannot evaluate against ceiling"
                    )
                    flags.append(TriageFlag(
                        rule=T01_OPEN_INCURRED, severity=Severity.WARN,
                        claim_numbers=(number,), reason=why,
                        sources=_current_source(history),
                        evaluated=False, not_evaluated_reason=why,
                    ))
                elif incurred > ceiling:
                    flags.append(TriageFlag(
                        rule=T01_OPEN_INCURRED, severity=Severity.WARN,
                        claim_numbers=(number,),
                        reason=(
                            f"open claim {number} incurred {incurred} exceeds "
                            f"ceiling {ceiling}"
                        ),
                        sources=_current_source(history),
                    ))

        for history in histories:
            if history.current.claim_status is not ClaimStatus.UNKNOWN:
                continue
            number = history.claim_number
            flags.append(TriageFlag(
                rule=T06_UNKNOWN_STATUS, severity=Severity.WARN,
                claim_numbers=(number,),
                reason="claim status UNKNOWN - cannot price",
                sources=_current_source(history),
                evaluated=False,
                not_evaluated_reason="claim status UNKNOWN - cannot price",
            ))

    # --- T-02: per-term claim count ---------------------------------------
    if rules.term_claim_count_ceiling is None:
        flags.append(TriageFlag(
            rule=T02_TERM_COUNT,
            severity=Severity.INFO,
            claim_numbers=(),
            reason=_NOT_CONFIGURED_T02,
            sources=(),
            evaluated=False,
            not_evaluated_reason=_NOT_CONFIGURED_T02,
        ))
    else:
        periods = account.periods
        members = _period_members(account, periods)
        for period, held in zip(periods, members):
            override = None
            lobs = {_history_lob(history, lob_by_document) for history in held}
            lobs.discard(None)
            if len(lobs) == 1:
                override = rules.lob_overrides.get(next(iter(lobs)).value)
            ceiling = _effective(
                rules.term_claim_count_ceiling,
                override.term_claim_count_ceiling if override else None,
            )
            if period.claims <= ceiling:
                continue
            numbers = tuple(sorted({history.claim_number for history in held}))
            sources = _sort_sources(tuple(
                _flag_source(history.appearances[-1]) for history in held
            ))
            flags.append(TriageFlag(
                rule=T02_TERM_COUNT, severity=Severity.WARN,
                claim_numbers=numbers,
                reason=(
                    f"period {period.label} holds {period.claims} claims, "
                    f"above the ceiling of {ceiling}"
                ),
                sources=sources,
            ))

    # --- T-03: development between valuations ------------------------------
    override_supplies_development = any(
        override.development_alert_amount is not None
        or override.development_alert_pct is not None
        for override in rules.lob_overrides.values()
    )
    if (
        rules.development_alert_amount is None
        and rules.development_alert_pct is None
        and not override_supplies_development
    ):
        flags.append(TriageFlag(
            rule=T03_DEVELOPMENT,
            severity=Severity.INFO,
            claim_numbers=(),
            reason=_NOT_CONFIGURED_T03,
            sources=(),
            evaluated=False,
            not_evaluated_reason=_NOT_CONFIGURED_T03,
        ))
    else:
        for history in histories:
            override = _override_for(rules, _history_lob(history, lob_by_document))
            amount = _effective(
                rules.development_alert_amount,
                override.development_alert_amount if override else None,
            )
            pct = _effective(
                rules.development_alert_pct,
                override.development_alert_pct if override else None,
            )
            if amount is None and pct is None:
                continue
            number = history.claim_number
            appearances = history.appearances
            earliest = appearances[0].claim.incurred_total
            if len(appearances) >= 2 and pct is not None and earliest is None:
                why = (
                    f"cannot compute development percentage for {number} "
                    "(earliest valuation has no incurred total)"
                )
                flags.append(TriageFlag(
                    rule=T03_DEVELOPMENT, severity=Severity.WARN,
                    claim_numbers=(number,), reason=why,
                    sources=_sources_of_history(history),
                    evaluated=False, not_evaluated_reason=why,
                ))
                continue
            development = history.development
            if development is None:
                # Seen once, or a valuation could not be read: an unknown
                # movement is not a movement of zero, but there is nothing
                # here to alert on either. T-01 covers the open claim whose
                # current total is missing.
                continue
            movement = abs(development)
            percentage: Decimal | None = None
            if pct is not None:
                if earliest == 0:
                    why = (
                        f"cannot compute development percentage for {number} "
                        "(earliest valuation has a zero incurred total)"
                    )
                    flags.append(TriageFlag(
                        rule=T03_DEVELOPMENT, severity=Severity.WARN,
                        claim_numbers=(number,), reason=why,
                        sources=_sources_of_history(history),
                        evaluated=False, not_evaluated_reason=why,
                    ))
                    continue
                percentage = movement / earliest * Decimal("100")
            over_amount = amount is not None and movement > amount
            over_pct = pct is not None and percentage is not None and percentage > pct
            if not ((amount is None or over_amount) and (pct is None or over_pct)):
                continue
            direction = "grew" if development > 0 else "decreased"
            triggers = []
            if amount is not None:
                triggers.append(
                    f"movement {movement} exceeds alert amount {amount}"
                )
            if pct is not None and percentage is not None:
                triggers.append(
                    f"{percentage:.2f}% of the earliest valuation exceeds "
                    f"the {pct}% alert"
                )
            flags.append(TriageFlag(
                rule=T03_DEVELOPMENT, severity=Severity.WARN,
                claim_numbers=(number,),
                reason=(
                    f"incurred {direction} from {earliest} to "
                    f"{history.current.incurred_total} - " + "; ".join(triggers)
                ),
                sources=_sources_of_history(history),
            ))

    # --- T-04: identity the account itself could not settle ----------------
    for history in account.uncertain:
        message = history.uncertain or "claim identity could not be established"
        flags.append(TriageFlag(
            rule=T04_UNCERTAIN_IDENTITY, severity=Severity.WARN,
            claim_numbers=(history.claim_number,),
            reason=(
                f"claim {history.claim_number}: {message} - "
                "the identity cannot be priced"
            ),
            sources=_sources_of_history(history),
        ))

    # --- T-05: a claim that stopped appearing ------------------------------
    for history in account.dropped:
        flags.append(TriageFlag(
            rule=T05_DROPPED_CLAIM, severity=Severity.WARN,
            claim_numbers=(history.claim_number,),
            reason=(
                "claim present at an earlier valuation, absent from a later "
                "run covering the period - reviewer to decide (closed, "
                "renumbered, or omitted)"
            ),
            sources=_sources_of_history(history),
        ))

    return _finish(flags)


# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------


def _period_gaps(account: AccountRollup) -> tuple[str, ...]:
    """Where the declared terms leave the calendar uncovered.

    Sorted by start, compared against the latest end seen so far, so
    overlapping terms are never read as a gap -- an overlap is its own
    ambiguity, which the period summary already labels.
    """
    terms = sorted({
        term
        for source in account.sources
        for term in source.terms
        if term[0] is not None and term[1] is not None
    })
    gaps: list[str] = []
    covered_until: date | None = None
    for start, end in terms:
        if covered_until is not None and (start - covered_until).days > 1:
            gaps.append(
                f"coverage gap: {covered_until} to {start} "
                "not covered by any stated term"
            )
        if covered_until is None or end > covered_until:
            covered_until = end
    if not terms and account.claims:
        gaps.append("no policy terms declared on any source - period grouping is a fallback")
    return tuple(gaps)


def _not_evaluated(
    account: AccountRollup, rules: AppetiteRules, flags: list[TriageFlag]
) -> tuple[str, ...]:
    """Everything triage looked at and could not evaluate, assembled from data."""
    items: list[str] = []
    for flag in flags:
        if not flag.evaluated and flag.not_evaluated_reason:
            if flag.not_evaluated_reason not in items:
                items.append(flag.not_evaluated_reason)

    current = [history.current for history in account.histories]
    unknown_status = sum(
        1 for claim in current if claim.claim_status is ClaimStatus.UNKNOWN
    )
    if unknown_status:
        items.append(f"{unknown_status} claim(s) carry UNKNOWN claim status - cannot price")
    unknown_alae = sum(
        1 for claim in current if claim.alae_treatment is AlaeTreatment.UNKNOWN
    )
    if unknown_alae:
        items.append(
            f"{unknown_alae} claim(s) carry UNKNOWN ALAE treatment - "
            "ALAE treatment not evaluated"
        )
    unknown_deductible = sum(
        1 for claim in current if claim.deductible_basis is DeductibleBasis.UNKNOWN
    )
    if unknown_deductible:
        items.append(
            f"{unknown_deductible} claim(s) carry UNKNOWN deductible basis - "
            "deductible basis not evaluated"
        )

    for source in account.sources:
        where = source.source_filename + (f" {source.run_id}" if source.run_id else "")
        if source.status is None:
            items.append(f"{where}: not reconciled - source not trusted")
        elif source.status is DocumentStatus.NEEDS_REVIEW:
            items.append(f"{where}: needs review - source not trusted")

    if rules.lob_overrides and not any(
        document.line_of_business is not None for document in account.documents
    ):
        items.append(_LOB_NOT_ATTRIBUTED)
    return tuple(items)


def triage_summary(account: AccountRollup, rules: AppetiteRules) -> TriageSummary:
    """The account, its appetite flags, and what could not be evaluated.

    Status, reasons and periods are taken from the account verbatim; this
    layer re-derives nothing the account already knows.
    """
    flags = evaluate_appetite(account, rules)
    return TriageSummary(
        account_name=account.name,
        status=account.status,
        reasons=tuple(account.reasons()),
        periods=tuple(account.periods),
        flags=tuple(flags),
        period_gaps=_period_gaps(account),
        not_evaluated=_not_evaluated(account, rules, flags),
    )
