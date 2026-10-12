"""Deterministic hazard cases for the account and triage layers.

The existing families drive a PDF through the whole pipeline. These two do
not: the account rollup (``core.account``) and the triage layer
(``core.intelligence``) sit after extraction, so a hazard here is a set of
logical runs with a deliberate shape -- a claim number shared across
carriers, a run that leaves its carrier unnamed, a valuation order printed
backwards, an appetite rule left unconfigured -- fed straight to the code
under test. No PDF is rendered, so a campaign of these costs almost nothing.

Truth is written by hand into each case: which appearances of a claim number
are one claim, which run's valuation is the one to price from, what the
development was, which appetite flags must fire, and which checks cannot
evaluate. The oracle reads only the one status policy and the layer's own
surface (``AccountRollup``, ``TriageSummary``) and classifies:

* SAFE    -- the layer said exactly what the truth says.
* SILENT -- the layer presented as fine something the truth says is not:
            claims merged or split the wrong way, an uncertain identity
            presented as certain, a movement mispriced, or a check that
            cannot evaluate shown as if it had.
* LOUD   -- a false alarm: flagged or sent to review when the truth is fine.
* UNNAMED -- the layer did send the account to review, but nothing it says
             names the defect the case planted.

Zero SILENT is the pass bar, as for the PDF families.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from core.account import build_account
from core.schema import (
    AlaeTreatment,
    Claim,
    ClaimStatus,
    DeductibleBasis,
    DocumentStatus,
    LineOfBusiness,
    LossRunDocument,
    ReconciliationResult,
)
from tests.fuzz.oracle import LOUD, SAFE, SILENT, UNNAMED, Defect, Outcome

ACCOUNT_NAME = "Kestrel Freight LLC"

CARRIERS = ("Northfield Auto Insurance", "Harbor Crest Specialty", "General Casualty Company")
TERMS = (
    (date(2023, 1, 1), date(2023, 12, 31)),
    (date(2024, 1, 1), date(2024, 12, 31)),
)
VALUATIONS = (date(2023, 12, 31), date(2024, 12, 31), date(2025, 3, 31))
AMOUNTS = (Decimal("1200.00"), Decimal("4800.00"), Decimal("9750.50"),
           Decimal("26000.00"), Decimal("61000.00"))

MERGE = "merge"
SPLIT = "split"
UNCERTAIN = "uncertain"


# --------------------------------------------------------------------------
# Case description and truth
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceSpec:
    """One logical run: its own facts, or the deliberate absence of them."""

    file: str
    carrier: str | None
    policy_number: str | None
    valuation: date | None
    terms: tuple[tuple[date, date], ...] = ()
    insured: str | None = ACCOUNT_NAME
    lob: LineOfBusiness | None = None
    trusted: bool = True


@dataclass(frozen=True)
class AppearSpec:
    """One claim as one run printed it."""

    file: str
    number: str
    incurred: Decimal | None
    loss: date = date(2023, 5, 1)
    status: ClaimStatus = ClaimStatus.CLOSED
    alae: AlaeTreatment = AlaeTreatment.INCLUDED
    deductible: DeductibleBasis = DeductibleBasis.GROSS


@dataclass(frozen=True)
class ExpectedHistory:
    """What one claim number must become in the account."""

    files: tuple[str, ...]                 # every appearance's source, valuation order
    mode: str = MERGE                      # merge | split | uncertain
    current_file: str | None = None
    development: Decimal | None | str = "n/a"   # a Decimal, None, or "n/a" when unmerged


@dataclass
class AccountCase:
    """The truth about one account-shaped hazard."""

    seed: int
    scenario: str
    sources: tuple[SourceSpec, ...]
    appearances: tuple[AppearSpec, ...]
    expected: dict[str, ExpectedHistory] = field(default_factory=dict)
    dropped: frozenset[str] = frozenset()
    status: DocumentStatus = DocumentStatus.CLEAN

    def documents(self) -> list[LossRunDocument]:
        built = []
        for source in self.sources:
            claims = [
                Claim(
                    claim_number=a.number,
                    date_of_loss=a.loss,
                    claim_status=a.status,
                    incurred_total=a.incurred,
                    paid_total=a.incurred if a.incurred is not None else None,
                    reserve_total=Decimal("0.00"),
                    recovery_total=Decimal("0.00"),
                    alae_treatment=a.alae,
                    deductible_basis=a.deductible,
                )
                for a in self.appearances if a.file == source.file
            ]
            built.append(LossRunDocument(
                document_id=source.file,
                source_filename=source.file,
                file_sha256=source.file,
                named_insured=source.insured,
                carrier=source.carrier,
                policy_number=source.policy_number,
                valuation_date=source.valuation,
                policy_periods=list(source.terms),
                line_of_business=source.lob,
                claims=claims,
            ))
        return built

    def reconciliations(self) -> dict[str, ReconciliationResult]:
        return {
            source.file: ReconciliationResult(
                status=DocumentStatus.CLEAN if source.trusted else DocumentStatus.NEEDS_REVIEW
            )
            for source in self.sources
        }

    def build(self) -> "object":
        return build_account(ACCOUNT_NAME, self.documents(), self.reconciliations())


@dataclass
class TriageCase:
    """An account case plus an appetite configuration, and what must follow."""

    seed: int
    scenario: str
    account: AccountCase
    rules: "object"                        # core.intelligence.AppetiteRules
    must_flag: frozenset[tuple[str, str]] = frozenset()
    must_not_flag: frozenset[tuple[str, str]] = frozenset()
    not_evaluated_rules: frozenset[str] = frozenset()
    not_evaluated_claims: frozenset[tuple[str, str]] = frozenset()
    unknown_alae: bool = False
    unknown_deductible: bool = False


# --------------------------------------------------------------------------
# Account-family scenarios
# --------------------------------------------------------------------------


def _spec(rng, file, *, carrier="on", policy="on", valuation=0, terms=TERMS,
          lob=None, trusted=True, insured=ACCOUNT_NAME):
    return SourceSpec(
        file=file,
        carrier=CARRIERS[rng.randrange(len(CARRIERS))] if carrier == "on" else carrier,
        policy_number=f"POL-{rng.randrange(1000, 9999)}" if policy == "on" else policy,
        valuation=VALUATIONS[valuation] if valuation is not None else None,
        terms=terms,
        insured=insured,
        lob=lob,
        trusted=trusted,
    )


_UNSET = object()


def _appear(rng, file, number, *, incurred=_UNSET, status=ClaimStatus.CLOSED,
            loss=date(2023, 5, 1), alae=AlaeTreatment.INCLUDED,
            deductible=DeductibleBasis.GROSS):
    return AppearSpec(
        file=file,
        number=number,
        incurred=AMOUNTS[rng.randrange(len(AMOUNTS))] if incurred is _UNSET else incurred,
        status=status,
        loss=loss,
        alae=alae,
        deductible=deductible,
    )


def _register_merge(case, sources, appearances, number):
    ordered = sorted(
        (a for a in appearances if a.number == number),
        key=lambda a: (_source_of(sources, a.file).valuation or date.min, a.file),
    )
    files = tuple(a.file for a in ordered)
    if len(ordered) >= 2 and ordered[0].incurred is not None and ordered[-1].incurred is not None:
        development = ordered[-1].incurred - ordered[0].incurred
    elif len(ordered) >= 2:
        development = None
    else:
        development = "n/a"
    case.expected[number] = ExpectedHistory(
        files=files, mode=MERGE, current_file=ordered[-1].file, development=development
    )


def _source_of(sources, file):
    return next(source for source in sources if source.file == file)


def _case(seed, scenario, sources, appearances, expected, dropped=()):
    case = AccountCase(seed=seed, scenario=scenario, sources=tuple(sources),
                       appearances=tuple(appearances))
    case.expected = expected
    case.dropped = frozenset(dropped)
    return case


def _expected_status(case: AccountCase) -> DocumentStatus:
    """What the account's own reading must conclude from this truth."""
    uncertain = any(history.mode == UNCERTAIN for history in case.expected.values())
    untrusted = not all(source.trusted for source in case.sources)
    mismatched = any(
        source.insured not in (None, ACCOUNT_NAME) for source in case.sources
    )
    return (
        DocumentStatus.NEEDS_REVIEW
        if uncertain or untrusted or mismatched
        else DocumentStatus.CLEAN
    )


