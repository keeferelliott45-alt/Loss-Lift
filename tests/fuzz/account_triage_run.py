"""Command-line runner for the account and triage hazard packs.

    python -m tests.fuzz.account_triage_run --family triage --seed-start 10000 --count 400

The account family drives ``core.account`` directly from truth-shaped
logical runs; the triage family adds an appetite configuration and checks
``core.intelligence``'s summary against the same truth. Both are seeded and
deterministic, and neither renders a PDF, so a campaign of thousands of
seeds costs seconds. The pack is also reachable through the campaign entry
point, ``python -m tests.fuzz.run --family account`` / ``--family triage``.

Exit 0 when nothing was SILENT and nothing errored; exit 1 when any case was
SILENT; exit 2 when the run tallied only ERRORs (a broken or absent layer --
``core.intelligence`` missing, say -- must not silently pass). LOUD and
UNNAMED are reported but do not fail the run, matching the PDF families.
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


def _reset_counts() -> None:
    """Zero the module tally so repeated in-process runs do not accumulate."""
    for category in COUNTS:
        COUNTS[category] = 0


def run_family(family: str, seed_start: int, count: int, *, verbose: bool):
    for offset in range(count):
        seed = seed_start + offset
        scenario = None
        try:
            if family == "account":
                case = account_case(seed)
                outcome = classify_account(case, case.build())
            else:
                # The import lives inside ``triage_case``; guarding the call
                # turns a missing ``core.intelligence`` into an ERROR tally
                # rather than an uncaught traceback.
                case = triage_case(seed)
                from core import intelligence as _ti

                summary = _ti.triage_summary(case.account.build(), case.rules)
                outcome = classify_triage(case, summary)
            scenario = case.scenario
        except Exception as exc:  # noqa: BLE001 - the tally records it
            COUNTS["ERROR"] += 1
            print(f"ERROR seed {seed} ({scenario or family}): {exc!r}")
            continue
        COUNTS[outcome.category] += 1
        if verbose or outcome.category != "SAFE":
            print(f"{outcome.category} seed {seed} {scenario}: {outcome.describe()}")


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

    _reset_counts()
    families = ("account", "triage") if args.family == "both" else (args.family,)
    for family in families:
        run_family(family, args.seed_start, args.count, verbose=args.verbose)
        if budget is not None and time.monotonic() > budget:
            print(f"budget {args.budget} reached; stopping after family {family}")
            break

    print(json.dumps(COUNTS, indent=None))
    if COUNTS["SILENT"]:
        return 1
    if COUNTS["ERROR"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
