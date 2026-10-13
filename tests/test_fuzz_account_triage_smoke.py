"""The account/triage hazard pack keeps its own promises.

Determinism (same seed, same case), oracle soundness (a wrong account or a
silent summary is caught on hand-checked shapes), and zero SILENT over a
bounded sweep that stays inside the suite's normal budget. The triage tests
skip on branches that do not carry ``core.intelligence``; the account tests
never do -- that layer is on main.
"""

from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from core.schema import DocumentStatus
from tests.fuzz import account_triage as pack
from tests.fuzz.oracle import LOUD, SAFE, SILENT, UNNAMED


def test_same_seed_builds_the_same_account_case():
    first, second = pack.account_case(11), pack.account_case(11)
    assert first.scenario == second.scenario
    assert first.sources == second.sources
    assert first.appearances == second.appearances
    assert first.expected == second.expected
    assert first.dropped == second.dropped


def test_the_account_oracle_passes_a_correct_account_on_every_scenario():
    for seed in range(40):
        outcome = pack.account_outcome(seed)
        assert outcome.category == SAFE, (seed, outcome.describe())


def test_the_account_oracle_catches_a_wrongly_merged_account():
    case = pack._two_carriers(random.Random(1), 1)
    good = pack.classify_account(case, case.build())
    assert good.category == SAFE

    merged = SimpleNamespace(
        claim_number="S-1",
        appearances=[
            SimpleNamespace(source_filename="run-one.pdf"),
            SimpleNamespace(source_filename="run-two.pdf"),
        ],
        uncertain=None,
        development=None,
    )
    account = SimpleNamespace(
        histories=[merged], dropped=[], status=DocumentStatus.CLEAN,
        reasons=lambda: [],
    )
    outcome = pack.classify_account(case, account)
    assert outcome.category == SILENT, outcome.describe()


def test_the_account_oracle_catches_a_certain_identity_that_is_not():
    case = pack._policy_missing(random.Random(2), 2)
    assert case.expected["U-1"].mode == pack.UNCERTAIN
    uncertain = SimpleNamespace(
        claim_number="U-1",
        appearances=[SimpleNamespace(source_filename="run-full.pdf")],
        uncertain=None,
        development=None,
    )
    account = SimpleNamespace(
        histories=[uncertain], dropped=[], status=DocumentStatus.CLEAN,
        reasons=lambda: [],
    )
    outcome = pack.classify_account(case, account)
    assert outcome.category == SILENT, outcome.describe()


def test_the_account_oracle_calls_a_false_alarm_loud():
    case = pack._single_run(random.Random(1), 1)
    good = pack.classify_account(case, case.build())
    assert good.category == SAFE

    real = case.build()
    account = SimpleNamespace(
        histories=real.histories, dropped=real.dropped,
        status=DocumentStatus.NEEDS_REVIEW,
        reasons=lambda: ["A-1: invented worry"],
    )
    outcome = pack.classify_account(case, account)
    assert outcome.category == LOUD, outcome.describe()


def _triage_or_skip():
    return pytest.importorskip("core.intelligence")


def test_same_seed_builds_the_same_triage_case():
    _triage_or_skip()
    first, second = pack.triage_case(5), pack.triage_case(5)
    assert first.scenario == second.scenario
    assert first.rules == second.rules
    assert first.account.expected == second.account.expected
    assert first.must_flag == second.must_flag


def test_the_triage_oracle_passes_the_product_on_every_scenario():
    intelligence = _triage_or_skip()
    for name, builder in pack.TRIAGE_BUILDERS.items():
        case = builder(random.Random(9), 9, intelligence)
        summary = intelligence.triage_summary(case.account.build(), case.rules)
        outcome = pack.classify_triage(case, summary)
        assert outcome.category == SAFE, (name, outcome.describe())


def test_a_check_that_cannot_evaluate_must_be_surfaced():
    intelligence = _triage_or_skip()
    case = pack._t_unconfigured(random.Random(3), 3, intelligence)
    account = case.account.build()

    honest = intelligence.triage_summary(account, case.rules)
    outcome = pack.classify_triage(case, honest)
    assert outcome.category == SAFE, outcome.describe()

    quiet = SimpleNamespace(
        status=honest.status, reasons=honest.reasons, periods=honest.periods,
        flags=tuple(
            SimpleNamespace(rule=f.rule, severity=f.severity,
                            claim_numbers=f.claim_numbers, reason=f.reason,
                            sources=f.sources, evaluated=False,
                            not_evaluated_reason=f.not_evaluated_reason)
            for f in honest.flags
        ),
        period_gaps=honest.period_gaps,
        not_evaluated=(),
    )
    outcome = pack.classify_triage(case, quiet)
    assert outcome.category == SILENT, outcome.describe()


def test_a_missing_flag_on_a_fine_looking_summary_is_silent():
    intelligence = _triage_or_skip()
    case = pack._t_over_ceiling(random.Random(4), 4, intelligence)
    assert case.must_flag
    summary = SimpleNamespace(
        status=DocumentStatus.CLEAN, reasons=(), periods=(),
        flags=(), period_gaps=(), not_evaluated=(),
    )
    outcome = pack.classify_triage(case, summary)
    assert outcome.category == SILENT, outcome.describe()


def test_a_false_flag_is_loud():
    intelligence = _triage_or_skip()
    case = pack._t_over_ceiling(random.Random(4), 4, intelligence)
    account = case.account.build()

    # The honest summary satisfies every must-flag and surfaces the checks
    # that cannot evaluate, so the case isolates the one thing it is named
    # for: a flag fired for a claim the account is inside appetite for.
    honest = intelligence.triage_summary(account, case.rules)
    assert pack.classify_triage(case, honest).category == SAFE

    number = sorted(case.must_not_flag)[0][1]
    false_flag = SimpleNamespace(
        rule="T-01 open-incurred-ceiling", severity=2, claim_numbers=(number,),
        reason="made up", sources=(), evaluated=True,
        not_evaluated_reason=None,
    )
    summary = SimpleNamespace(
        status=honest.status, reasons=honest.reasons, periods=honest.periods,
        flags=tuple(honest.flags) + (false_flag,),
        period_gaps=honest.period_gaps, not_evaluated=honest.not_evaluated,
    )
    outcome = pack.classify_triage(case, summary)
    assert outcome.category == LOUD, outcome.describe()


def test_the_cli_runner_passes_a_bounded_account_sweep():
    from tests.fuzz import account_triage_run

    assert account_triage_run.main(
        ["--family", "account", "--seed-start", "0", "--count", "30"]
    ) == 0


def test_the_cli_runner_passes_a_bounded_triage_sweep():
    _triage_or_skip()
    from tests.fuzz import account_triage_run

    assert account_triage_run.main(
        ["--family", "triage", "--seed-start", "0", "--count", "30"]
    ) == 0