def account_case(seed: int) -> AccountCase:
    """One deterministic account-shaped hazard."""
    rng = random.Random(seed)
    case = ACCOUNT_BUILDERS[rng.choice(ACCOUNT_SCENARIOS)](rng, seed)
    case.status = _expected_status(case)
    return case


def _single_run(rng, seed):
    sources = [_spec(rng, "run-a.pdf")]
    appearances = [
        _appear(rng, "run-a.pdf", "A-1", status=ClaimStatus.OPEN),
        _appear(rng, "run-a.pdf", "A-2"),
    ]
    case = _case(seed, "single_run", sources, appearances, {})
    _register_merge(case, sources, appearances, "A-1")
    _register_merge(case, sources, appearances, "A-2")
    return case


def _two_valuations(rng, seed):
    carrier, policy = CARRIERS[0], "POL-4001"
    sources = [
        _spec(rng, "run-old.pdf", carrier=carrier, policy=policy, valuation=0),
        _spec(rng, "run-new.pdf", carrier=carrier, policy=policy, valuation=1),
    ]
    appearances = [
        _appear(rng, "run-old.pdf", "C-1", incurred=Decimal("1000.00")),
        _appear(rng, "run-new.pdf", "C-1", incurred=Decimal("4000.00")),
        _appear(rng, "run-new.pdf", "C-2"),
    ]
    case = _case(seed, "two_valuations", sources, appearances, {})
    _register_merge(case, sources, appearances, "C-1")
    _register_merge(case, sources, appearances, "C-2")
    return case


