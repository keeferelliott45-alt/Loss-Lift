"""Command-line runner for the account and triage hazard packs.

    python -m tests.fuzz.account_triage_run --family triage --seed-start 10000 --count 400

The account family drives ``core.account`` directly from truth-shaped
logical runs; the triage family adds an appetite configuration and checks
``core.intelligence``'s summary against the same truth. Both are seeded and
deterministic, and neither renders a PDF, so a campaign of thousands of
seeds costs seconds. The pack is also reachable through the campaign entry
point, ``python -m tests.fuzz.run --family account`` / ``--family triage``.

Exit 0 when nothing was SILENT; exit 1 otherwise (LOUD and UNNAMED are
reported but do not fail the run, matching the PDF families).
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from tests.fuzz.account_triage import (
    account_case,
    classify_account,
    classify_triage,
    triage_case,
)

COUNTS = {"SAFE": 0, "SILENT": 0, "LOUD": 0, "UNNAMED": 0, "ERROR": 0}


def run_family(family: str, seed_start: int, count: int, *, verbose: bool):
    for offset in range(count):
        seed = seed_start + offset
        if family == "account":
            case = account_case(seed)
            try:
                outcome = classify_account(case, case.build())
            except Exception as exc:  # noqa: BLE001 - the tally records it
                COUNTS["ERROR"] += 1
                print(f"ERROR seed {seed} ({case.scenario}): {exc!r}")
                continue
            token = f"{case.scenario}"
        else:
            case = triage_case(seed)
            try:
                from core import intelligence as _ti

                summary = _ti.triage_summary(case.account.build(), case.rules)
                outcome = classify_triage(case, summary)
            except Exception as exc:  # noqa: BLE001 - the tally records it
                COUNTS["ERROR"] += 1
                print(f"ERROR seed {seed} ({case.scenario}): {exc!r}")
                continue
            token = f"{case.scenario}"
        COUNTS[outcome.category] += 1
        if verbose or outcome.category != "SAFE":
            print(f"{outcome.category} seed {seed} {token}: {outcome.describe()}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", choices=("account", "triage", "both"), default="both")
    parser.add_argument("--seed-start", type=int, default=0)
    parser.add_argument("--count", type=int, default=200,
                        help="seeds per family")
    parser.add_argument("--budget", default=None,
                        help="wall-clock cap, e.g. 10m; the run stops cleanly inside it")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    budget = None
    if args.budget:
        unit = args.budget[-1]
        seconds = float(args.budget[:-1]) * {"s": 1, "m": 60, "h": 3600}[unit]
        budget = time.monotonic() + seconds

    families = ("account", "triage") if args.family == "both" else (args.family,)
    for family in families:
        run_family(family, args.seed_start, args.count, verbose=args.verbose)
        if budget is not None and time.monotonic() > budget:
            print(f"budget {args.budget} reached; stopping after family {family}")
            break

    print(json.dumps(COUNTS, indent=None))
    return 1 if COUNTS["SILENT"] else 0


if __name__ == "__main__":
    sys.exit(main())
