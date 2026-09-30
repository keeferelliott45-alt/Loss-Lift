"""python -m tools.qualification validate|run

  validate --manifest M --truth T [--corpus C]
      Check the truth file against the manifest (ids and hashes), and the
      corpus bytes when given. Exit 0 valid, 1 bytes changed or missing,
      2 unusable truth or setup.

  run --corpus C --manifest M --truth T --out O [--vision-replay R]
      Score the current clean committed checkout. Writes O/qualification.json
      and O/qualification.txt (counts, rates, sealed ids only). Exit 0 when
      every document was scored against adjudicated truth, 1 when the
      evidence is insufficient (a document not scored, a claim page unread,
      provisional truth), 2 for an unusable truth file or setup.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tools.corpus_gate.manifest import SetupError
from tools.qualification import public, runner
from tools.qualification.truth import TruthError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.qualification",
                                     description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("validate", help="check a truth file")
    check.add_argument("--manifest", required=True, type=Path)
    check.add_argument("--truth", required=True, type=Path)
    check.add_argument("--corpus", type=Path, default=None)
    score = commands.add_parser("run", help="score the committed checkout")
    score.add_argument("--corpus", required=True, type=Path)
    score.add_argument("--manifest", required=True, type=Path)
    score.add_argument("--truth", required=True, type=Path)
    score.add_argument("--out", required=True, type=Path)
    score.add_argument("--vision-replay", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            summary = runner.validate(manifest_path=args.manifest, truth_path=args.truth,
                                      corpus=args.corpus)
            print("truth valid: " + ", ".join(f"{k}={v}" for k, v in summary.items()))
            return 1 if summary["bytes_changed_or_missing"] else 0
        report = runner.run(corpus=args.corpus, manifest_path=args.manifest,
                            truth_path=args.truth, out=args.out,
                            vision_replay=args.vision_replay)
    except (TruthError, SetupError) as error:
        # Both are written to carry positions and field names only.
        print(f"error: {error}", file=sys.stderr)
        return 2
    except public.PrivateDataInReport as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    sys.stdout.write(public.render(report))
    return 0 if report["qualification"] == "measured" else 1


if __name__ == "__main__":
    sys.exit(main())