def _two_carriers(rng, seed):
    sources = [
        _spec(rng, "run-one.pdf", carrier=CARRIERS[0], policy="POL-5001"),
        _spec(rng, "run-two.pdf", carrier=CARRIERS[1], policy="POL-5002"),
    ]
    appearances = [
        _appear(rng, "run-one.pdf", "S-1"),
        _appear(rng, "run-two.pdf", "S-1"),
    ]
    case = _case(seed, "shared_number_two_carriers", sources, appearances, {})
    case.expected["S-1"] = ExpectedHistory(
        files=("run-one.pdf", "run-two.pdf"), mode=SPLIT)
    return case


def _policy_missing(rng, seed):
    carrier = CARRIERS[0]
    sources = [
        _spec(rng, "run-full.pdf", carrier=carrier, policy="POL-6001", terms=()),
        _spec(rng, "run-blank.pdf", carrier=carrier, policy=None, terms=()),
    ]
    appearances = [
        _appear(rng, "run-full.pdf", "U-1"),
        _appear(rng, "run-blank.pdf", "U-1"),
    ]
    case = _case(seed, "policy_not_stated", sources, appearances, {})
    case.expected["U-1"] = ExpectedHistory(
        files=("run-blank.pdf", "run-full.pdf"), mode=UNCERTAIN)
    return case


def _carrier_missing(rng, seed):
    sources = [
        _spec(rng, "run-named.pdf", carrier=CARRIERS[0], policy="POL-7001", valuation=0),
        # The anonymous run is the later valuation: both runs print U-2, and
        # identity is uncertain because one run leaves both facts unstated.
        _spec(rng, "run-anon.pdf", carrier=None, policy=None, terms=(), valuation=1),
    ]
    appearances = [
        _appear(rng, "run-named.pdf", "U-2"),
        _appear(rng, "run-anon.pdf", "U-2"),
    ]
    case = _case(seed, "carrier_not_stated", sources, appearances, {})
    case.expected["U-2"] = ExpectedHistory(
        files=("run-anon.pdf", "run-named.pdf"), mode=UNCERTAIN)
    return case


def _conflicting_terms(rng, seed):
    carrier = CARRIERS[0]
    sources = [
        _spec(rng, "run-2023.pdf", carrier=carrier, policy="POL-8001",
              terms=(TERMS[0],)),
        _spec(rng, "run-2024.pdf", carrier=carrier, policy="POL-8001",
              terms=(TERMS[1],)),
    ]
    appearances = [
        _appear(rng, "run-2023.pdf", "T-1", loss=date(2023, 6, 1)),
        _appear(rng, "run-2024.pdf", "T-1", loss=date(2024, 6, 1)),
    ]
    case = _case(seed, "conflicting_terms", sources, appearances, {})
    case.expected["T-1"] = ExpectedHistory(
        files=("run-2023.pdf", "run-2024.pdf"), mode=SPLIT)
    return case


def _order_inverted(rng, seed):
    carrier, policy = CARRIERS[1], "POL-9001"
    sources = [
        _spec(rng, "run-undated.pdf", carrier=carrier, policy=policy, valuation=None),
        _spec(rng, "run-latest.pdf", carrier=carrier, policy=policy, valuation=2),
    ]
    appearances = [
        _appear(rng, "run-undated.pdf", "V-1", incurred=Decimal("2000.00")),
        _appear(rng, "run-latest.pdf", "V-1", incurred=Decimal("5000.00")),
    ]
    case = _case(seed, "valuation_order_inverted", sources, appearances, {})
    _register_merge(case, sources, appearances, "V-1")
    return case


def _dropped_claim(rng, seed):
    carrier, policy = CARRIERS[2], "POL-9100"
    sources = [
        _spec(rng, "run-old.pdf", carrier=carrier, policy=policy, valuation=0),
        _spec(rng, "run-new.pdf", carrier=carrier, policy=policy, valuation=1),
    ]
    appearances = [
        _appear(rng, "run-old.pdf", "D-1", loss=date(2023, 5, 1)),
        _appear(rng, "run-old.pdf", "D-2", loss=date(2023, 6, 1)),
        _appear(rng, "run-new.pdf", "D-2", loss=date(2023, 6, 1)),
    ]
    case = _case(seed, "dropped_claim", sources, appearances, {}, dropped={"D-1"})
    _register_merge(case, sources, appearances, "D-2")
    case.expected["D-1"] = ExpectedHistory(
        files=("run-old.pdf",), mode=MERGE, current_file="run-old.pdf",
        development="n/a")
    return case


def _untrusted_source(rng, seed):
    sources = [
        _spec(rng, "run-clean.pdf", trusted=True),
        _spec(rng, "run-flagged.pdf", trusted=False),
    ]
    appearances = [
        _appear(rng, "run-clean.pdf", "R-1"),
        _appear(rng, "run-flagged.pdf", "R-2"),
    ]
    case = _case(seed, "untrusted_source", sources, appearances, {})
    _register_merge(case, sources, appearances, "R-1")
    _register_merge(case, sources, appearances, "R-2")
    return case


