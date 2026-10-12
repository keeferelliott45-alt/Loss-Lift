"""Score the current committed checkout against private, verified truth.

The runner reads real documents, so it keeps to the corpus gate's rules:

* It evaluates the checkout it is run from, and only when that checkout is
  clean: a score belongs to a commit, never to uncommitted edits.
* The corpus, manifest, truth, recordings and output all live outside the
  repository.
* Every document is copied into a private snapshot, hashed as it is copied,
  and read from there. A document whose bytes differ from the manifest (and
  so from the truth written for it) is reported ``bytes_changed``, not scored.
* No live model is ever called: credentials are hidden for the run, column
  mapping by model is off, and scanned pages are read only from recordings.
  Without recordings they stay unread and are reported as such.
* Profiles and temporary files go to a private directory removed afterwards.
* The report carries counts, rates, fixed categories, the commit and sealed
  document ids. It is checked for every private string the run touched
  before it is written.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

from tools.corpus_gate import manifest as manifests
from tools.corpus_gate.manifest import SetupError
from tools.qualification import public
from tools.qualification.cases import offline, vision_arguments
from tools.qualification.score import QualificationMetrics, combine, score_result
from tools.qualification.truth import (
    DocumentTruth,
    LabelState,
    TruthSet,
    check_against_manifest,
)
from tools.qualification.truth import load as load_truth

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Checkout:
    commit: str
    clean: bool


def checkout_state(root: Path = REPO_ROOT) -> Checkout:
    """The commit this code is running from, and whether it has local edits."""
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                              text=True).stdout
    try:
        commit = git("rev-parse", "HEAD").strip()
        dirty = git("status", "--porcelain", "--untracked-files=normal").strip()
    except (OSError, subprocess.CalledProcessError):
        raise SetupError("the checkout's commit cannot be read") from None
    return Checkout(commit, not dirty)


def _isolated_core() -> bool:
    import core

    location = Path(getattr(core, "__file__", "") or "").resolve()
    return location.is_relative_to(REPO_ROOT)


@contextmanager
def _private_temp(root: Path) -> Iterator[None]:
    """Route every temporary file the pipeline makes into ``root``."""
    saved = tempfile.tempdir
    tempfile.tempdir = str(root)
    try:
        yield
    finally:
        tempfile.tempdir = saved


def _snapshot(source: Path, target: Path) -> str | None:
    """Copy while hashing; the SHA-256 of what was copied, None if unreadable."""
    digest = hashlib.sha256()
    try:
        with source.open("rb") as reader, target.open("xb") as writer:
            for block in iter(lambda: reader.read(1 << 20), b""):
                writer.write(block)
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


def private_strings(truth: DocumentTruth, entry: Any, paths: list[Path]) -> set[str]:
    """Everything from this document that must never appear in the report."""
    # The id and the document's own hash name the file to whoever holds the
    # manifest; the report names it only by its sealed id.
    values: set[str] = {entry.path, Path(entry.path).name, truth.document_id, truth.sha256}
    values.update(str(path) for path in paths)
    for claim in truth.claims:
        if claim.claim_number:
            values.add(claim.claim_number)
        for label in claim.fields.values():
            if label.state is not LabelState.KNOWN:
                continue
            if isinstance(label.value, Decimal):
                # An amount is searched for as printed with its decimals, and
                # only from 1 up: "0.50" would match inside any rate.
                if abs(label.value) >= 1 and "." in str(label.value):
                    values.add(str(label.value))
            else:
                values.add(str(label.value))
    return {value for value in values if _searchable(value)}


def _searchable(value: str) -> bool:
    """Whether a private string is distinctive enough to search a report for.

    A few digits turn up in any hash or count by chance, so short numbers are
    skipped; the report is built from counts and sealed ids, and this search
    is the second line of defence, not the first.
    """
    if not value or len(value) < 3:
        return False
    return not (value.isdigit() and len(value) < 6)


def _error_type(error: BaseException) -> str:
    return type(error).__name__


def run(
    *,
    corpus: Path,
    manifest_path: Path,
    truth_path: Path,
    out: Path,
    vision_replay: Path | None = None,
    checkout: Checkout | None = None,
) -> dict[str, Any]:
    """Score every truth document; write ``qualification.json`` and ``.txt``."""
    state = checkout or checkout_state()
    if not state.clean:
        raise SetupError("the checkout has uncommitted changes; the runner scores "
                         "committed code only")
    if not _isolated_core():
        raise SetupError("core is not imported from this checkout")
    for path, what in ((corpus, "corpus"), (manifest_path, "manifest"),
                       (truth_path, "truth file"), (out, "output directory"),
                       *(((vision_replay, "vision recordings"),) if vision_replay else ())):
        manifests.ensure_outside(Path(path), REPO_ROOT, what)
    manifest = manifests.load(Path(manifest_path))
    truth: TruthSet = load_truth(Path(truth_path))
    check_against_manifest(truth, manifest.entries)
    truth_sha = hashlib.sha256(Path(truth_path).read_bytes()).hexdigest()
    by_id = {entry.id: entry for entry in manifest.entries}

    records: list[dict[str, Any]] = []
    scored: list[QualificationMetrics] = []
    private: set[str] = {str(Path(corpus)), str(Path(manifest_path)), str(Path(truth_path)),
                         str(Path(out))}
    work = Path(tempfile.mkdtemp(prefix="losslift-qualification-"))
    try:
        from core.pipeline import run_pipeline

        snapshots = work / "snapshots"
        snapshots.mkdir()
        temp = work / "temp"
        temp.mkdir()
        vision = vision_arguments(vision_replay, None)
        for position, document in enumerate(truth.documents, start=1):
            entry = by_id[document.document_id]
            source = manifests.locate(entry, Path(corpus))
            private |= private_strings(document, entry, [source])
            sealed = public.seal(document.document_id, manifest.salt)
            copy = snapshots / f"{position:06d}.pdf"
            digest = _snapshot(source, copy) if source.is_file() else None
            if digest is None:
                records.append(public.document_record(
                    sealed, "missing_file", qualified=document.qualifies))
                continue
            if digest != document.sha256:
                records.append(public.document_record(
                    sealed, "bytes_changed", qualified=document.qualifies))
                continue
            profiles = work / "profiles" / f"{position:06d}"
            profiles.mkdir(parents=True)
            try:
                with offline(), _private_temp(temp):
                    result = run_pipeline(copy, use_llm=False, profiles_dir=profiles, **vision)
            except Exception as error:  # noqa: BLE001 - a failure is a category
                record = public.document_record(sealed, "pipeline_failed",
                                                qualified=document.qualifies)
                record["error_type"] = _error_type(error)
                records.append(record)
                continue
            metrics = score_result(result, document)
            scored.append(metrics)
            records.append(public.document_record(
                sealed, "scored", qualified=document.qualifies, metrics=metrics.as_dict()))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    report = public.build_report(
        commit=state.commit, manifest_sha256=manifest.sha256, truth_sha256=truth_sha,
        records=records, totals=combine(scored).as_dict(),
        vision_replay=vision_replay is not None)
    public.check_public(report, private)
    target = Path(out)
    target.mkdir(parents=True, exist_ok=True)
    (target / "qualification.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (target / "qualification.txt").write_text(public.render(report), encoding="utf-8")
    return report


def validate(*, manifest_path: Path, truth_path: Path, corpus: Path | None = None
             ) -> dict[str, int]:
    """Check a truth file against its manifest, and the corpus bytes if given."""
    manifests.ensure_outside(Path(manifest_path), REPO_ROOT, "manifest")
    manifests.ensure_outside(Path(truth_path), REPO_ROOT, "truth file")
    manifest = manifests.load(Path(manifest_path))
    truth = load_truth(Path(truth_path))
    check_against_manifest(truth, manifest.entries)
    changed = 0
    if corpus is not None:
        manifests.ensure_outside(Path(corpus), REPO_ROOT, "corpus")
        by_id = {entry.id: entry for entry in manifest.entries}
        for document in truth.documents:
            path = manifests.locate(by_id[document.document_id], Path(corpus))
            if not path.is_file() or manifests.file_sha256(path) != document.sha256:
                changed += 1
    return {
        "documents": len(truth.documents),
        "adjudicated": sum(1 for d in truth.documents if d.qualifies),
        "claim_occurrences": sum(len(d.claims) for d in truth.documents),
        "bytes_changed_or_missing": changed,
    }
