"""Print the accuracy table (spec section 10).

    python -m tests.golden.report

Field-level accuracy is reported separately for money and non-money fields,
because money accuracy is the number that decides whether this product can be
charged for.
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from tests.accuracy import aggregate, score_all
from tests.golden.fixtures import DIGITAL_FIXTURES
from tests.golden.generate import build_all

MONEY_THRESHOLD = 0.995


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-dir", type=Path, default=None,
                        help="reuse already-rendered fixtures instead of building them")
    args = parser.parse_args()

    directory = args.pdf_dir or Path(tempfile.mkdtemp(prefix="losslift-golden-"))
    if args.pdf_dir is None:
        build_all(directory)

    names = [fixture.name for fixture in DIGITAL_FIXTURES]
    reports = score_all(directory, names)

    width = max(len(name) for name in names) + 2
    print(f"{'fixture':<{width}}{'money':>18}{'other':>18}{'rows':>10}")
    print("-" * (width + 46))
    for name in names:
        report = reports[name]
        print(
            f"{name:<{width}}"
            f"{report.money.accuracy:>10.2%} "
            f"{f'({report.money.correct}/{report.money.expected_non_null})':>7}"
            f"{report.other.accuracy:>10.2%} "
            f"{f'({report.other.correct}/{report.other.expected_non_null})':>7}"
            f"{f'{report.extracted_rows}/{report.expected_rows}':>10}"
        )

    total = aggregate(reports.values())
    print("-" * (width + 46))
    print(
        f"{'ALL':<{width}}"
        f"{total.money.accuracy:>10.2%} "
        f"{f'({total.money.correct}/{total.money.expected_non_null})':>7}"
        f"{total.other.accuracy:>10.2%} "
        f"{f'({total.other.correct}/{total.other.expected_non_null})':>7}"
        f"{f'{total.extracted_rows}/{total.expected_rows}':>10}"
    )
    print()
    print(f"nulls silently read as zero: {total.money.nulls_as_zeros}")
    print(f"money threshold ({MONEY_THRESHOLD:.1%}): "
          f"{'PASS' if total.money.accuracy >= MONEY_THRESHOLD else 'FAIL'}")

    for mismatch in total.all_mismatches[:20]:
        print(f"  {mismatch}")

    return 0 if total.money.accuracy >= MONEY_THRESHOLD else 1


if __name__ == "__main__":
    raise SystemExit(main())