def _insured_mismatch(rng, seed):
    sources = [
        _spec(rng, "run-mine.pdf"),
        _spec(rng, "run-other.pdf", insured="Pelagic Shipping Ltd"),
    ]
    appearances = [
        _appear(rng, "run-mine.pdf", "M-1"),
        _appear(rng, "run-other.pdf", "M-2"),
    ]
    case = _case(seed, "insured_mismatch", sources, appearances, {})
    _register_merge(case, sources, appearances, "M-1")
    _register_merge(case, sources, appearances, "M-2")
    return case


def _mixed_identity(rng, seed):
    """Three claim numbers in one account: one merges, two do not."""
    sources = [
        _spec(rng, "run-a.pdf", carrier=CARRIERS[0], policy="POL-9201"),
        _spec(rng, "run-b.pdf", carrier=CARRIERS[0], policy="POL-9201"),
        _spec(rng, "run-c.pdf", carrier=None, policy=None, terms=()),
    ]
    appearances = [
        _appear(rng, "run-a.pdf", "X-1", incurred=Decimal("1000.00")),
        _appear(rng, "run-b.pdf", "X-1", incurred=Decimal("3500.00")),
        _appear(rng, "run-b.pdf", "X-2"),
        _appear(rng, "run-c.pdf", "X-2"),
    ]
    case = _case(seed, "mixed_identity", sources, appearances, {})
    _register_merge(case, sources, appearances, "X-1")
    case.expected["X-2"] = ExpectedHistory(
        files=("run-b.pdf", "run-c.pdf"), mode=UNCERTAIN)
    return case


ACCOUNT_BUILDERS = {
    "single_run": _single_run,
    "two_valuations": _two_valuations,
    "shared_number_two_carriers": _two_carriers,
    "policy_not_stated": _policy_missing,
    "carrier_not_stated": _carrier_missing,
    "conflicting_terms": _conflicting_terms,
    "valuation_order_inverted": _order_inverted,
    "dropped_claim": _dropped_claim,
    "untrusted_source": _untrusted_source,
    "insured_mismatch": _insured_mismatch,
    "mixed_identity": _mixed_identity,
}

ACCOUNT_SCENARIOS = tuple(ACCOUNT_BUILDERS)


# --------------------------------------------------------------------------
# Account oracle
# --------------------------------------------------------------------------


def classify_account(case: AccountCase, account) -> Outcome:
    """Whether the account said what the truth says, in the harness's terms."""
    defects: list[Defect] = []
    reasons = account.reasons()
    reason_text = " ".join(reasons)

    def real(kind, number, detail):
        defects.append(Defect(kind, number, named=False, detail=detail,
                              false_alarm=False))

    def alarm(kind, number, detail):
        defects.append(Defect(kind, number, named=True, detail=detail, false_alarm=True))

    for number, want in case.expected.items():
        got = [h for h in account.histories if h.claim_number == number]
        if want.mode == MERGE:
            if len(got) != 1 or sorted(
                    a.source_filename for a in got[0].appearances) != sorted(want.files):
                real("split-or-merged", number,
                     f"expected one history over {sorted(want.files)}, "
                     f"got {[sorted(a.source_filename for a in h.appearances) for h in got]}")
                continue
            history = got[0]
            if history.uncertain:
                alarm("false-uncertain", number, "identity flagged though every fact agrees")
                continue
            if want.current_file and history.appearances[-1].source_filename != want.current_file:
                real("current", number,
                     f"expected {want.current_file} to be priced from, "
                     f"got {history.appearances[-1].source_filename}")
            if want.development != "n/a" and history.development != want.development:
                real("development", number,
                     f"expected development {want.development}, got {history.development}")
            continue
        if len(got) != len(want.files) or any(
                len(h.appearances) != 1 for h in got) or sorted(
                h.appearances[0].source_filename for h in got) != sorted(want.files):
            real("split-or-merged", number,
                 f"expected {len(want.files)} separate histories over {sorted(want.files)}, "
                 f"got {[sorted(a.source_filename for a in h.appearances) for h in got]}")
            continue
        marked = [h for h in got if h.uncertain]
        if want.mode == UNCERTAIN and len(marked) != len(got):
            real("uncertain", number,
                 "identity kept apart but presented as certain")
        if want.mode == SPLIT and marked:
            alarm("false-uncertain", number, "identity flagged though every fact agrees")

    got_dropped = {h.claim_number for h in account.dropped}
    for number in sorted(case.dropped - got_dropped):
        real("dropped", number, "a claim a later run of its term omits was not reported")
    for number in sorted(got_dropped - case.dropped):
        alarm("false-dropped", number, "reported dropped though every later run omits nothing")

    if account.status is not case.status:
        if case.status is DocumentStatus.CLEAN:
            alarm("false-review", "account",
                  f"sent to review: {'; '.join(reasons)}")
        else:
            real("silent-review", "account",
                 f"expected NEEDS_REVIEW, got {account.status.value}")

    real_defects = [d for d in defects if not d.false_alarm]
    false_alarms = [d for d in defects if d.false_alarm]
    # A real defect is surfaced when the account's own reading names it: the
    # claim number appears in a reason, or an uncertain history carries its
    # message. Anything a reviewer is not told about is presented as fine.
    for defect in real_defects:
        defect.named = defect.token in reason_text or any(
            defect.token in (h.uncertain or "") for h in account.histories
        )
    if not defects:
        category = SAFE
    elif not real_defects:
        category = LOUD
    else:
        unnamed = [d for d in real_defects if not d.named]
        if unnamed and account.status is DocumentStatus.CLEAN:
            category = SILENT
        elif unnamed:
            category = UNNAMED
        else:
            category = SAFE
    return Outcome(
        category=category,
        status=account.status.value,
        defects=defects,
        rules=[],
        read=[h.claim_number for h in account.histories],
        expected=sorted(case.expected),
    )


