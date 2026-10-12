"""The demo pack behaves the way demo/README.md tells a prospect it will.

A live demo that contradicts its own runbook is worse than no demo, so each
document's status, run count and talking-point findings are pinned here, and
the PDFs must rebuild byte for byte. Synthetic documents only.
"""

from __future__ import annotations

from demo.build import DEMO, EMAIL_FILENAME, build, check


def test_every_demo_document_behaves_as_the_runbook_says(tmp_path):
    build(tmp_path)
    assert check(tmp_path) == []


def test_the_demo_pdfs_rebuild_byte_for_byte(tmp_path):
    first = build(tmp_path / "a")
    second = build(tmp_path / "b")
    pdfs = [item.filename for item in DEMO]
    assert {name: first[name] for name in pdfs} == {name: second[name] for name in pdfs}
    assert EMAIL_FILENAME in first
