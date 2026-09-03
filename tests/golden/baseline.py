"""Per-carrier accuracy report, and the baseline the build is held to.

    python -m tests.golden.baseline             # print the report
    python -m tests.golden.baseline --update    # record it as the new baseline

The baseline is committed. A pull request that lowers any carrier's accuracy
fails the build; one that raises it is expected to refresh the file, so the
number being defended is always the best one reached so far.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from tests.accuracy import (
    BASELINE_PATH,
    by_carrier,
    carrier_table,
    load_baseline,
    regressions,
    score_all,
    write_baseline,
)
from tests.golden.fixtures import DIGITAL_FIXTURES
from tests.golden.generate import build_all


def carrier_reports(golden_dir: Path | None = None):
    """Score every digital fixture and group the results by carrier."""
    if golden_dir is None:
        golden_dir = Path(tempfile.mkdtemp(prefix="losslift-golden-"))
        build_all(golden_dir)
    names = [fixture.name for fixture in DIGITAL_FIXTURES]
    return by_carrier(score_all(golden_dir, names), DIGITAL_FIXTURES)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update", action="store_true",
        help="write the current numbers to the baseline file",
    )
    args = parser.parse_args()

    reports = carrier_reports()
    print(carrier_table(reports))

    if args.update:
        write_baseline(reports)
        print(f"\nbaseline written to {BASELINE_PATH}")
        return 0

    missing = sorted(set(reports) - set(load_baseline()))
    dropped = regressions(reports, load_baseline())
    if missing:
        print("\nno baseline recorded for: " + ", ".join(missing))
    for regression in dropped:
        print(f"REGRESSION  {regression}")
    return 1 if dropped else 0


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
