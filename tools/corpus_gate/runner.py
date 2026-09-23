"""Run the collector once per revision, each in a worktree of its own.

The checkout this is started from is never read by the revisions under test
and never written to. Each revision gets a detached ``git worktree`` of its
exact commit in a private temporary directory, and the collector runs there
with ``-P`` and ``PYTHONPATH`` set to that worktree alone, so ``import core``
can only mean that revision's code -- and the collector checks it did before
measuring anything.

Each collector also gets a private temporary directory of its own. The
pipeline copies every document it ingests into the system temporary
directory; pointing that at a directory the gate owns means every copy is
deleted with it, whatever happens.

Collector output streams are discarded, never shown and never stored: a
traceback can quote the cell that caused it. What a revision did is read only
from the measurements it wrote.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tools.corpus_gate import compare as comparison
from tools.corpus_gate import manifest as manifests
from tools.corpus_gate.manifest import SetupError

COLLECTOR = Path(__file__).with_name("collect.py")
DEFAULT_TIMEOUT = 3600.0


@dataclass
class GateRun:
    exit_code: int
    report: str
    result: dict
    out_dir: Path | None


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SetupError(f"git {args[0]} failed")
    return completed.stdout.strip()


def repo_root(start: Path) -> Path:
    try:
        return Path(_git(start, "rev-parse", "--show-toplevel"))
    except SetupError:
        raise SetupError(
            "not inside a git repository: run from the LossLift checkout, or pass --repo"
        ) from None


def resolve(repo: Path, revision: str) -> str:
    try:
        return _git(repo, "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}")
    except SetupError:
        raise SetupError(f"revision {revision!r} is not a commit in this repository") from None


def _remove_tree(path: Path) -> None:
    def retry(function, target, _info):  # read-only files on Windows
        os.chmod(target, stat.S_IWRITE)
        function(target)

    if not path.exists():
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)


def _launch(worktree: Path, documents: Path, out: Path, scratch: Path, salt: bytes):
    env = dict(os.environ)
    for key in ("PYTHONSTARTUP", "PYTHONHOME", "PYTHONINSPECT"):
        env.pop(key, None)
    env.update(
        PYTHONPATH=str(worktree),
        PYTHONSAFEPATH="1",
        PYTHONHASHSEED="0",
        PYTHONDONTWRITEBYTECODE="1",
        TMPDIR=str(scratch),
        TEMP=str(scratch),
        TMP=str(scratch),
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-P",
            str(COLLECTOR),
            "--root",
            str(worktree),
            "--documents",
            str(documents),
            "--out",
            str(out),
        ],
        cwd=str(worktree),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert process.stdin is not None
    process.stdin.write(salt.hex() + "\n")
    process.stdin.close()
    return process


def _default_out(manifest_path: Path, baseline: str, candidate: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return manifest_path.resolve().parent / "gate-runs" / f"{stamp}-{baseline[:7]}-{candidate[:7]}"


def _write(target: Path, result: dict, report: str) -> None:
    target.mkdir(parents=True, exist_ok=True)
    (target / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (target / "report.txt").write_text(report, encoding="utf-8")


def _setup_failure(message: str, verification: manifests.Verification | None = None) -> GateRun:
    lines = [
        "LossLift real-corpus gate: FAIL (exit 3: the corpus or setup cannot be trusted)",
        "",
        message,
    ]
    result: dict = {"gate": {"schema": 1, "verdict": "fail", "exit_code": 3, "problems": [message]}}
    if verification is not None:
        if verification.missing:
            lines.append(f"missing ({len(verification.missing)}): " + ", ".join(verification.missing))
        if verification.mismatched:
            lines.append(
                f"hash mismatch ({len(verification.mismatched)}): " + ", ".join(verification.mismatched)
            )
        lines.append("Nothing was run. Restore the corpus, or regenerate the manifest and review why.")
        result["corpus"] = {
            "missing": list(verification.missing),
            "mismatched": list(verification.mismatched),
            "unlisted": verification.unlisted,
        }
    return GateRun(comparison.EXIT_SETUP, "\n".join(lines) + "\n", result, None)


def run_gate(
    *,
    repo: Path,
    baseline: str,
    candidate: str,
    corpus: Path,
    manifest_path: Path,
    allowlist_path: Path | None = None,
    out_dir: Path | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> GateRun:
    """Verify the corpus, measure both revisions, compare, and write the result.

    A setup failure writes its result too when ``out_dir`` was given. Without
    one there is nowhere trustworthy to write it: the default location sits
    beside a manifest that may be the very thing that is missing.
    """
    try:
        if sys.version_info < (3, 11):
            raise SetupError("Python 3.11 or newer is required (the collector runs with -P)")
        root = repo_root(repo)
        manifests.ensure_outside(corpus, root, "corpus directory")
        manifests.ensure_outside(manifest_path, root, "manifest")
        manifest = manifests.load(manifest_path)
        verification = manifests.verify(manifest, corpus)
        if not verification.ok:
            failed = _setup_failure("The corpus does not match its manifest.", verification)
        else:
            failed = None
            allowlist = comparison.load_allowlist(allowlist_path)
            base_sha = resolve(root, baseline)
            cand_sha = resolve(root, candidate)
    except SetupError as error:
        failed = _setup_failure(str(error))
    if failed is not None:
        if out_dir is not None:
            _write(out_dir, failed.result, failed.report)
            failed.out_dir = out_dir
        return failed

    runs = {
        "baseline": comparison.RevisionRun("baseline", base_sha),
        "candidate": comparison.RevisionRun("candidate", cand_sha),
    }
    scratch = Path(tempfile.mkdtemp(prefix="losslift-gate-"))
    worktrees: list[Path] = []
    try:
        documents = scratch / "documents.json"
        documents.write_text(
            json.dumps(
                [
                    {"id": entry.id, "path": str(manifests.locate(entry, corpus).resolve())}
                    for entry in manifest.entries
                ]
            ),
            encoding="utf-8",
        )
        processes = {}
        for label, run in runs.items():
            tree = scratch / label
            _git(root, "worktree", "add", "--detach", str(tree), run.commit)
            worktrees.append(tree)
            private = scratch / f"{label}-tmp"
            private.mkdir()
            processes[label] = (
                _launch(tree, documents, scratch / f"{label}.jsonl", private, manifest.salt),
                time.monotonic(),
            )

        deadline = time.monotonic() + timeout
        for label, (process, started) in processes.items():
            run = runs[label]
            timed_out = False
            try:
                process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                timed_out = True
            run.seconds = time.monotonic() - started
            comparison.read_run(scratch / f"{label}.jsonl", run)
            if timed_out:
                run.process = f"timed out after {timeout:.0f} s"
            elif process.returncode != 0 and not run.fatal:
                # A fatal record already says what stopped it; anything else
                # died without saying, and the exit code is all there is.
                run.process = f"exited with code {process.returncode}"
    finally:
        for tree in worktrees:
            subprocess.run(
                ["git", "-C", str(root), "worktree", "remove", "--force", str(tree)],
                capture_output=True,
                check=False,
            )
        subprocess.run(["git", "-C", str(root), "worktree", "prune"], capture_output=True, check=False)
        _remove_tree(scratch)

    outcome = comparison.compare(manifest, runs["baseline"], runs["candidate"], allowlist)
    verified = len(manifest.entries)
    report = comparison.render(outcome, manifest, verified)
    if verification.unlisted:
        report += (
            f"\nNote: {verification.unlisted} PDF(s) under the corpus directory are not in the "
            "manifest and were not run.\nRegenerate the manifest to include them.\n"
        )
    result = comparison.to_json(outcome, manifest, verified, verification.unlisted)

    target = out_dir or _default_out(manifest_path, base_sha, cand_sha)
    _write(target, result, report)
    return GateRun(outcome.exit_code, report, result, target)
