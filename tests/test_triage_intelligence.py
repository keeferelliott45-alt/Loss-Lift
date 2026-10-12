"""Underwriter-triage layer (core.intelligence) over merged accounts.

Synthetic data only -- nothing here resembles a real loss run. These tests
pin the triage layer's fail-closed semantics: an unconfigured threshold is
never a pass, an unknown movement is never zero, and every flag carries the
provenance it was derived from or says plainly that it could not be
evaluated and why.

The triage rule ids (T-01 .. T-06) are triage-local and deliberately distinct
from the reconciliation rule ids (R-01 ...): a triage flag is an appetite
observation for a person deciding whether to write the account, not a
statement that a document was read wrongly.

Workbook round-trip tests for the triage export land with the export hook in
a later commit in this same lane -- ``account_triage_to_bytes`` does not
exist yet, so no export tests live here.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from core.account import build_account
from core.intelligence import (
    AppetiteConfigError,
    AppetiteRules,
    FlagSource,
    LobOverride,
    T01_OPEN_INCURRED,
    T02_TERM_COUNT,
    T03_DEVELOPMENT,
    T04_UNCERTAIN_IDENTITY,
    T05_DROPPED_CLAIM,
    T06_UNKNOWN_STATUS,
    TriageFlag,
    evaluate_appetite,
    load_appetite_rules,
    triage_summary,
)
from core.schema import (
    Claim,
    ClaimStatus,
    DocumentStatus,
    Finding,
    LineOfBusiness,
    LogicalRun,
    LossRunDocument,
    ReconciliationResult,
    Severity,
)

TERM_2023 = (date(2023, 1, 1), date(2023, 12, 31))
TERM_2024 = (date(2024, 1, 1), date(2024, 12, 31))
TERM_2025 = (date(2025, 1, 1), date(2025, 12, 31))

CLEAN = ReconciliationResult(status=DocumentStatus.CLEAN)

#: Thresholds used when a test's point is the flag, not the config.
RICH_RULES = AppetiteRules(
    open_incurred_ceiling=Decimal("2000.00"),
    term_claim_count_ceiling=1,
    development_alert_amount=Decimal("2000.00"),
    development_alert_pct=Decimal("50"),
)


def _claim(
    number: str,
    loss: date,
    incurred: str,
    status: ClaimStatus = ClaimStatus.OPEN,
    page: int = 1,
    row: int | None = None,
) -> Claim:
    return Claim(
        claim_number=number,
        date_of_loss=loss,
        claim_status=status,
        paid_total=Decimal(incurred),
        reserve_total=Decimal("0"),
        recovery_total=Decimal("0"),
        incurred_total=Decimal(incurred),
        source_page=page,
        source_row=row,
    )


def _run(
    filename: str,
    valuation: date,
    claims: list[Claim],
    *,
    insured: str | None = "Acme Haulage",
    carrier: str | None = "Carrier A",
    policy: str | None = "POL-1",
    periods: list[tuple[date, date]] | None = None,
    lob: LineOfBusiness | None = None,
) -> LossRunDocument:
    return LossRunDocument(
        source_filename=filename,
        file_sha256=filename,
        named_insured=insured,
        carrier=carrier,
        policy_number=policy,
        line_of_business=lob,
        valuation_date=valuation,
        policy_periods=periods or [TERM_2023, TERM_2024],
        claims=claims,
    )


def _clean(*documents: LossRunDocument) -> dict[str, ReconciliationResult]:
    return {document.document_id: CLEAN for document in documents}


def _packet() -> LossRunDocument:
    runs = [
        LogicalRun(run_id="run-1", pages=[1], confidence="printed", carrier="Carrier A",
                   policy_number="GL-1", valuation_date=date(2023, 12, 31),
                   named_insured="Acme Haulage",
                   policy_period_start=TERM_2023[0], policy_period_end=TERM_2023[1]),
        LogicalRun(run_id="run-2", pages=[2], confidence="printed", carrier="Carrier B",
                   policy_number="AU-9", valuation_date=date(2024, 3, 31),
                   policy_period_start=TERM_2023[0], policy_period_end=TERM_2023[1]),
    ]
    return LossRunDocument(
        source_filename="packet.pdf", file_sha256="p", named_insured="Acme Haulage",
        carrier="Carrier A", policy_number="GL-1", valuation_date=date(2023, 12, 31),
        claims=[_claim("100", date(2023, 5, 1), "4000.00", page=1, row=2),
                _claim("100", date(2023, 5, 1), "7000.00", page=2, row=2)],
        runs=runs,
    )


def _rich_account():
    """Two runs of one term: C-1 worsens 1000 -> 4000, C-2 closes, C-5 new."""
    old = _run("old.pdf", date(2023, 12, 31), [
        _claim("C-1", date(2023, 5, 1), "1000.00"),
        _claim("C-2", date(2023, 8, 1), "500.00"),
    ])
    new = _run("new.pdf", date(2024, 12, 31), [
        _claim("C-1", date(2023, 5, 1), "4000.00", page=3, row=4),
        _claim("C-2", date(2023, 8, 1), "500.00", status=ClaimStatus.CLOSED),
        _claim("C-5", date(2024, 3, 1), "250.00"),
    ])
    return build_account("Acme Haulage", [old, new])


# --------------------------------------------------------------------------
# Required behaviors
# --------------------------------------------------------------------------

def test_T01_T02_T03_fire_on_synthetic_account():
    account = _rich_account()
    flags = evaluate_appetite(account, RICH_RULES)

    t01 = [f for f in flags if f.rule == T01_OPEN_INCURRED]
    assert len(t01) == 1
    flag = t01[0]
    assert flag.severity is Severity.WARN
    assert flag.evaluated is True
    assert flag.claim_numbers == ("C-1",)
    assert flag.reason == "open claim C-1 incurred 4000.00 exceeds ceiling 2000.00"
    # provenance comes from the CURRENT appearance's claim
    history = next(h for h in account.histories if h.claim_number == "C-1")
    current = history.current
    appearance = history.appearances[-1]
    (source,) = flag.sources
    assert isinstance(source, FlagSource)
    assert source.document_id == appearance.document_id
    assert source.source_filename == appearance.source_filename
    assert source.run_id == appearance.run_id
    assert source.page == current.source_page
    assert source.row_id == current.row_id
    assert source.claim_number == current.claim_number

    t02 = [f for f in flags if f.rule == T02_TERM_COUNT]
    assert len(t02) == 1
    assert t02[0].severity is Severity.WARN
    assert t02[0].evaluated is True
    assert t02[0].claim_numbers == ("C-1", "C-2")
    assert "above the ceiling of 1" in t02[0].reason
    assert len(t02[0].sources) == 2

    t03 = [f for f in flags if f.rule == T03_DEVELOPMENT]
    assert len(t03) == 1
    assert t03[0].severity is Severity.WARN
    assert t03[0].evaluated is True
    assert t03[0].claim_numbers == ("C-1",)
    assert "grew from 1000.00 to 4000.00" in t03[0].reason
    assert "movement 3000.00 exceeds alert amount 2000.00" in t03[0].reason
    assert "300.00% of the earliest valuation exceeds the 50% alert" in t03[0].reason
    # sources cover both valuations the development was computed from
    assert {s.source_filename for s in t03[0].sources} == {"old.pdf", "new.pdf"}


def test_UNKNOWN_status_is_cannot_price_never_a_pass():
    doc = _run("unknown.pdf", date(2024, 12, 31), [
        _claim("U-1", date(2024, 3, 1), "500.00", status=ClaimStatus.UNKNOWN),
    ])
    account = build_account("Acme Haulage", [doc])
    flags = evaluate_appetite(account, AppetiteRules(
        open_incurred_ceiling=Decimal("2000.00"),
    ))
    unknown = [f for f in flags if f.rule == T06_UNKNOWN_STATUS]
    assert len(unknown) == 1
    flag = unknown[0]
    assert flag.severity is Severity.WARN
    assert flag.claim_numbers == ("U-1",)
    assert flag.evaluated is False
    assert flag.not_evaluated_reason == "claim status UNKNOWN - cannot price"
    assert "cannot price" in flag.reason
    # no flag anywhere records the claim as evaluated/passing
    for flag in flags:
        if "U-1" in flag.claim_numbers:
            assert flag.evaluated is False


def test_unconfigured_rules_report_not_configured_not_a_pass():
    account = _rich_account()
    flags = evaluate_appetite(account, AppetiteRules())
    by_rule = {f.rule: f for f in flags}
    assert set(by_rule) == {T01_OPEN_INCURRED, T02_TERM_COUNT, T03_DEVELOPMENT}
    for rule in (T01_OPEN_INCURRED, T02_TERM_COUNT, T03_DEVELOPMENT):
        flag = by_rule[rule]
        assert flag.severity is Severity.INFO
        assert flag.evaluated is False
        assert flag.claim_numbers == ()
        assert flag.sources == ()
        assert flag.not_evaluated_reason is not None
        assert "not configured" in flag.not_evaluated_reason
    # an unconfigured threshold is never a pass and never a WARN over-ceiling
    assert all(f.severity is Severity.INFO for f in flags)
def test_REOPENED_counts_as_open_under_default_open_statuses():
    doc = _run("reopen.pdf", date(2024, 12, 31), [
        _claim("R-1", date(2023, 5, 1), "4000.00", status=ClaimStatus.REOPENED),
    ])
    account = build_account("Acme Haulage", [doc])
    default = evaluate_appetite(account, AppetiteRules(
        open_incurred_ceiling=Decimal("2000.00"),
    ))
    t01 = [f for f in default if f.rule == T01_OPEN_INCURRED]
    assert len(t01) == 1
    assert t01[0].claim_numbers == ("R-1",)
    assert t01[0].evaluated is True
    assert t01[0].severity is Severity.WARN

    open_only = evaluate_appetite(account, AppetiteRules(
        open_incurred_ceiling=Decimal("2000.00"),
        open_statuses=frozenset({ClaimStatus.OPEN}),
    ))
    assert all(f.rule != T01_OPEN_INCURRED for f in open_only)


def test_REPORT_ONLY_is_listed_but_gets_no_open_flags():
    doc = _run("ro.pdf", date(2024, 12, 31), [
        _claim("C-1", date(2023, 5, 1), "1000.00"),
        _claim("RO-1", date(2023, 6, 1), "0.00", status=ClaimStatus.REPORT_ONLY),
    ])
    account = build_account("Acme Haulage", [doc])
    rules = AppetiteRules(
        open_incurred_ceiling=Decimal("2000.00"),
        term_claim_count_ceiling=5,
    )
    flags = evaluate_appetite(account, rules)
    for flag in flags:
        if flag.rule in (T01_OPEN_INCURRED, T06_UNKNOWN_STATUS):
            assert "RO-1" not in flag.claim_numbers
    # ...but the report-only claim is not silently dropped from the book
    assert "RO-1" in {c.claim_number for c in account.claims}
    summary = triage_summary(account, rules)
    period_claims = {p.label: p.claims for p in summary.periods}
    assert period_claims["2023-01-01 to 2023-12-31"] == 2


def test_uncertain_identity_is_always_flagged():
    a = _run("a.pdf", date(2023, 9, 30), [_claim("100", date(2023, 5, 1), "1000.00")],
             policy="P-A")
    a2 = _run("a2.pdf", date(2024, 6, 30), [_claim("100", date(2023, 5, 1), "2000.00")],
              policy="P-A")
    b = _run("b.pdf", date(2024, 12, 31), [_claim("100", date(2023, 5, 1), "9000.00")],
             carrier=None, policy=None)
    c = _run("c.pdf", date(2023, 9, 30), [_claim("200", date(2023, 6, 1), "1000.00")],
             carrier="Carrier C", policy="P-C")
    d = _run("d.pdf", date(2024, 12, 31), [_claim("200", date(2023, 6, 1), "9000.00")],
             carrier=None, policy=None)
    account = build_account("Acme Haulage", [a, a2, b, c, d])
    assert account.uncertain

    for rules in (AppetiteRules(), RICH_RULES):
        flags = evaluate_appetite(account, rules)
        t04 = [f for f in flags if f.rule == T04_UNCERTAIN_IDENTITY]
        assert len(t04) == 2
        for flag in t04:
            assert flag.severity is Severity.WARN
            assert flag.evaluated is True
        # one flag per claim number; its sources cover every appearance of
        # every history that could not be attributed (dedupe merges, never drops)
        for flag in t04:
            number = flag.claim_numbers[0]
            appearances = [
                app
                for history in account.uncertain
                if history.claim_number == number
                for app in history.appearances
            ]
            assert {s.document_id for s in flag.sources} == {app.document_id for app in appearances}
            assert len(flag.sources) == len(appearances)
    # the two-run identity is represented: one flag cites both runs of its claim
    flags = evaluate_appetite(account, RICH_RULES)
    assert any(len(f.sources) == 2 for f in flags if f.rule == T04_UNCERTAIN_IDENTITY)


def test_dropped_claim_flagged_for_reviewer_decision():
    old = _run("old.pdf", date(2023, 12, 31), [
        _claim("C-1", date(2023, 5, 1), "1000.00"),
        _claim("C-2", date(2023, 8, 1), "500.00"),
    ])
    later = _run("new.pdf", date(2024, 12, 31), [
        _claim("C-5", date(2024, 3, 1), "250.00"),
    ])
    account = build_account("Acme Haulage", [old, later])
    assert {h.claim_number for h in account.dropped} == {"C-1", "C-2"}
    flags = evaluate_appetite(account, AppetiteRules())
    t05 = [f for f in flags if f.rule == T05_DROPPED_CLAIM]
    assert len(t05) == 2
    assert {f.claim_numbers[0] for f in t05} == {"C-1", "C-2"}
    for flag in t05:
        assert flag.severity is Severity.WARN
        assert flag.evaluated is True
        assert "reviewer to decide" in flag.reason
        assert len(flag.sources) == 1


def test_severity_pinning_T_rules_warn_not_configured_info():
    def warn_rules(account, rules):
        return {f.rule for f in evaluate_appetite(account, rules) if f.severity is Severity.WARN}

    rich = _rich_account()
    assert T01_OPEN_INCURRED in warn_rules(rich, RICH_RULES)
    assert T02_TERM_COUNT in warn_rules(rich, RICH_RULES)
    assert T03_DEVELOPMENT in warn_rules(rich, RICH_RULES)

    uncertain = build_account("Acme Haulage", [
        _run("a.pdf", date(2023, 9, 30), [_claim("100", date(2023, 5, 1), "1000.00")], policy="P-A"),
        _run("b.pdf", date(2024, 12, 31), [_claim("100", date(2023, 5, 1), "9000.00")],
             carrier=None, policy=None),
    ])
    assert T04_UNCERTAIN_IDENTITY in warn_rules(uncertain, AppetiteRules())

    dropped = build_account("Acme Haulage", [
        _run("old.pdf", date(2023, 12, 31), [_claim("C-1", date(2023, 5, 1), "1000.00")]),
        _run("new.pdf", date(2024, 12, 31), [_claim("C-5", date(2024, 3, 1), "250.00")]),
    ])
    assert T05_DROPPED_CLAIM in warn_rules(dropped, AppetiteRules())

    unknown = build_account("Acme Haulage", [
        _run("u.pdf", date(2024, 12, 31), [
            _claim("U-1", date(2024, 3, 1), "500.00", status=ClaimStatus.UNKNOWN)]),
    ])
    assert T06_UNKNOWN_STATUS in warn_rules(unknown, AppetiteRules())

    bare = evaluate_appetite(rich, AppetiteRules())
    assert {f.rule for f in bare if f.severity is Severity.INFO} == {
        T01_OPEN_INCURRED, T02_TERM_COUNT, T03_DEVELOPMENT,
    }


def test_empty_open_statuses_disables_open_checks_with_explicit_item():
    doc = _run("mix.pdf", date(2024, 12, 31), [
        _claim("C-1", date(2023, 5, 1), "4000.00"),
        _claim("U-1", date(2023, 8, 1), "500.00", status=ClaimStatus.UNKNOWN),
    ])
    account = build_account("Acme Haulage", [doc])
    rules = AppetiteRules(
        open_incurred_ceiling=Decimal("2000.00"),
        open_statuses=frozenset(),
    )
    flags = evaluate_appetite(account, rules)
    t01 = [f for f in flags if f.rule == T01_OPEN_INCURRED]
    assert len(t01) == 1
    assert t01[0].severity is Severity.INFO
    assert t01[0].evaluated is False
    assert t01[0].claim_numbers == ()
    assert t01[0].not_evaluated_reason == (
        "no statuses configured as open - open-claim checks cannot evaluate"
    )
    # no per-claim open checks, and no T-06 at all
    assert not any(f.rule == T06_UNKNOWN_STATUS for f in flags)
    assert not any(f.rule == T01_OPEN_INCURRED and f.claim_numbers for f in flags)
    summary = triage_summary(account, rules)
    assert "no statuses configured as open - open-claim checks cannot evaluate" in (
        summary.not_evaluated
    )


def test_lob_override_applies_to_attributed_history_only():
    wc = _run("wc.pdf", date(2024, 12, 31),
              [_claim("W-1", date(2023, 5, 1), "3000.00")], policy="WC-1",
              lob=LineOfBusiness.WC)
    gl = _run("gl.pdf", date(2024, 12, 31),
              [_claim("G-1", date(2023, 6, 1), "3000.00")], policy="GL-1",
              lob=LineOfBusiness.GL)
    account = build_account("Acme Haulage", [wc, gl])
    rules = AppetiteRules(
        open_incurred_ceiling=Decimal("4000.00"),
        lob_overrides={"WC": LobOverride(open_incurred_ceiling=Decimal("2000.00"))},
    )
    flags = evaluate_appetite(account, rules)
    t01 = [f for f in flags if f.rule == T01_OPEN_INCURRED]
    assert len(t01) == 1
    assert t01[0].claim_numbers == ("W-1",)
    assert t01[0].evaluated is True
    assert "exceeds ceiling 2000.00" in t01[0].reason
    # the non-WC claim keeps the account-level ceiling and does not flag
    assert not any("G-1" in f.claim_numbers for f in flags)


def test_overrides_without_attributed_lob_are_explicit_not_evaluated():
    doc = _run("nolob.pdf", date(2024, 12, 31), [
        _claim("C-1", date(2023, 5, 1), "3000.00"),
    ])
    account = build_account("Acme Haulage", [doc])
    rules = AppetiteRules(lob_overrides={
        "WC": LobOverride(open_incurred_ceiling=Decimal("2000.00")),
    })
    summary = triage_summary(account, rules)
    assert (
        "per-LOB override(s) present but line of business is not attributed "
        "in the extracted data - overrides not applied"
    ) in summary.not_evaluated
    # with no LOB the override hangs on nothing: the open check cannot evaluate
    t01 = [f for f in summary.flags if f.rule == T01_OPEN_INCURRED]
    assert t01 and all(f.evaluated is False for f in t01)
    assert all("cannot evaluate against ceiling" in f.not_evaluated_reason for f in t01)


def test_triage_summary_composition():
    one = _run("unreconciled.pdf", date(2023, 12, 31), [
        _claim("C-1", date(2023, 5, 1), "1000.00"),
        _claim("U-1", date(2023, 8, 1), "500.00", status=ClaimStatus.UNKNOWN),
    ], periods=[TERM_2023])
    two = _run("needsreview.pdf", date(2025, 12, 31), [
        _claim("C-2", date(2025, 3, 1), "2000.00"),
    ], periods=[TERM_2025])
    account = build_account(
        "Acme Haulage",
        [one, two],
        {two.document_id: ReconciliationResult(status=DocumentStatus.NEEDS_REVIEW)},
    )
    summary = triage_summary(account, AppetiteRules())
    assert summary.account_name == "Acme Haulage"
    assert summary.status == account.status
    assert summary.reasons == tuple(account.reasons())
    assert summary.periods == tuple(account.periods)
    assert summary.period_gaps == (
        "coverage gap: 2023-12-31 to 2025-01-01 not covered by any stated term",
    )
    notes = "\n".join(summary.not_evaluated)
    assert "1 claim(s) carry UNKNOWN claim status - cannot price" in notes
    assert (
        "3 claim(s) carry UNKNOWN ALAE treatment - ALAE treatment not evaluated"
    ) in notes
    assert (
        "3 claim(s) carry UNKNOWN deductible basis - deductible basis not evaluated"
    ) in notes
    assert "unreconciled.pdf: not reconciled - source not trusted" in notes
    assert "needsreview.pdf: needs review - source not trusted" in notes


def test_overlapping_terms_report_no_gap():
    short = _run("short.pdf", date(2023, 12, 31), [
        _claim("C-1", date(2023, 5, 1), "1000.00"),
    ], periods=[TERM_2023])
    long = _run("long.pdf", date(2025, 12, 31), [
        _claim("C-2", date(2024, 6, 1), "2000.00"),
    ], periods=[(date(2023, 6, 1), date(2025, 6, 30))])
    account = build_account("Acme Haulage", [short, long])
    summary = triage_summary(account, AppetiteRules())
    assert summary.period_gaps == ()


def test_triage_creates_no_findings_and_mutates_nothing():
    old = _run("old.pdf", date(2023, 12, 31), [
        _claim("C-1", date(2023, 5, 1), "1000.00"),
        _claim("C-2", date(2023, 8, 1), "500.00"),
    ])
    new = _run("new.pdf", date(2024, 12, 31), [
        _claim("C-1", date(2023, 5, 1), "4000.00"),
        _claim("C-2", date(2023, 8, 1), "500.00", ClaimStatus.CLOSED),
        _claim("C-5", date(2024, 3, 1), "250.00"),
    ])
    account = build_account("Acme Haulage", [old, new], _clean(old, new))
    assert account.status is DocumentStatus.CLEAN
    before = (
        [(h.claim_number, len(h.appearances), h.current.incurred_total)
         for h in account.histories],
        {c.claim_number: c.incurred_total for c in account.claims},
        list(account.reasons()),
        account.status,
    )
    flags = evaluate_appetite(account, RICH_RULES)
    summary = triage_summary(account, RICH_RULES)
    # triage returns triage flags, never reconciliation findings
    assert all(isinstance(f, TriageFlag) and not isinstance(f, Finding) for f in flags)
    assert all(isinstance(f, TriageFlag) and not isinstance(f, Finding) for f in summary.flags)
    # ...and it alters nothing upstream
    assert [(h.claim_number, len(h.appearances), h.current.incurred_total)
            for h in account.histories] == before[0]
    assert {c.claim_number: c.incurred_total for c in account.claims} == before[1]
    assert list(account.reasons()) == before[2]
    assert account.status is before[3]


def test_T01_flag_carries_packet_run_provenance():
    packet = _packet()
    result = ReconciliationResult(status=DocumentStatus.CLEAN, run_status={
        "run-1": DocumentStatus.CLEAN, "run-2": DocumentStatus.CLEAN,
    })
    account = build_account("Acme Haulage", [packet], {packet.document_id: result})
    flags = evaluate_appetite(account, AppetiteRules(open_incurred_ceiling=Decimal("2000.00")))
    t01 = [f for f in flags if f.rule == T01_OPEN_INCURRED]
    by_run = {f.sources[0].run_id: f for f in t01}
    assert "run-1" in by_run
    source = by_run["run-1"].sources[0]
    assert source.document_id == packet.document_id
    assert source.source_filename == "packet.pdf"
    assert source.page == 1
    assert source.row_id == "p1r2"
    assert source.claim_number == "100"
# --------------------------------------------------------------------------
# Fail-closed extras the semantics section promises
# --------------------------------------------------------------------------

def test_open_claim_without_incurred_cannot_evaluate():
    doc = _run("blank.pdf", date(2024, 12, 31), [
        Claim(claim_number="C-9", date_of_loss=date(2023, 5, 1),
              claim_status=ClaimStatus.OPEN),
    ])
    account = build_account("Acme Haulage", [doc])
    flags = evaluate_appetite(account, AppetiteRules(open_incurred_ceiling=Decimal("2000.00")))
    t01 = [f for f in flags if f.rule == T01_OPEN_INCURRED]
    assert len(t01) == 1
    assert t01[0].claim_numbers == ("C-9",)
    assert t01[0].evaluated is False
    assert "carries no incurred total" in t01[0].not_evaluated_reason


def test_development_spike_without_earliest_incurred_is_not_evaluated():
    old = _run("old.pdf", date(2023, 12, 31), [
        Claim(claim_number="C-9", date_of_loss=date(2023, 5, 1),
              claim_status=ClaimStatus.OPEN, incurred_total=None),
    ])
    new = _run("new.pdf", date(2024, 12, 31), [
        _claim("C-9", date(2023, 5, 1), "4000.00"),
    ])
    account = build_account("Acme Haulage", [old, new])
    flags = evaluate_appetite(account, AppetiteRules(
        development_alert_amount=Decimal("2000.00"),
        development_alert_pct=Decimal("50"),
    ))
    t03 = [f for f in flags if f.rule == T03_DEVELOPMENT]
    assert len(t03) == 1
    assert t03[0].evaluated is False
    assert "cannot compute development percentage" in t03[0].not_evaluated_reason
    assert t03[0].severity is Severity.WARN


def test_development_amount_alone_alerts_without_pct():
    old = _run("old.pdf", date(2023, 12, 31), [_claim("C-1", date(2023, 5, 1), "1000.00")])
    new = _run("new.pdf", date(2024, 12, 31), [_claim("C-1", date(2023, 5, 1), "4000.00")])
    account = build_account("Acme Haulage", [old, new])
    flags = evaluate_appetite(account, AppetiteRules(
        development_alert_amount=Decimal("2000.00"),
    ))
    t03 = [f for f in flags if f.rule == T03_DEVELOPMENT]
    assert len(t03) == 1
    assert t03[0].evaluated is True
    assert t03[0].severity is Severity.WARN
    assert "movement 3000.00 exceeds alert amount 2000.00" in t03[0].reason


# --------------------------------------------------------------------------
# Config loader
# --------------------------------------------------------------------------

def _rules_file(tmp_path, payload) -> str:
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_load_appetite_rules_round_trip(tmp_path):
    rules = load_appetite_rules(_rules_file(tmp_path, {
        "open_incurred_ceiling": "250000",
        "term_claim_count_ceiling": 40,
        "development_alert_amount": 100000.0,
        "development_alert_pct": "40",
        "open_statuses": ["OPEN"],
        "lob_overrides": {"WC": {
            "open_incurred_ceiling": 50000,
            "term_claim_count_ceiling": 10,
            "development_alert_amount": "25000",
            "development_alert_pct": 50,
        }},
    }))
    assert rules.open_incurred_ceiling == Decimal("250000")
    assert rules.term_claim_count_ceiling == 40
    assert rules.development_alert_amount == Decimal("100000")
    assert rules.development_alert_pct == Decimal("40")
    assert rules.open_statuses == frozenset({ClaimStatus.OPEN})
    wc = rules.lob_overrides["WC"]
    assert wc.open_incurred_ceiling == Decimal("50000")
    assert wc.term_claim_count_ceiling == 10
    assert wc.development_alert_amount == Decimal("25000")
    assert wc.development_alert_pct == Decimal("50")


def test_load_appetite_rules_refuses_unknown_keys(tmp_path):
    with pytest.raises(AppetiteConfigError):
        load_appetite_rules(_rules_file(tmp_path, {"open_incurred_celling": 5000}))


def test_load_appetite_rules_refuses_negative_ceiling(tmp_path):
    with pytest.raises(AppetiteConfigError):
        load_appetite_rules(_rules_file(tmp_path, {"open_incurred_ceiling": -1}))


def test_load_appetite_rules_refuses_boolean_threshold(tmp_path):
    with pytest.raises(AppetiteConfigError):
        load_appetite_rules(_rules_file(tmp_path, {"development_alert_pct": True}))


def test_load_appetite_rules_refuses_unknown_status(tmp_path):
    with pytest.raises(AppetiteConfigError):
        load_appetite_rules(_rules_file(tmp_path, {"open_statuses": ["DRAFT"]}))


def test_load_appetite_rules_refuses_non_object_root(tmp_path):
    with pytest.raises(AppetiteConfigError):
        load_appetite_rules(_rules_file(tmp_path, []))


def test_load_appetite_rules_refuses_unknown_lob(tmp_path):
    with pytest.raises(AppetiteConfigError):
        load_appetite_rules(_rules_file(tmp_path, {
            "lob_overrides": {"FRAUD": {"open_incurred_ceiling": 1}},
        }))


def test_load_appetite_rules_empty_object_is_safe_defaults(tmp_path):
    rules = load_appetite_rules(_rules_file(tmp_path, {}))
    assert rules.open_incurred_ceiling is None
    assert rules.term_claim_count_ceiling is None
    assert rules.development_alert_amount is None
    assert rules.development_alert_pct is None
    assert rules.lob_overrides == {}
    assert rules.open_statuses == frozenset({ClaimStatus.OPEN, ClaimStatus.REOPENED})
