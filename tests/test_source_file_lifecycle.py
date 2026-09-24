"""Retention boundaries for files passed into the extraction pipeline."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from core import ingest as ingest_module
from core import pipeline as pipeline_module
from core.ingest import (
    MAX_UPLOAD_BYTES,
    IngestError,
    discard,
    ingest,
    ingest_path,
    verify_source_unchanged,
)
from core.pipeline import run_pipeline


def _digital_pdf(path: Path) -> Path:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "Loss run report with enough searchable text to use digital extraction. "
        "This source file belongs to the caller and must remain in place.",
    )
    document.save(path)
    document.close()
    return path


def test_path_input_snapshot_is_removed_after_pipeline(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A stable snapshot may be used, but must never be retained."""
    source = _digital_pdf(tmp_path / "source.pdf")
    stage = tmp_path / "losslift-stage"
    calls: list[str] = []

    def recording_mkdtemp(*, prefix: str) -> str:
        calls.append(prefix)
        stage.mkdir()
        return str(stage)

    monkeypatch.setattr(ingest_module.tempfile, "mkdtemp", recording_mkdtemp)

    result = run_pipeline(source, use_vision=False)

    assert calls == ["losslift-"]
    assert not stage.exists()
    assert result.source_path == source
    assert source.exists()


def test_explicit_staged_upload_remains_available_after_pipeline(
    tmp_path: Path,
) -> None:
    source = _digital_pdf(tmp_path / "upload.pdf")
    staged = ingest_path(source, tmp_path / "staged")

    result = run_pipeline(staged, use_vision=False)

    assert staged.exists
    assert result.source_path == staged.path
    discard(staged, remove_directory=False)
    assert not staged.exists


def test_explicit_staged_upload_remains_caller_owned_when_pipeline_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _digital_pdf(tmp_path / "upload.pdf")
    staged = ingest_path(source, tmp_path / "staged")

    def fail_classification(_path: Path):
        raise RuntimeError("synthetic classification failure")

    monkeypatch.setattr(pipeline_module, "classify_pdf", fail_classification)

    with pytest.raises(RuntimeError, match="synthetic classification failure"):
        run_pipeline(staged, use_vision=False)

    assert staged.exists
    discard(staged, remove_directory=False)


def test_path_failure_removes_snapshot_without_deleting_caller_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _digital_pdf(tmp_path / "caller-owned.pdf")
    stage = tmp_path / "losslift-stage"
    calls: list[str] = []

    def recording_mkdtemp(*, prefix: str) -> str:
        calls.append(prefix)
        stage.mkdir()
        return str(stage)

    def fail_classification(_path: Path):
        raise RuntimeError("synthetic classification failure")

    monkeypatch.setattr(ingest_module.tempfile, "mkdtemp", recording_mkdtemp)
    monkeypatch.setattr(pipeline_module, "classify_pdf", fail_classification)

    with pytest.raises(RuntimeError, match="synthetic classification failure"):
        run_pipeline(source, use_vision=False)

    assert calls == ["losslift-"]
    assert not stage.exists()
    assert source.exists()


def test_path_change_during_extraction_is_rejected_and_snapshot_is_removed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _digital_pdf(tmp_path / "caller-owned.pdf")
    original_bytes = source.read_bytes()
    replacement = _digital_pdf(tmp_path / "replacement.pdf").read_bytes()
    stage = tmp_path / "losslift-stage"
    observed_snapshot: list[bytes] = []

    def recording_mkdtemp(*, prefix: str) -> str:
        assert prefix == "losslift-"
        stage.mkdir()
        return str(stage)

    real_pipeline = pipeline_module._run_pipeline

    def replace_source(ingested, **kwargs):
        observed_snapshot.append(ingested.path.read_bytes())
        result = real_pipeline(ingested, **kwargs)
        source.write_bytes(replacement)
        return result

    monkeypatch.setattr(ingest_module.tempfile, "mkdtemp", recording_mkdtemp)
    monkeypatch.setattr(pipeline_module, "_run_pipeline", replace_source)

    with pytest.raises(IngestError, match="changed while it was being read"):
        run_pipeline(source, use_vision=False)

    assert observed_snapshot == [original_bytes]
    assert source.read_bytes() == replacement
    assert not stage.exists()


def test_atomic_replacement_during_final_verification_is_rejected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _digital_pdf(tmp_path / "caller-owned.pdf")
    replacement = _digital_pdf(tmp_path / "replacement.pdf")
    stage = tmp_path / "losslift-stage"
    ingested = ingest_path(source, stage)
    real_stat = Path.stat
    swapped = False

    def replace_before_final_path_stat(path: Path, *args, **kwargs):
        nonlocal swapped
        if path == source and not swapped:
            replacement.replace(source)
            swapped = True
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", replace_before_final_path_stat)

    with pytest.raises(IngestError, match="changed while it was being read"):
        verify_source_unchanged(ingested)

    assert swapped
    assert ingested.path.exists()
    discard(ingested, remove_directory=False)