# --------------------------------------------------------------------------
# Triage-family scenarios
# --------------------------------------------------------------------------


def _rules(kind, intelligence):
    AppetiteRules, LobOverride = intelligence.AppetiteRules, intelligence.LobOverride
    if kind == "none":
        return AppetiteRules()
    if kind == "open_only":
        return AppetiteRules(open_incurred_ceiling=Decimal("50000.00"))
    if kind == "full":
        return AppetiteRules(
            open_incurred_ceiling=Decimal("50000.00"),
            term_claim_count_ceiling=5,
            development_alert_amount=Decimal("5000.00"),
            development_alert_pct=Decimal("100"),
        )
    if kind == "dev_only":
        return AppetiteRules(
            development_alert_amount=Decimal("5000.00"),
            development_alert_pct=Decimal("100"),
        )
    if kind == "no_open":
        return AppetiteRules(open_incurred_ceiling=Decimal("50000.00"),
                             open_statuses=frozenset())
    if kind == "lob_only":
        return AppetiteRules(lob_overrides={
            "GL": LobOverride(open_incurred_ceiling=Decimal("40000.00")),
        })
    if kind == "lob_count":
        return AppetiteRules(term_claim_count_ceiling=2, lob_overrides={
            "GL": LobOverride(term_claim_count_ceiling=1),
        })
    raise ValueError(kind)


def _triage_sources(rng, *, count=2, lob="GL", trusted=True):
    return [
        _spec(rng, f"triage-{index}.pdf", valuation=index,
              lob=None if lob is None else LineOfBusiness(lob), trusted=trusted)
        for index in range(count)
    ]


def _triage_account_case(seed, scenario, sources, appearances):
    case = _case(seed, scenario, sources, appearances, {})
    for number in sorted({a.number for a in appearances}):
        _register_merge(case, sources, appearances, number)
    case.status = _expected_status(case)
    return case


def triage_case(seed: int) -> TriageCase:
    """One deterministic triage-shaped hazard."""
    from core import intelligence as _ti

    rng = random.Random(seed * 31 + 7)
    scenario = rng.choice(TRIAGE_SCENARIOS)
    return TRIAGE_BUILDERS[scenario](rng, seed, _ti)


def _t_clean(rng, seed, ti):
    sources = _triage_sources(rng)
    appearances = [
        _appear(rng, "triage-0.pdf", "K-1", incurred=Decimal("10000.00"),
                status=ClaimStatus.OPEN),
        _appear(rng, "triage-0.pdf", "K-2", incurred=Decimal("20000.00")),
    ]
    account = _triage_account_case(seed, "triage_clean", sources, appearances)
    return TriageCase(
        seed=seed, scenario="triage_clean", account=account,
        rules=_rules("full", ti),
        must_flag=frozenset(),
        must_not_flag=frozenset({("T-01 open-incurred-ceiling", "K-1"),
                                 ("T-01 open-incurred-ceiling", "K-2")}),
    )


def _t_over_ceiling(rng, seed, ti):
    sources = _triage_sources(rng)
    appearances = [
        _appear(rng, "triage-0.pdf", "K-3", incurred=Decimal("61000.00"),
                status=ClaimStatus.OPEN),
        _appear(rng, "triage-0.pdf", "K-4", incurred=Decimal("26000.00"),
                status=ClaimStatus.OPEN),
    ]
    account = _triage_account_case(seed, "triage_over_ceiling", sources, appearances)
    return TriageCase(
        seed=seed, scenario="triage_over_ceiling", account=account,
        rules=_rules("open_only", ti),
        must_flag=frozenset({("T-01 open-incurred-ceiling", "K-3")}),
        must_not_flag=frozenset({("T-01 open-incurred-ceiling", "K-4")}),
        not_evaluated_rules=frozenset({"T-02 term-claim-count-ceiling",
                                       "T-03 development-spike"}),
    )


def _t_unconfigured(rng, seed, ti):
    sources = _triage_sources(rng)
    appearances = [
        _appear(rng, "triage-0.pdf", "K-5", incurred=Decimal("61000.00"),
                status=ClaimStatus.OPEN),
    ]
    account = _triage_account_case(seed, "triage_unconfigured", sources, appearances)
    return TriageCase(
        seed=seed, scenario="triage_unconfigured", account=account,
        rules=_rules("none", ti),
        not_evaluated_rules=frozenset({
            "T-01 open-incurred-ceiling",
            "T-02 term-claim-count-ceiling",
            "T-03 development-spike",
        }),
    )


