"""Every registered pack, run through the current pipeline, against its ledger.

Each case declares what a correct reading is (its truth) and whether LossLift
should read it exactly (SUPPORTED) or must not accept it unreviewed (REVIEW).
``pack_ledger.json`` records, per case, whether the current pipeline meets
that and, if not, how it falls short -- by fixed category -- and the follow-up
that owns it. The ledger is an accounting, not an allowance: truth is never
changed to fit, no case is skipped, and the test fails when any outcome moves
in either direction, so a regression is caught and a fix must be recorded.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from tests.qualification.registry import PACKS
from tools.qualification.cases import build_all, evaluate_case

LEDGER = Path(__file__).with_name("pack_ledger.json")
PACK_DIR = Path(__file__).with_name("packs")


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory):
    root = tmp_path_factory.mktemp("packs")
    builders = [importlib.import_module(name).build_cases for name in PACKS]
    cases = build_all(builders, root / "cases")
    return {case.case_id: evaluate_case(case, root / "work") for case in cases}


@pytest.fixture(scope="module")
def ledger():
    return json.loads(LEDGER.read_text(encoding="utf-8"))["cases"]


def test_every_pack_on_disk_is_registered():
    on_disk = {path.name for path in PACK_DIR.iterdir()
               if path.is_dir() and (path / "__init__.py").exists()}
    assert {name.rsplit(".", 1)[1] for name in PACKS} == on_disk


def test_the_ledger_accounts_for_exactly_the_registered_cases(outcomes, ledger):
    assert set(ledger) == set(outcomes)


def test_every_case_meets_its_ledgered_outcome(outcomes, ledger):
    moved = {
        case_id: {"met": outcome.met, "shortfalls": sorted(outcome.shortfalls)}
        for case_id, outcome in outcomes.items()
        if (outcome.met, sorted(outcome.shortfalls))
        != (ledger[case_id]["met"], sorted(ledger[case_id]["shortfalls"]))
    }
    assert not moved, f"outcomes moved; fix the regression or record the fix: {moved}"


def test_every_unmet_case_names_its_follow_up(ledger):
    for case_id, entry in ledger.items():
        assert entry["met"] == (not entry["shortfalls"]), case_id
        if not entry["met"]:
            assert entry.get("followup"), case_id
