"""Hazard pack for the account rollup: one insured's history across many runs.

The document fuzz harness (``tests/fuzz/``) asks whether one PDF can read
CLEAN while wrong. This pack asks the same of ``core.account``: whether an
account can read CLEAN while its merged history is wrong. Documents are built
directly as ``LossRunDocument`` objects with a known truth: every claim's
identity (carrier, policy, number), its valuation and whether a later run of
the same policy omits it. Each seed applies a random set of hazards:

* the same claim number at two carriers, or under two policies of one carrier;
* a run that leaves its carrier, policy number or term unprinted;
* a run with no valuation date; runs supplied out of valuation order;
* carrier names that differ only in case and spacing;
* a run that needs review; a packet binding two carriers' runs;
* a claim purged from a later run of its own policy, or absent from another
  carrier's run (which is not a purge).

Oracle, on every account whose status is CLEAN (anything else is SAFE):

* **merged**: two truth claims share one history;
* **split**: one truth claim spread over two histories (counted twice);
* **stale**: a history's current appearance is not its latest valuation;
* **untrusted**: a claim from a run that needs review;
* **dropped**: a purge not reported, or a non-purge reported.

Any of those on a CLEAN account is SILENT. The pass bar is zero SILENT.
``python -m tests.test_account_hazards --seeds N`` runs a campaign. The
test suite runs a fixed slice. Synthetic data only.
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import pytest

from core.account import build_accounts
from core.schema import (
    Claim,
    ClaimStatus,
    DocumentStatus,
    LogicalRun,
    LossRunDocument,
    ReconciliationResult,
    RunConfidence,
)

CARRIERS = ("ASHGROVE MUTUAL", "BLUE LEDGER CASUALTY", "CEDAR POINT INDEMNITY")
INSURED = "Ridgeway Test Freight LLC"
HAZARDS = (
    "shared_number_two_carriers", "shared_number_two_policies", "carrier_unprinted",
    "policy_unprinted", "term_unprinted", "valuation_unprinted", "shuffled_order",
    "carrier_spelling", "run_needs_review", "packet", "purged_later", "other_carrier_absent",
)


@dataclass(frozen=True)
class Truth:
    """One real claim: who owns it, and what it is worth at each valuation."""

    carrier: str
    policy: str
    number: str
    loss: date


@dataclass
class Run:
    carrier: str
    policy: str
    term: tuple[date, date]
    valuation: date
    claims: dict[Truth, Decimal]
    printed_carrier: str | None = None
    printed_policy: str | None = None
    printed_term: bool = True
    printed_valuation: bool = True
    clean: bool = True


@dataclass
class Scenario:
    seed: int
    hazards: tuple[str, ...]
    runs: list[Run] = field(default_factory=list)
    packets: list[tuple[int, int]] = field(default_factory=list)


def _claims(rng, carrier, policy, term, count, start):
    year = term[0].year
    return [Truth(carrier, policy, f"{start + n}", date(year, 1 + n % 12, 10 + n % 15))
            for n in range(count)]


def scenario(seed: int, max_hazards: int = 4) -> Scenario:
    rng = random.Random(seed)
    hazards = tuple(sorted(rng.sample(HAZARDS, rng.randint(1, max_hazards))))
    term = (date(2023, 1, 1), date(2023, 12, 31))
    out = Scenario(seed, hazards)
    a = _claims(rng, CARRIERS[0], "GL-100", term, rng.randint(2, 5), 7000)
    b_start = 7000 if "shared_number_two_carriers" in hazards else 9000
    b = _claims(rng, CARRIERS[1], "CA-200", term, rng.randint(1, 4), b_start)
    if "shared_number_two_policies" in hazards:
        b += _claims(rng, CARRIERS[0], "GL-101", term, 1, 7000)
    for valuation in (date(2023, 6, 30), date(2023, 12, 31)):
        later = valuation.month == 12
        for carrier, policy, truths in ((CARRIERS[0], "GL-100", a),
                                        (CARRIERS[1], "CA-200", [t for t in b if t.policy == "CA-200"]),
                                        (CARRIERS[0], "GL-101", [t for t in b if t.policy == "GL-101"])):
            if not truths:
                continue
            held = list(truths)
            if later and "purged_later" in hazards and carrier == CARRIERS[0] and policy == "GL-100":
                held = held[:-1]
            out.runs.append(Run(carrier, policy, term, valuation,
                                {t: Decimal(1000 * (i + 1) + (500 if later else 0))
                                 for i, t in enumerate(held)},
                                printed_carrier=carrier, printed_policy=policy))
    if "other_carrier_absent" in hazards:
        # A third carrier's run for the same insured, holding none of the others.
        c = _claims(rng, CARRIERS[2], "WC-300", term, 2, 5000)
        out.runs.append(Run(CARRIERS[2], "WC-300", term, date(2023, 12, 31),
                            {t: Decimal(700) for t in c}, printed_carrier=CARRIERS[2],
                            printed_policy="WC-300"))
    victim = rng.randrange(len(out.runs))
    run = out.runs[victim]
    if "carrier_unprinted" in hazards:
        run.printed_carrier = None
    if "policy_unprinted" in hazards:
        run.printed_policy = None
    if "term_unprinted" in hazards:
        run.printed_term = False
    if "valuation_unprinted" in hazards:
        # The engine never calls such a run clean (R-06 is an ERROR).
        run.printed_valuation = False
        run.clean = False
    if "carrier_spelling" in hazards:
        other = out.runs[(victim + 1) % len(out.runs)]
        if other.printed_carrier:
            other.printed_carrier = "  " + other.printed_carrier.lower() + " "
    if "run_needs_review" in hazards:
        out.runs[rng.randrange(len(out.runs))].clean = False
    if "packet" in hazards and len(out.runs) >= 2:
        out.packets.append((0, 1))
    if "shuffled_order" in hazards:
        rng.shuffle(out.runs)
        out.packets = []
    return out


def _claim(truth: Truth, incurred: Decimal, page: int) -> Claim:
    return Claim(claim_number=truth.number, date_of_loss=truth.loss,
                 claim_status=ClaimStatus.OPEN, paid_total=incurred, reserve_total=Decimal(0),
                 incurred_total=incurred, source_page=page)


def documents(s: Scenario):
    """The scenario as LossRunDocuments and their reconciliations."""
    docs, recs = [], {}
    packed = {index for pair in s.packets for index in pair}
    for index, run in enumerate(s.runs):
        if index in packed:
            continue
        doc = LossRunDocument(
            source_filename=f"run-{index}.pdf", file_sha256=f"{index:064x}",
            carrier=run.printed_carrier, named_insured=INSURED,
            policy_number=run.printed_policy,
            valuation_date=run.valuation if run.printed_valuation else None,
            policy_periods=[run.term] if run.printed_term else [],
            claims=[_claim(t, v, 1) for t, v in run.claims.items()],
        )
        docs.append(doc)
        recs[doc.document_id] = ReconciliationResult(
            status=DocumentStatus.CLEAN if run.clean else DocumentStatus.NEEDS_REVIEW)
    for first, second in s.packets:
        pair = (s.runs[first], s.runs[second])
        claims, logical, run_status = [], [], {}
        for page, run in enumerate(pair, start=1):
            claims += [_claim(t, v, page) for t, v in run.claims.items()]
            logical.append(LogicalRun(
                run_id=f"run-{page}", pages=[page], confidence=RunConfidence.PRINTED,
                carrier=run.printed_carrier, named_insured=INSURED,
                policy_number=run.printed_policy,
                valuation_date=run.valuation if run.printed_valuation else None,
                policy_period_start=run.term[0] if run.printed_term else None,
                policy_period_end=run.term[1] if run.printed_term else None,
            ))
            run_status[f"run-{page}"] = (DocumentStatus.CLEAN if run.clean
                                         else DocumentStatus.NEEDS_REVIEW)
        doc = LossRunDocument(source_filename=f"packet-{first}-{second}.pdf",
                              file_sha256=f"{first + 100:064x}", named_insured=INSURED,
                              page_count=2, claims=claims, runs=logical)
        docs.append(doc)
        recs[doc.document_id] = ReconciliationResult(
            status=(DocumentStatus.CLEAN if all(s == DocumentStatus.CLEAN for s in run_status.values())
                    else DocumentStatus.NEEDS_REVIEW), run_status=run_status)
    return docs, recs


def judge(s: Scenario) -> tuple[str, list[str]]:
    """SAFE, LOUD or SILENT, with the defects found on a CLEAN account.

    A claim reported as dropped that was not purged is LOUD: the account shows
    it to the reviewer, which costs a look and hides nothing."""
    docs, recs = documents(s)
    accounts = build_accounts(docs, recs)
    assert len(accounts) == 1, "every run names the same insured"
    account = accounts[0]
    if account.status is not DocumentStatus.CLEAN:
        return "SAFE", []
    defects: list[str] = []
    # Which truth each appearance is: claims are rebuilt per run, so match on
    # (run carrier, run policy, number) from the scenario.
    truth_of: dict[int, Truth] = {}
    for run in s.runs:
        for truth in run.claims:
            truth_of.setdefault(id(truth), truth)
    owners: dict[tuple[str, str], Truth] = {}
    for run in s.runs:
        for truth in run.claims:
            owners[(run.carrier, truth.number, str(run.valuation), truth.policy)] = truth
    seen: Counter[Truth] = Counter()
    for history in account.histories:
        truths = set()
        for appearance in history.appearances:
            matches = [t for (carrier, number, valuation, policy), t in owners.items()
                       if number == appearance.claim.claim_number
                       and (appearance.valuation_date is None or valuation == str(appearance.valuation_date))
                       and t.loss == appearance.claim.date_of_loss
                       and (appearance.carrier is None
                            or " ".join(appearance.carrier.upper().split()) == carrier)
                       and (appearance.policy_number is None or appearance.policy_number == policy)]
            truths.update(matches if len(matches) == 1 else [])
        if len(truths) > 1:
            defects.append(f"merged {sorted(t.number + '@' + t.carrier for t in truths)}")
        for truth in truths:
            seen[truth] += 1
        dated = [a.valuation_date for a in history.appearances if a.valuation_date]
        if dated and history.appearances[-1].valuation_date != max(dated):
            defects.append(f"stale {history.claim_number}")
        if not all(a.trusted for a in history.appearances):
            defects.append(f"untrusted {history.claim_number}")
    defects += [f"split {t.number}@{t.carrier}" for t, n in seen.items() if n > 1]
    purged = "purged_later" in s.hazards
    reported = {h.claim_number for h in account.dropped}
    if purged and not reported:
        defects.append("dropped: purge not reported")
    if defects:
        return "SILENT", defects
    if not purged and reported:
        return "LOUD", [f"dropped: {sorted(reported)} reported without a purge"]
    return "SAFE", defects


def campaign(start: int, count: int) -> tuple[Counter, list]:
    tally: Counter[str] = Counter()
    silent = []
    for seed in range(start, start + count):
        s = scenario(seed)
        verdict, defects = judge(s)
        tally[verdict] += 1
        if verdict == "SILENT":
            silent.append((seed, s.hazards, defects))
            continue
    return tally, silent


# --- the suite's slice ---------------------------------------------------------


def _pinned(hazards: tuple[str, ...], defects: list[str]) -> bool:
    """The two known classes, each pinned below by a strict xfail."""
    unreported = defects == ["dropped: purge not reported"] and "purged_later" in hazards
    return unreported and ("term_unprinted" in hazards
                           or "shared_number_two_carriers" in hazards)


def test_a_fixed_slice_has_no_silent_account_beyond_the_pinned_classes():
    tally, silent = campaign(0, 400)
    unknown = [case for case in silent if not _pinned(case[1], case[2])]
    assert not unknown, unknown[:5]
    assert tally["SAFE"] + tally["LOUD"] + len(silent) == 400


@pytest.mark.xfail(strict=True, reason=(
    "account finding (since main 98ed282, #58): _later_run_omits now treats the "
    "claim number appearing in ANY later covering run as presence, so another "
    "carrier's distinct claim with the same number hides a genuine purge"))
def test_a_purge_is_not_hidden_by_another_carriers_claim_with_the_same_number():
    s = scenario(2143)
    assert s.hazards == ("purged_later", "shared_number_two_carriers")
    verdict, defects = judge(s)
    assert verdict != "SILENT", defects


def test_the_same_purge_without_a_shared_number_is_reported():
    """The boundary: with the other carrier numbering differently, it is reported."""
    s = scenario(2143)
    for run in s.runs:
        if run.carrier != CARRIERS[0]:
            run.claims = {Truth(t.carrier, t.policy, "9" + t.number, t.loss): v
                          for t, v in run.claims.items()}
    assert judge(s)[0] == "SAFE"


@pytest.mark.xfail(strict=True, reason=(
    "account finding: a later run of the same carrier and policy number omits a "
    "claim, but prints no policy term; _later_run_omits needs _covers() to be "
    "true, so the omission is not reported and the account reads CLEAN"))
def test_a_claim_omitted_by_a_later_run_without_a_term_is_reported():
    s = scenario(995)
    assert s.hazards == ("purged_later", "term_unprinted")
    verdict, defects = judge(s)
    assert verdict != "SILENT", defects


def test_a_claim_omitted_by_a_later_run_with_its_term_is_reported():
    """The same purge with the term printed is reported: the boundary a fix keeps."""
    s = scenario(995)
    for run in s.runs:
        run.printed_term = True
    assert judge(s)[0] == "SAFE"


@pytest.mark.xfail(strict=True, reason=(
    "account finding, carried into triage: core.intelligence's T-05 dropped-claim "
    "flag reads account.dropped, so the omission above never reaches the "
    "underwriter either"))
def test_triage_flags_the_claim_a_later_run_without_a_term_omits():
    from core.intelligence import AppetiteRules, triage_summary

    docs, recs = documents(scenario(995))
    summary = triage_summary(build_accounts(docs, recs)[0], AppetiteRules())
    assert any(flag.rule.startswith("T-05") for flag in summary.flags)


def test_triage_flags_the_same_omission_when_the_term_is_printed():
    from core.intelligence import AppetiteRules, triage_summary

    s = scenario(995)
    for run in s.runs:
        run.printed_term = True
    docs, recs = documents(s)
    summary = triage_summary(build_accounts(docs, recs)[0], AppetiteRules())
    assert any(flag.rule.startswith("T-05") for flag in summary.flags)


def test_the_pack_exercises_clean_accounts_too():
    """An oracle that only ever sees NEEDS_REVIEW proves nothing."""
    clean = 0
    for seed in range(200):
        docs, recs = documents(scenario(seed))
        if build_accounts(docs, recs)[0].status is DocumentStatus.CLEAN:
            clean += 1
    assert clean >= 20


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="account rollup hazard campaign")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--seeds", type=int, default=2000)
    args = parser.parse_args(argv)
    tally, silent = campaign(args.start, args.seeds)
    print(f"account hazards seeds={args.start}..{args.start + args.seeds - 1} {dict(tally)}")
    for seed, hazards, defects in silent[:20]:
        print("SILENT", seed, hazards, defects)
    return 1 if silent else 0


if __name__ == "__main__":
    sys.exit(main())