def _t_open_no_incurred(rng, seed, ti):
    sources = _triage_sources(rng)
    appearances = [
        _appear(rng, "triage-0.pdf", "K-6", incurred=None,
                status=ClaimStatus.OPEN),
        _appear(rng, "triage-0.pdf", "K-7", incurred=Decimal("30000.00"),
                status=ClaimStatus.OPEN),
    ]
    account = _triage_account_case(seed, "triage_open_no_incurred", sources, appearances)
    return TriageCase(
        seed=seed, scenario="triage_open_no_incurred", account=account,
        rules=_rules("open_only", ti),
        must_flag=frozenset(),
        must_not_flag=frozenset({("T-01 open-incurred-ceiling", "K-7")}),
        not_evaluated_rules=frozenset({"T-02 term-claim-count-ceiling",
                                       "T-03 development-spike"}),
        not_evaluated_claims=frozenset({("T-01 open-incurred-ceiling", "K-6")}),
    )


def _t_unknown_status(rng, seed, ti):
    sources = _triage_sources(rng)
    appearances = [
        _appear(rng, "triage-0.pdf", "K-8", incurred=Decimal("10000.00"),
                status=ClaimStatus.UNKNOWN),
        _appear(rng, "triage-0.pdf", "K-9", incurred=Decimal("10000.00"),
                status=ClaimStatus.OPEN),
    ]
    account = _triage_account_case(seed, "triage_unknown_status", sources, appearances)
    return TriageCase(
        seed=seed, scenario="triage_unknown_status", account=account,
        rules=_rules("open_only", ti),
        must_flag=frozenset({("T-06 unknown-claim-status", "K-8")}),
        must_not_flag=frozenset({("T-06 unknown-claim-status", "K-9")}),
        not_evaluated_rules=frozenset({"T-02 term-claim-count-ceiling",
                                       "T-03 development-spike"}),
    )


def _t_unknown_alae_deductible(rng, seed, ti):
    sources = _triage_sources(rng)
    appearances = [
        _appear(rng, "triage-0.pdf", "K-10", incurred=Decimal("10000.00"),
                alae=AlaeTreatment.UNKNOWN, deductible=DeductibleBasis.UNKNOWN),
    ]
    account = _triage_account_case(seed, "triage_unknown_alae_deductible",
                                   sources, appearances)
    return TriageCase(
        seed=seed, scenario="triage_unknown_alae_deductible", account=account,
        rules=_rules("open_only", ti),
        not_evaluated_rules=frozenset({"T-02 term-claim-count-ceiling",
                                       "T-03 development-spike"}),
        unknown_alae=True,
        unknown_deductible=True,
    )


def _t_development(rng, seed, ti):
    carrier, policy = CARRIERS[0], "POL-4400"
    sources = [
        _spec(rng, "dev-old.pdf", carrier=carrier, policy=policy, valuation=0),
        _spec(rng, "dev-new.pdf", carrier=carrier, policy=policy, valuation=1),
    ]
    appearances = [
        _appear(rng, "dev-old.pdf", "G-1", incurred=Decimal("1000.00")),
        _appear(rng, "dev-new.pdf", "G-1", incurred=Decimal("25000.00")),
        _appear(rng, "dev-old.pdf", "G-2", incurred=Decimal("10000.00")),
        _appear(rng, "dev-new.pdf", "G-2", incurred=Decimal("10500.00")),
    ]
    account = _triage_account_case(seed, "triage_development", sources, appearances)
    return TriageCase(
        seed=seed, scenario="triage_development", account=account,
        rules=_rules("dev_only", ti),
        must_flag=frozenset({("T-03 development-spike", "G-1")}),
        must_not_flag=frozenset({("T-03 development-spike", "G-2")}),
        not_evaluated_rules=frozenset({"T-01 open-incurred-ceiling",
                                       "T-02 term-claim-count-ceiling"}),
    )


def _t_development_unreadable(rng, seed, ti):
    carrier, policy = CARRIERS[1], "POL-4401"
    sources = [
        _spec(rng, "dev-old.pdf", carrier=carrier, policy=policy, valuation=0),
        _spec(rng, "dev-new.pdf", carrier=carrier, policy=policy, valuation=1),
    ]
    appearances = [
        _appear(rng, "dev-old.pdf", "G-3", incurred=None),
        _appear(rng, "dev-new.pdf", "G-3", incurred=Decimal("25000.00")),
        _appear(rng, "dev-old.pdf", "G-4", incurred=Decimal("0.00")),
        _appear(rng, "dev-new.pdf", "G-4", incurred=Decimal("25000.00")),
    ]
    account = _triage_account_case(seed, "triage_development_unreadable",
                                   sources, appearances)
    return TriageCase(
        seed=seed, scenario="triage_development_unreadable", account=account,
        rules=_rules("dev_only", ti),
        not_evaluated_rules=frozenset({"T-01 open-incurred-ceiling",
                                       "T-02 term-claim-count-ceiling"}),
        not_evaluated_claims=frozenset({("T-03 development-spike", "G-3"),
                                        ("T-03 development-spike", "G-4")}),
    )


