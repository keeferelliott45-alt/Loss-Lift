"""Smoke tests for the silent-CLEAN fuzz harness.

Fast, fixed-seed, synthetic only. They do not prove the product correct; they
prove the harness runs, is deterministic, catches nothing silent on the fixed
seeds, and holds the clean family's false-alarm baseline.
"""

from __future__ import annotations

import pymupdf
import pytest

from tests.fuzz import mutations
from tests.fuzz.generator import render
from tests.fuzz.oracle import LOUD, SAFE, SILENT, UNNAMED, classify
from tests.fuzz.run import build_case, choose_operators, run_case, run_case_scanned

#: Seeds the harness was measured on when this file was written. Fixed, so a
#: regression in the reader shows up as a new SILENT rather than a new seed.
SEEDS = tuple(range(50))

#: The clean family's LOUD count across SEEDS at the baseline commit. It must
#: not rise: a correct document read NEEDS_REVIEW is a false alarm.
CLEAN_LOUD_BASELINE = 0

#: A clean synthetic scan reads NEEDS_REVIEW at this base (R-27: the model's
#: count is not adopted). Every fixed seed is LOUD, none silent.
SCANNED_LOUD_BASELINE = 50


@pytest.fixture(scope="module")
def workdir(tmp_path_factory):
    return tmp_path_factory.mktemp("fuzz_smoke")


def _run(family: str, seed: int, workdir):
    names = () if family == "clean" else choose_operators(seed, max_ops=3)
    case = build_case(seed, names)
    result = run_case(case, workdir, f"{family}_{seed}")
    return case, classify(case, result)


def test_clean_family_reads_safe_and_holds_its_loud_baseline(workdir):
    loud = 0
    for seed in SEEDS:
        _case, outcome = _run("clean", seed, workdir)
        assert outcome.category != SILENT, (seed, outcome.defects, outcome.rules)
        if outcome.category == LOUD:
            loud += 1
    assert loud <= CLEAN_LOUD_BASELINE, f"clean LOUD rose to {loud}"


def test_hostile_family_has_no_silent_clean(workdir):
    categories = []
    for seed in SEEDS:
        _case, outcome = _run("hostile", seed, workdir)
        categories.append(outcome.category)
        assert outcome.category != SILENT, (seed, outcome.defects, outcome.rules)
        assert outcome.category != UNNAMED, (seed, outcome.defects, outcome.rules)
    # The oracle is not vacuous: with hazards applied it must reject some cases.
    assert any(category != SAFE for category in categories)


def _page_texts(path) -> list[str]:
    with pymupdf.open(path) as document:
        return [page.get_text() for page in document]


def test_a_case_is_reproducible_from_seed_and_operators(workdir):
    names = choose_operators(7, max_ops=3)
    assert names == choose_operators(7, max_ops=3)
    first = build_case(7, names)
    second = build_case(7, names)
    # The PDF's document id is not stable across saves, so determinism is
    # asserted on what was printed, which is what the reader sees.
    assert _page_texts(render(first, workdir / "first.pdf")) == _page_texts(
        render(second, workdir / "second.pdf")
    )
    assert classify(first, run_case(first, workdir, "first")) == classify(
        second, run_case(second, workdir, "second")
    )


def test_scanned_family_has_no_silent_clean(workdir):
    """Replayed scans must never read CLEAN while a claim is unaccounted for."""
    loud = 0
    for seed in SEEDS:
        case = build_case(seed, ())
        result = run_case_scanned(case, workdir, f"scanned_{seed}")
        outcome = classify(case, result)
        assert outcome.category != SILENT, (seed, outcome.defects, outcome.rules)
        assert outcome.category != UNNAMED, (seed, outcome.defects, outcome.rules)
        loud += outcome.category == LOUD
    assert loud <= SCANNED_LOUD_BASELINE, f"scanned LOUD rose to {loud}"


def test_operators_are_known_and_mutations_import():
    assert mutations.ALL_OPERATOR_NAMES
    assert set(mutations.OPERATORS) == set(mutations.ALL_OPERATOR_NAMES)


def test_shrinker_keeps_only_the_operators_the_failure_needs():
    from tests.fuzz.shrink import shrink

    build = lambda names: names  # noqa: E731 - a local stand-in for the builder
    kept = shrink(("a", "keep", "b", "keep2"), build,
                  lambda ops: "keep" in ops and "keep2" in ops)
    assert kept == ("keep", "keep2")


def test_oracle_calls_clean_with_a_missing_claim_silent():
    """The canary: the oracle must be able to see a silent failure at all."""
    import types
    from decimal import Decimal

    from core.schema import DocumentStatus, LossRunDocument, ReconciliationResult

    from tests.fuzz.generator import Case, ClaimGT, PageSpec

    printed = ClaimGT(number="71004410", run=0, page=1,
                      paid=Decimal("10"), reserve=Decimal("0"), incurred=Decimal("10"))
    case = Case(pages=[PageSpec(run=0, rows=[printed.row()])], claims=[printed], run_count=1)
    empty = LossRunDocument(source_filename="synthetic.pdf", file_sha256="0" * 64)
    result = types.SimpleNamespace(
        document=empty,
        reconciliation=ReconciliationResult(status=DocumentStatus.CLEAN),
    )
    outcome = classify(case, result)
    assert outcome.category == SILENT
    assert outcome.defects and outcome.defects[0].kind == "missing"
