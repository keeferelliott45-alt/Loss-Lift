"""Red-team tests: staged-source-file lifecycle security & retention.

These tests assert security/retention properties of the ingest/discard and
evidence lifecycle, not implementation details. They are expected to FAIL on
the base checkout: the point is to be red.

Synthetic data only: every staged file is a generated PDF that opens.
"""

import os
import pathlib
import tempfile

import pytest

from core.evidence import Evidence, EvidenceKind, claim_evidence, confirm_region, render_evidence
from core.ingest import IngestedFile, discard, ingest
from core.schema import Claim, SourceMethod
from tests.pdf_fixtures import synthetic_pdf

PDF = synthetic_pdf("attack lifecycle")

OUTCOMES = {"deleted", "already_gone", "refused", "failed"}


def _outcome(result) -> str:
    return str(result)


def _staged_file(path, owns_directory=False) -> IngestedFile:
    return IngestedFile(
        document_id="attack-probe",
        source_filename=path.name,
        sha256="attack-probe",
        path=path,
        size_bytes=path.stat().st_size,
        owns_directory=owns_directory,
    )


def test_discard_idempotent_with_verified_outcome():
    f = ingest(PDF, "lifecycle.pdf")
    assert f.path.exists()
    try:
        first = _outcome(discard(f))
        assert first in OUTCOMES, f"discard must report a verified outcome, got {first!r}"
        assert not f.path.exists(), "successful discard must remove the staged file"
        second = _outcome(discard(f))
        assert second == "already_gone", f"second discard must report already_gone, got {second!r}"
    finally:
        if f.path.exists():
            discard(f)


def test_discard_refuses_path_outside_tempdir():
    outside = pathlib.Path.cwd() / "_attack_outside"
    outside.mkdir(exist_ok=True)
    p = outside / "staged.pdf"
    p.write_bytes(PDF)
    try:
        assert tempfile.gettempdir() not in str(p.parent), "test premise: path must be outside tempdir"
        f = _staged_file(p)
        result = _outcome(discard(f))
        assert result == "refused", f"path outside tempdir must be refused, got {result!r}"
        assert p.exists(), "refused path must be left on disk"
    finally:
        p.unlink(missing_ok=True)
        outside.rmdir()


def test_discard_refuses_dir_without_ownership_marker():
    td = pathlib.Path(tempfile.gettempdir()) / "attack-no-marker"
    td.mkdir(exist_ok=True)
    p = td / "s.pdf"
    p.write_bytes(PDF)
    try:
        f = _staged_file(p)
        result = _outcome(discard(f))
        assert result == "refused", f"directory without ownership marker must be refused, got {result!r}"
        assert p.exists(), "refused path must be left on disk"
    finally:
        p.unlink(missing_ok=True)
        td.rmdir()


def test_discard_refuses_symlink_and_keeps_target(tmp_path):
    target = tmp_path / "target.pdf"
    target.write_bytes(PDF)
    link = tmp_path / "link.pdf"
    try:
        os.symlink(target, link)
    except OSError as exc:
        pytest.skip(f"cannot create a symlink on this platform: {exc}")
    f = _staged_file(link)
    result = _outcome(discard(f))
    assert result == "refused", f"a symlink at the staged path must be refused, got {result!r}"
    assert target.exists(), "refusing a symlink must leave the target on disk"


def test_ingest_then_discard_removes_owned_directory():
    f = ingest(PDF, "owned.pdf")
    parent = f.path.parent
    assert parent.name.startswith("losslift-")
    assert parent.exists()
    try:
        discard(f)
        assert not f.path.exists(), "discard must remove the staged file"
        assert not parent.exists(), "discard must remove the owned losslift-* directory"
    finally:
        if parent.exists():
            for leftover in parent.iterdir():
                leftover.unlink(missing_ok=True)
            parent.rmdir()


def test_independent_ingests_discard_without_cross_deletion():
    a = ingest(PDF, "a.pdf")
    b = ingest(PDF, "b.pdf")
    assert a.path != b.path
    try:
        discard(a)
        assert not a.path.exists()
        assert b.path.exists(), "discarding one staged copy must not delete the other"
        discard(b)
        assert not b.path.exists()
    finally:
        for f in (a, b):
            if f.path.exists():
                discard(f)
            if f.path.parent.exists():
                for leftover in f.path.parent.iterdir():
                    leftover.unlink(missing_ok=True)
                f.path.parent.rmdir()


def test_render_evidence_after_delete_returns_none():
    f = ingest(PDF, "ev.pdf")
    evidence = claim_evidence(Claim(claim_number="A1"), "paid_total")
    discard(f)
    assert not f.path.exists()
    assert render_evidence(f.path, evidence) is None


def test_confirm_region_after_delete_degrades_to_page():
    f = ingest(PDF, "ev2.pdf")
    evidence = Evidence(
        kind=EvidenceKind.REGION,
        method=SourceMethod.DIGITAL,
        page=1,
        bbox=(10, 10, 50, 50),
        note="attack region",
    )
    discard(f)
    assert not f.path.exists()
    result = confirm_region(f.path, evidence, "whatever")
    assert result.kind is not EvidenceKind.REGION, "deleted file must not yield a REGION claim"
    assert result.kind is EvidenceKind.PAGE, "deleted file must degrade to PAGE, not raise"