def _t_lob_partial_attribution(rng, seed, ti):
    sources = [
        _spec(rng, "lob-gl.pdf", carrier=CARRIERS[0], policy="POL-LOB-1",
              valuation=0, lob=LineOfBusiness.GL),
        # Same carrier and policy, so both runs' L-2 is one history; the runs
        # disagree about the line of business, so the history is attributable
        # to neither and the LOB override cannot rescue it.
        _spec(rng, "lob-wc.pdf", carrier=CARRIERS[0], policy="POL-LOB-1",
              valuation=1, lob=LineOfBusiness.WC),
    ]
    appearances = [
        _appear(rng, "lob-gl.pdf", "L-1", incurred=Decimal("45000.00"),
                status=ClaimStatus.OPEN),
        _appear(rng, "lob-gl.pdf", "L-2", incurred=Decimal("45000.00"),
                status=ClaimStatus.OPEN),
        _appear(rng, "lob-wc.pdf", "L-2", incurred=Decimal("45000.00"),
                status=ClaimStatus.OPEN),
    ]
    account = _triage_account_case(seed, "triage_lob_partial", sources, appearances)
    return TriageCase(
        seed=seed, scenario="triage_lob_partial", account=account,
        rules=_rules("lob_only", ti),
        must_flag=frozenset({("T-01 open-incurred-ceiling", "L-1")}),
        must_not_flag=frozenset(),
        not_evaluated_rules=frozenset({"T-02 term-claim-count-ceiling",
                                       "T-03 development-spike"}),
        not_evaluated_claims=frozenset({("T-01 open-incurred-ceiling", "L-2")}),
    )


def _t_no_open_statuses(rng, seed, ti):
    sources = _triage_sources(rng)
    appearances = [
        _appear(rng, "triage-0.pdf", "N-1", incurred=Decimal("61000.00"),
                status=ClaimStatus.OPEN),
    ]
    account = _triage_account_case(seed, "triage_no_open_statuses", sources, appearances)
    return TriageCase(
        seed=seed, scenario="triage_no_open_statuses", account=account,
        rules=_rules("no_open", ti),
        not_evaluated_rules=frozenset({"T-01 open-incurred-ceiling",
                                       "T-02 term-claim-count-ceiling",
                                       "T-03 development-spike"}),
    )


def _t_term_count(rng, seed, ti):
    sources = [_spec(rng, "count.pdf", valuation=0, lob="GL")]
    appearances = [
        _appear(rng, "count.pdf", f"W-{index}", loss=date(2023, 3, 1))
        for index in range(4)
    ]
    account = _triage_account_case(seed, "triage_term_count", sources, appearances)
    return TriageCase(
        seed=seed, scenario="triage_term_count", account=account,
        rules=_rules("lob_count", ti),
        must_flag=frozenset({("T-02 term-claim-count-ceiling", f"W-{index}")
                             for index in range(4)}),
        must_not_flag=frozenset(),
        not_evaluated_rules=frozenset({"T-01 open-incurred-ceiling",
                                       "T-03 development-spike"}),
    )


def _t_uncertain_and_dropped(rng, seed, ti):
    sources = [
        _spec(rng, "mix-old.pdf", carrier=CARRIERS[0], policy="POL-9500", valuation=0),
        _spec(rng, "mix-new.pdf", carrier=CARRIERS[0], policy="POL-9500", valuation=1),
        # The anonymous run is the latest valuation, so no later run covers
        # the uncertain pair's term: the drop check would otherwise probe an
        # identity the account has already given up settling.
        _spec(rng, "mix-anon.pdf", carrier=None, policy=None, terms=(), valuation=2),
    ]
    appearances = [
        _appear(rng, "mix-old.pdf", "Q-1", loss=date(2023, 5, 1)),
        _appear(rng, "mix-new.pdf", "Q-2", loss=date(2023, 6, 1)),
        _appear(rng, "mix-anon.pdf", "Q-2", loss=date(2023, 6, 1)),
    ]
    case = _case(seed, "triage_uncertain_dropped", sources, appearances, {},
                 dropped={"Q-1"})
    _register_merge(case, sources, appearances, "Q-1")
    case.expected["Q-2"] = ExpectedHistory(
        files=("mix-new.pdf", "mix-anon.pdf"), mode=UNCERTAIN)
    case.status = _expected_status(case)
    return TriageCase(
        seed=seed, scenario="triage_uncertain_dropped", account=case,
        rules=_rules("full", ti),
        must_flag=frozenset({("T-04 uncertain-identity", "Q-2"),
                             ("T-05 dropped-claim", "Q-1")}),
    )


