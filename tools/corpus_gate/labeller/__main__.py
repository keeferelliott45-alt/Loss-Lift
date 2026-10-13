"""Command line for the R1 labelling helper.

    py -m tools.corpus_gate.labeller init --corpus DIR --manifest FILE --out DIR
    py -m tools.corpus_gate.labeller import --corpus DIR --manifest FILE \
        --worksheets DIR --truth FILE
    py -m tools.corpus_gate.labeller measure --truth FILE --manifest FILE \
        [--labels FILE] --out DIR

Every path named above must live outside this repository: the corpus, the
manifest (it holds the digest salt), the worksheets and truth file (they
hold real extraction values and truth), and the measure report. Each
command refuses to write inside the working tree. See the package
docstring for what each command does, the privacy rules, and the
fail-closed rules.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tools.corpus_gate import compare as gate
from tools.corpus_gate import labels as corpus_labels
from tools.corpus_gate import manifest as manifests
from tools.corpus_gate import runner
from tools.corpus_gate.manifest import SetupError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="py -m tools.corpus_gate.labeller",
        description="Owner-side real-document truth adjudication (R1).",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="run the current pipeline and write worksheets")
    init.add_argument("--corpus", required=True, type=Path)
    init.add_argument("--manifest", required=True, type=Path)
    init.add_argument("--out", required=True, type=Path, help="worksheets directory")
    init.add_argument("--repo", type=Path, default=Path.cwd())
    init.add_argument("--force", action="store_true", help="replace existing worksheets")

    imp = commands.add_parser("import", help="read adjudicated worksheets into a truth file")
    imp.add_argument("--corpus", required=True, type=Path)
    imp.add_argument("--manifest", required=True, type=Path)
    imp.add_argument("--worksheets", required=True, type=Path)
    imp.add_argument("--truth", required=True, type=Path)
    imp.add_argument("--repo", type=Path, default=Path.cwd())

    measure = commands.add_parser("measure", help="compute accuracy from a truth file")
    measure.add_argument("--truth", required=True, type=Path)
    measure.add_argument("--manifest", required=True, type=Path)
    measure.add_argument("--labels", type=Path, default=None,
                         help="labels file (per-document carrier / template family)")
    measure.add_argument("--out", required=True, type=Path, help="report directory")
    measure.add_argument("--repo", type=Path, default=Path.cwd())
    return parser


def _init(args: argparse.Namespace) -> int:
    from core.pipeline import run_pipeline

    from tools.corpus_gate.labeller import worksheet as sheets

    try:
        root = runner.repo_root(args.repo)
        manifests.ensure_outside(args.corpus, root, "corpus directory")
        manifests.ensure_outside(args.manifest, root, "manifest")
        manifests.ensure_outside(args.out, root, "worksheets directory")
        manifest = manifests.load(args.manifest)
        verification = manifests.verify(manifest, args.corpus)
        if not verification.ok:
            raise SetupError(
                "the corpus does not match the manifest "
                f"({len(verification.missing)} missing, "
                f"{len(verification.mismatched)} changed)"
            )
        written, failed = [], []
        for entry in manifest.entries:
            try:
                result = run_pipeline(manifests.locate(entry, args.corpus), use_vision=False)
            except Exception:  # noqa: BLE001 - named by type only; the message may quote a path
                failed.append(entry.id)
                continue
            sheets.write_worksheet(args.out, entry.id, result, force=args.force)
            written.append(entry.id)
    except SetupError as error:
        print(f"init: {error}", file=sys.stderr)
        return gate.EXIT_SETUP
    print(
        f"init: {len(written)} worksheet(s) written, {len(failed)} document(s) failed "
        f"extraction{': ' + ', '.join(failed) if failed else ''}. "
        "Adjudicate the TRUTH column in Excel; a blank cell means not adjudicated."
    )
    if args.out is not None:
        print(f"worksheets written to {args.out}", file=sys.stderr)
    return gate.EXIT_EXECUTION if failed else gate.EXIT_PASS


def _import(args: argparse.Namespace) -> int:
    from tools.corpus_gate.labeller import adjudicated

    try:
        summary, _payload = adjudicated.import_worksheets(
            args.corpus,
            args.manifest,
            args.worksheets,
            args.truth,
            repo_root=runner.repo_root(args.repo),
        )
    except SetupError as error:
        print(f"import: {error}", file=sys.stderr)
        return gate.EXIT_SETUP
    without = (
        f"; no worksheet for {len(summary.documents_without_worksheet)} document(s)"
        if summary.documents_without_worksheet
        else ""
    )
    print(
        f"import: {summary.documents_read} worksheet(s) read, "
        f"{summary.adjudicated} field(s) adjudicated, "
        f"{summary.unadjudicated} left blank{without}. "
        "Keep the truth file out of Git: it holds real truth values."
    )
    print(f"truth written to {args.truth}", file=sys.stderr)
    return gate.EXIT_PASS


def _measure(args: argparse.Namespace) -> int:
    from tools.corpus_gate.labeller import accuracy, adjudicated

    try:
        root = runner.repo_root(args.repo)
        manifests.ensure_outside(args.manifest, root, "manifest")
        manifests.ensure_outside(args.out, root, "report directory")
        manifest = manifests.load(args.manifest)
        truth = adjudicated.load_truth(args.truth, manifest)
        labels = (
            corpus_labels.load(args.labels, manifest) if args.labels is not None else None
        )
        report = accuracy.measure_to_files(
            truth, manifest, labels, args.out, repo_root=root
        )
    except SetupError as error:
        print(f"measure: {error}", file=sys.stderr)
        return gate.EXIT_SETUP
    print(accuracy.publishable_summary(report))
    print("measure.json and measure.md written to " + str(args.out), file=sys.stderr)
    return gate.EXIT_PASS


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return {"init": _init, "import": _import, "measure": _measure}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
