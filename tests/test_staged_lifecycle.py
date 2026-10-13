"""The staged source PDF: ownership, verified deletion, and the startup sweep.

Uploads are written to a temporary directory and the UI promises they are not
kept. That promise is only honest if deletion is verified and cannot be turned
against an arbitrary path. These tests cover the ownership rules, the
after-ingest failure cleanup, and the best-effort sweep.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from core.export import to_bytes
from core.ingest import (
    OWNER_MARKER_NAME,
    DiscardOutcome,
    discard,
    ingest,
    run_or_discard,
    stage_and_run,
    sweep_orphaned_staging,
)
from core.schema import Claim, LossRunDocument
from tests.pdf_fixtures import synthetic_pdf

PDF = synthetic_pdf("staged lifecycle")


def _marked_dir(root: Path, name: str, *, age_seconds: float = 0) -> Path:
    directory = root / name
    directory.mkdir()
    (directory / "staged.pdf").write_bytes(PDF)
    (directory / OWNER_MARKER_NAME).write_text('{"files": {}}', encoding="utf-8")
    if age_seconds:
        stamp = directory.stat().st_mtime - age_seconds
        os.utime(directory, (stamp, stamp))
    return directory


def test_ingest_writes_a_marker_recording_the_hash():
    staged = ingest(PDF, "claims.pdf")
    try:
        marker = staged.path.parent / OWNER_MARKER_NAME
        assert marker.is_file()
        assert staged.sha256 in marker.read_text(encoding="utf-8")
    finally:
        discard(staged)


def test_marker_exists_before_the_staged_bytes_are_copied(tmp_path, monkeypatch):
    """A kill during the copy must not leave a staged file cleanup cannot see."""
    from core import ingest as ingest_module
    from core.ingest import ingest_path

    source = tmp_path / "source.pdf"
    source.write_bytes(PDF)
    observed: dict[str, str] = {}
    real_copy = ingest_module._copy_and_hash

    def watching_copy(handle, target):
        directory = Path(target.name).parent
        observed["marker"] = (directory / OWNER_MARKER_NAME).read_text(
            encoding="utf-8"
        )
        return real_copy(handle, target)

    monkeypatch.setattr(ingest_module, "_copy_and_hash", watching_copy)
    staged = ingest_path(source)
    try:
        assert observed.get("marker"), "no marker before the copy began"
        # Provisional entry first (empty digest), final entry once the copy is
        # done; either way the directory is marker-bearing and sweepable.
        assert '"sha256": ""' in observed["marker"]
        assert staged.sha256 in (
            staged.path.parent / OWNER_MARKER_NAME
        ).read_text(encoding="utf-8")
    finally:
        discard(staged)


def test_discard_outcomes_are_verified_and_idempotent():
    staged = ingest(PDF, "claims.pdf")
    first = discard(staged)
    assert first.outcome is DiscardOutcome.DELETED
    assert str(first) == "deleted"
    assert first.gone
    assert not staged.path.exists()

    second = discard(staged)
    assert second.outcome is DiscardOutcome.ALREADY_GONE
    assert second.gone
    assert not staged.path.exists()


def test_discard_refuses_a_path_outside_the_temp_directory(tmp_path, monkeypatch):
    outside = tmp_path / "not-a-losslift-dir"
    outside.mkdir()
    target = outside / "staged.pdf"
    target.write_bytes(PDF)
    # Pretend the system temp directory is somewhere else entirely.
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / "elsewhere"))

    from core.ingest import IngestedFile

    staged = IngestedFile(
        document_id="x", source_filename="staged.pdf", sha256="h",
        path=target, size_bytes=len(PDF), owns_directory=False,
    )
    result = discard(staged)
    assert result.outcome is DiscardOutcome.REFUSED
    assert target.exists()


def test_discard_refuses_a_directory_without_a_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    directory = tmp_path / "unmarked"
    directory.mkdir()
    target = directory / "staged.pdf"
    target.write_bytes(PDF)

    from core.ingest import IngestedFile

    staged = IngestedFile(
        document_id="x", source_filename="staged.pdf", sha256="h",
        path=target, size_bytes=len(PDF), owns_directory=False,
    )
    result = discard(staged)
    assert result.outcome is DiscardOutcome.REFUSED
    assert target.exists()


def test_stage_and_run_discards_the_staged_file_when_extraction_fails():
    seen: list[Path] = []

    def failing_run(staged):
        seen.append(staged.path)
        assert staged.path.exists()
        raise RuntimeError("synthetic extraction failure")

    with pytest.raises(RuntimeError, match="synthetic extraction failure"):
        stage_and_run(PDF, "claims.pdf", failing_run)

    assert seen and not seen[0].exists()
    assert not seen[0].parent.exists()


def test_stage_and_run_hands_ownership_back_on_success():
    def run(staged):
        return "result"

    staged, result = stage_and_run(PDF, "claims.pdf", run)
    try:
        assert result == "result"
        assert staged.path.exists()
    finally:
        discard(staged)


def test_run_or_discard_deletes_the_staged_file_on_a_failed_reread():
    staged = ingest(PDF, "claims.pdf")

    def failing_run(_staged):
        raise ValueError("synthetic mapping failure")

    with pytest.raises(ValueError, match="synthetic mapping failure"):
        run_or_discard(staged, failing_run)

    assert not staged.path.exists()


def test_export_degrades_without_crashing_when_the_source_is_deleted():
    staged = ingest(PDF, "claims.pdf")
    document = LossRunDocument(
        source_filename="claims.pdf",
        file_sha256=staged.sha256,
        claims=[Claim(claim_number="C1", source_page=1)],
    )
    assert to_bytes(document, None, source_path=staged.path)
    discard(staged)
    # The staged file is gone; the export still builds, with evidence marked
    # unconfirmed rather than raising.
    assert to_bytes(document, None, source_path=staged.path)


def test_sweep_removes_only_old_marked_staging(tmp_path):
    old = _marked_dir(tmp_path, "losslift-old", age_seconds=90_000)
    fresh = _marked_dir(tmp_path, "losslift-fresh")
    unmarked = tmp_path / "losslift-unmarked"
    unmarked.mkdir()
    (unmarked / "staged.pdf").write_bytes(PDF)
    other = _marked_dir(tmp_path, "not-a-staging-dir", age_seconds=90_000)

    removed = sweep_orphaned_staging(tmp_path)

    assert old in removed
    assert not old.exists()
    assert fresh.exists()
    assert unmarked.exists()
    assert other.exists()


def test_sweep_honours_a_custom_age(tmp_path):
    recent = _marked_dir(tmp_path, "losslift-recent", age_seconds=120)
    removed = sweep_orphaned_staging(tmp_path, max_age_hours=0.0001)
    assert recent in removed
