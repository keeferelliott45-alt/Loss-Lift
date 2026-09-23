"""Retention boundaries for files passed into the extraction pipeline."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from core import ingest as ingest_module
from core import pipeline as pipeline_module
from core.ingest import (
    IngestError,
    borrow_path,
    discard,
    ingest_path,
    sha256_file,
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


def test_path_input_is_borrowed_without_creating_a_temporary_copy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A caller-owned path must not be duplicated into ``losslift-*``."""
    source = _digital_pdf(tmp_path / "source.pdf")
    unexpected_stage = tmp_path / "losslift-unexpected"
    calls: list[str] = []

    def recording_mkdtemp(*, prefix: str) -> str:
        calls.append(prefix)
        unexpected_stage.mkdir()
        return str(unexpected_stage)

    monkeypatch.setattr(ingest_module.tempfile, "mkdtemp", recording_mkdtemp)

    result = run_pipeline(source, use_vision=False)

    assert calls == []
    assert not unexpected_stage.exists()
    assert result.source_path == source
    assert source.exists()


def test_borrowed_file_cannot_be_deleted_through_discard(tmp_path: Path) -> None:
    source = _digital_pdf(tmp_path / "caller-owned.pdf")
    borrowed = borrow_path(source)

    assert borrowed.path == source
    assert borrowed.temporary is False
    assert borrowed.size_bytes == source.stat().st_size
    assert borrowed.sha256 == sha256_file(source)

    discard(borrowed)

    assert source.exists()


@pytest.mark.parametrize(
    ("content", "message"),
    [(b"", "empty"), (b"not a PDF", "not a PDF")],
)
def test_borrowed_path_uses_upload_validation(
    tmp_path: Path,
    content: bytes,
    message: str,
) -> None:
    source = tmp_path / "invalid.pdf"
    source.write_bytes(content)

    with pytest.raises(IngestError, match=message):
        borrow_path(source)


def test_explicit_staged_upload_remains_available_after_pipeline(
    tmp_path: Path,
) -> None:
    source = _digital_pdf(tmp_path / "upload.pdf")
    staged = ingest_path(source, tmp_path / "staged")

    result = run_pipeline(staged, use_vision=False)

    assert staged.temporary is True
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


def test_path_failure_neither_copies_nor_deletes_caller_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _digital_pdf(tmp_path / "caller-owned.pdf")
    calls: list[str] = []

    def recording_mkdtemp(*, prefix: str) -> str:
        calls.append(prefix)
        return str(tmp_path / "unexpected-stage")

    def fail_classification(_path: Path):
        raise RuntimeError("synthetic classification failure")

    monkeypatch.setattr(ingest_module.tempfile, "mkdtemp", recording_mkdtemp)
    monkeypatch.setattr(pipeline_module, "classify_pdf", fail_classification)

    with pytest.raises(RuntimeError, match="synthetic classification failure"):
        run_pipeline(source, use_vision=False)

    assert calls == []
    assert source.exists()