def test_failed_upload_does_not_delete_an_earlier_staged_upload(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workdir = tmp_path / "shared-stage"
    data = b"%PDF-1.7 first upload remains owned"
    first = ingest(data, "claims.pdf", workdir)
    original = first.path.read_bytes()
    real_open = Path.open

    def fail_new_write(path: Path, mode: str = "r", *args, **kwargs):
        if "w" in mode or "x" in mode:
            raise OSError("synthetic file descriptor exhaustion")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_new_write)

    with pytest.raises(OSError, match="descriptor exhaustion"):
        ingest(data, "claims.pdf", workdir)

    assert first.path.exists()
    assert first.path.read_bytes() == original


def test_discard_does_not_remove_a_caller_owned_workdir(
    tmp_path: Path,
) -> None:
    workdir = tmp_path / "losslift-caller-owned"
    first = ingest(b"%PDF-1.7 first", "first.pdf", workdir)
    second = ingest(b"%PDF-1.7 second", "second.pdf", workdir)

    discard(first)

    assert workdir.exists()
    assert not first.path.exists()
    assert second.path.exists()


def test_snapshot_name_collision_preserves_the_existing_owner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _digital_pdf(tmp_path / "caller-owned.pdf")
    workdir = tmp_path / "shared-stage"
    workdir.mkdir()
    existing = workdir / ".snapshot-collision.pdf"
    original = b"%PDF-1.7 earlier owner"
    existing.write_bytes(original)

    class CollidingId:
        hex = "collision"

        def __str__(self) -> str:
            return "collision"

    monkeypatch.setattr(ingest_module, "uuid4", lambda: CollidingId())

    with pytest.raises(FileExistsError):
        ingest_path(source, workdir)

    assert existing.exists()
    assert existing.read_bytes() == original


@pytest.mark.parametrize("path_input", [False, True])
def test_empty_workdir_uses_and_owns_a_private_directory(
    tmp_path: Path,
    monkeypatch,
    path_input: bool,
) -> None:
    stage = tmp_path / f"losslift-private-{path_input}"

    def recording_mkdtemp(*, prefix: str) -> str:
        assert prefix == "losslift-"
        stage.mkdir()
        return str(stage)

    monkeypatch.setattr(ingest_module.tempfile, "mkdtemp", recording_mkdtemp)
    if path_input:
        source = _digital_pdf(tmp_path / "caller-owned.pdf")
        staged = ingest_path(source, "")
    else:
        staged = ingest(b"%PDF-1.7 upload", "upload.pdf", "")

    assert staged.owns_directory
    discard(staged)
    assert not stage.exists()


@pytest.mark.parametrize("path_input", [False, True])
def test_empty_workdir_failure_removes_its_private_directory(
    tmp_path: Path,
    monkeypatch,
    path_input: bool,
) -> None:
    stage = tmp_path / f"losslift-failed-{path_input}"

    def recording_mkdtemp(*, prefix: str) -> str:
        assert prefix == "losslift-"
        stage.mkdir()
        return str(stage)

    monkeypatch.setattr(ingest_module.tempfile, "mkdtemp", recording_mkdtemp)
    if path_input:
        source = _digital_pdf(tmp_path / "caller-owned.pdf")

        def fail_copy(_source, target) -> str:
            target.write(b"%PDF-partial private bytes")
            raise OSError("synthetic snapshot failure")

        monkeypatch.setattr(ingest_module, "_copy_and_hash", fail_copy)
        operation = lambda: ingest_path(source, "")
    else:
        real_open = Path.open

        def fail_upload_write(path: Path, mode: str = "r", *args, **kwargs):
            if "x" in mode:
                raise OSError("synthetic upload failure")
            return real_open(path, mode, *args, **kwargs)

        monkeypatch.setattr(Path, "open", fail_upload_write)
        operation = lambda: ingest(b"%PDF-1.7 upload", "upload.pdf", "")

    with pytest.raises(OSError, match="synthetic"):
        operation()

    assert not stage.exists()


def test_oversized_path_is_rejected_before_snapshot_or_full_read(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "oversized.pdf"
    with source.open("wb") as handle:
        handle.write(b"%PDF-")
        handle.truncate(MAX_UPLOAD_BYTES + 1)
    calls: list[str] = []

    def recording_mkdtemp(*, prefix: str) -> str:
        calls.append(prefix)
        return str(tmp_path / "losslift-unexpected")

    def forbid_read_bytes(_path: Path) -> bytes:
        raise AssertionError("oversized input was loaded into memory")

    monkeypatch.setattr(ingest_module.tempfile, "mkdtemp", recording_mkdtemp)
    monkeypatch.setattr(Path, "read_bytes", forbid_read_bytes)

    with pytest.raises(IngestError, match="limit"):
        run_pipeline(source, use_vision=False)

    assert calls == []


def test_failed_snapshot_write_removes_partial_document(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _digital_pdf(tmp_path / "caller-owned.pdf")
    stage = tmp_path / "losslift-stage"

    def recording_mkdtemp(*, prefix: str) -> str:
        assert prefix == "losslift-"
        stage.mkdir()
        return str(stage)

    def fail_copy(_source, target) -> str:
        target.write(b"%PDF-partial private bytes")
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(ingest_module.tempfile, "mkdtemp", recording_mkdtemp)
    monkeypatch.setattr(ingest_module, "_copy_and_hash", fail_copy)

    with pytest.raises(OSError, match="synthetic disk failure"):
        run_pipeline(source, use_vision=False)

    assert not stage.exists()
    assert source.exists()
