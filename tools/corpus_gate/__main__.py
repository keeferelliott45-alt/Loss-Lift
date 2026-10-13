"""Command line for the real-corpus gate.

    python -m tools.corpus_gate init-manifest --corpus DIR --manifest FILE
    python -m tools.corpus_gate run --baseline REV --candidate REV \\
        --corpus DIR --manifest FILE [--allowlist FILE] [--out DIR]

stdout carries the report and nothing else, so it can be forwarded as it
stands. Where the result was written goes to stderr: it is a local path.
"""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

from tools.corpus_gate import compare as comparison
from tools.corpus_gate import manifest as manifests
from tools.corpus_gate.collect import error_name
from tools.corpus_gate.manifest import SetupError
from tools.corpus_gate.runner import DEFAULT_TIMEOUT, repo_root, run_gate
from tools.corpus_gate.sandbox import DockerSandbox


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.corpus_gate",
        description="Compare LossLift's behavior on the local real corpus between two commits.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init-manifest", help="record the corpus's documents and hashes")
    init.add_argument("--corpus", required=True, type=Path)
    init.add_argument("--manifest", required=True, type=Path)
    init.add_argument("--repo", type=Path, default=Path.cwd())
    init.add_argument("--force", action="store_true", help="replace an existing manifest")

    run = commands.add_parser("run", help="measure both revisions and compare them")
    run.add_argument("--baseline", required=True, help="commit to compare against")
    run.add_argument("--candidate", required=True, help="commit under review")
    run.add_argument("--corpus", required=True, type=Path)
    run.add_argument("--manifest", required=True, type=Path)
    run.add_argument("--allowlist", type=Path, default=None)
    run.add_argument("--out", type=Path, default=None)
    run.add_argument("--repo", type=Path, default=Path.cwd())
    run.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="seconds per run")
    run.add_argument(
        "--public-output",
        action="store_true",
        help="write only the publishable result: states, changed fields, claim count and status",
    )
    run.add_argument(
        "--vision-replay",
        type=Path,
        default=None,
        help="replay recorded vision answers from this directory (outside the repository) "
             "so scanned pages are measured too; no live model is ever called",
    )
    run.add_argument(
        "--sandbox-image",
        default=None,
        help="run each collector in this local container image, with no network and no credentials",
    )

    labels = commands.add_parser(
        "init-labels",
        help="write a labels file listing every manifest document with every label unknown",
    )
    labels.add_argument("--manifest", required=True, type=Path)
    labels.add_argument("--labels", required=True, type=Path)
    labels.add_argument("--repo", type=Path, default=Path.cwd())
    labels.add_argument("--force", action="store_true", help="replace an existing labels file")
    return parser


def _init_labels(args: argparse.Namespace) -> int:
    from tools.corpus_gate import labels as corpus_labels

    try:
        root = repo_root(args.repo)
        manifests.ensure_outside(args.manifest, root, "manifest")
        manifests.ensure_outside(args.labels, root, "labels file")
        manifest = manifests.load(args.manifest)
        corpus_labels.write_template(manifest, args.labels, force=args.force)
    except SetupError as error:
        print(f"init-labels: {error}", file=sys.stderr)
        return comparison.EXIT_SETUP
    print(f"Listed {len(manifest.entries)} document(s) with every label unknown. "
          "Keep the labels file out of Git: it names real carriers.")
    return comparison.EXIT_PASS


def _init(args: argparse.Namespace) -> int:
    try:
        root = repo_root(args.repo)
        manifests.ensure_outside(args.corpus, root, "corpus directory")
        manifests.ensure_outside(args.manifest, root, "manifest")
        manifest = manifests.create(args.corpus, args.manifest, force=args.force)
    except SetupError as error:
        print(f"init-manifest: {error}", file=sys.stderr)
        return comparison.EXIT_SETUP
    print(f"Recorded {len(manifest.entries)} document(s). Keep the manifest out of Git: it holds the digest salt.")
    return comparison.EXIT_PASS


def _interrupted(signum, _frame):
    # A cancelled CI job is sent SIGTERM. Raising here runs every ``finally``
    # in the runner: collectors and their containers are stopped and every
    # worktree and copy is removed, exactly as on Ctrl-C.
    raise KeyboardInterrupt


def _run(args: argparse.Namespace) -> int:
    signal.signal(signal.SIGTERM, _interrupted)
    sandbox = DockerSandbox(args.sandbox_image) if args.sandbox_image else None
    try:
        outcome = run_gate(
            repo=args.repo,
            baseline=args.baseline,
            candidate=args.candidate,
            corpus=args.corpus,
            manifest_path=args.manifest,
            allowlist_path=args.allowlist,
            out_dir=args.out,
            timeout=args.timeout,
            sandbox=sandbox,
            public=args.public_output,
            vision_replay=args.vision_replay,
        )
    except SetupError as error:
        print(f"LossLift real-corpus gate: FAIL (exit 3)\n\n{error}")
        return comparison.EXIT_SETUP
    except KeyboardInterrupt:
        print(
            "LossLift real-corpus gate: FAIL (exit 4: the run stopped before it finished)\n\n"
            "Interrupted. Nothing was compared."
        )
        return comparison.EXIT_EXECUTION
    except Exception as error:  # noqa: BLE001 - the run stopped; its collectors already have
        # Named by type alone, as a crash in a collector is: the message of an
        # error raised this far in can quote a corpus path.
        print(
            "LossLift real-corpus gate: FAIL (exit 4: the run stopped before it finished)\n\n"
            f"{error_name(error)}. Nothing was compared."
        )
        return comparison.EXIT_EXECUTION
    sys.stdout.write(outcome.report)
    if outcome.out_dir is not None:
        print(f"result.json and report.txt written to {outcome.out_dir}", file=sys.stderr)
    return outcome.exit_code


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "init-labels":
        return _init_labels(args)
    return _init(args) if args.command == "init-manifest" else _run(args)


if __name__ == "__main__":
    sys.exit(main())