TRIAGE_BUILDERS = {
    "triage_clean": _t_clean,
    "triage_over_ceiling": _t_over_ceiling,
    "triage_unconfigured": _t_unconfigured,
    "triage_open_no_incurred": _t_open_no_incurred,
    "triage_unknown_status": _t_unknown_status,
    "triage_unknown_alae_deductible": _t_unknown_alae_deductible,
    "triage_development": _t_development,
    "triage_development_unreadable": _t_development_unreadable,
    "triage_lob_partial": _t_lob_partial_attribution,
    "triage_no_open_statuses": _t_no_open_statuses,
    "triage_term_count": _t_term_count,
    "triage_uncertain_dropped": _t_uncertain_and_dropped,
}

TRIAGE_SCENARIOS = tuple(TRIAGE_BUILDERS)


# --------------------------------------------------------------------------
# Triage oracle
# --------------------------------------------------------------------------


def _flag_covers(flag, rule, number) -> bool:
    return flag.rule == rule and number in flag.claim_numbers


def classify_triage(case: TriageCase, summary) -> Outcome:
    """Whether the triage summary said what the truth says.

    A check that cannot evaluate is SAFE only when the summary surfaces it as
    not evaluated; presented as passing, it is SILENT. A flag the truth
    requires, absent while the account reads as fine, is SILENT too.
    """
    defects: list[Defect] = []

    def real(kind, token, detail, named):
        defects.append(Defect(kind, token, named=named, detail=detail, false_alarm=False))

    def alarm(kind, token, detail):
        defects.append(Defect(kind, token, named=True, detail=detail, false_alarm=True))

    surfaced_text = " ".join(summary.not_evaluated)

    for rule in sorted(case.not_evaluated_rules):
        flags = [f for f in summary.flags if f.rule == rule and not f.evaluated]
        if not flags:
            real("cannot-evaluate-hidden", rule,
                 f"{rule} cannot evaluate but the summary does not say so", named=False)
            continue
        for flag in flags:
            if not flag.not_evaluated_reason or flag.not_evaluated_reason not in surfaced_text:
                real("cannot-evaluate-hidden", rule,
                     f"{rule} reports not evaluated but the reason is not surfaced",
                     named=False)

    for rule, number in sorted(case.not_evaluated_claims):
        flags = [f for f in summary.flags
                 if _flag_covers(f, rule, number) and not f.evaluated]
        if not flags:
            real("cannot-evaluate-hidden", f"{rule}/{number}",
                 f"{rule} cannot evaluate {number} but the summary does not say so",
                 named=False)
            continue
        for flag in flags:
            if not flag.not_evaluated_reason or flag.not_evaluated_reason not in surfaced_text:
                real("cannot-evaluate-hidden", f"{rule}/{number}",
                     f"{rule} reports not evaluated for {number} but it is not surfaced",
                     named=False)

    for rule, number in sorted(case.must_flag):
        if not any(_flag_covers(f, rule, number) and f.evaluated for f in summary.flags):
            surfaced = (
                any(f.rule == rule for f in summary.flags) or number in surfaced_text
            )
            real("missing-flag", f"{rule}/{number}",
                 f"{rule} must fire for {number}", named=surfaced)

    for rule, number in sorted(case.must_not_flag):
        if any(_flag_covers(f, rule, number) and f.evaluated for f in summary.flags):
            alarm("false-flag", f"{rule}/{number}",
                  f"{rule} fired for {number} though the account is inside appetite")

    if case.unknown_alae and not any("ALAE" in item for item in summary.not_evaluated):
        real("cannot-evaluate-hidden", "ALAE",
             "claims carry UNKNOWN ALAE treatment but the summary does not say so",
             named=False)
    if case.unknown_deductible and not any(
            "deductible" in item.lower() for item in summary.not_evaluated):
        real("cannot-evaluate-hidden", "deductible",
             "claims carry UNKNOWN deductible basis but the summary does not say so",
             named=False)

    if summary.status is not case.account.status:
        if case.account.status is DocumentStatus.CLEAN:
            alarm("false-review", "account",
                  f"triage presents the account as reviewed when the account reads "
                  f"{case.account.status.value}")
        else:
            real("silent-review", "account",
                 f"account reads {case.account.status.value} but triage presents "
                 f"{summary.status.value}", named=False)

    real_defects = [d for d in defects if not d.false_alarm]
    false_alarms = [d for d in defects if d.false_alarm]
    if not defects:
        category = SAFE
    elif not real_defects:
        category = LOUD
    else:
        unnamed = [d for d in real_defects if not d.named]
        if unnamed:
            category = SILENT
        else:
            category = SAFE
    return Outcome(
        category=category,
        status=summary.status.value,
        defects=defects,
        rules=sorted({f.rule for f in summary.flags}),
        read=[],
        expected=sorted(f"{rule}/{number}" for rule, number in case.must_flag),
    )


# --------------------------------------------------------------------------
# Runner helpers
# --------------------------------------------------------------------------


def account_outcome(seed: int) -> Outcome:
    case = account_case(seed)
    return classify_account(case, case.build())


def triage_outcome(seed: int):
    from core import intelligence as _ti

    case = triage_case(seed)
    if _ti is None:
        raise ImportError("core.intelligence is not present on this branch")
    summary = _ti.triage_summary(case.account.build(), case.rules)
    return classify_triage(case, summary)
