"""Retention boundaries for files passed into the extraction pipeline."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from core import ingest as ingest_module
from core import pipeline as pipeline_module
from core.ingest import IngestError, discard, ingest_path
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
