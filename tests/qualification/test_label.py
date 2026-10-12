"""The labelling helper: a blank skeleton, a review sheet, and a sign-off.

Truth must stay independent of what it measures, so the helper never runs
LossLift: the skeleton holds only what the bytes say (hash, page count), the
review sheet shows the page beside whatever the person has labelled, and only
complete truth can be signed off as adjudicated. Everything it writes holds
real document content, so all of it must live outside the repository.

Synthetic PDFs only.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from tests.qualification.synthetic import document_spec, loss_run_pdf, rows
from tools.corpus_gate import manifest as manifests
from tools.corpus_gate.manifest import SetupError
from tools.qualification import __main__ as cli
from tools.qualification import label, runner
from tools.qualification.truth import TruthError
from tools.qualification.truth import load as load_truth

ROWS = rows(3, start=55501000, big=None)
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def private(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "client losses.pdf").write_bytes(loss_run_pdf(rows=ROWS))
    manifest = manifests.create(corpus, tmp_path / "manifest.json")
    return corpus, tmp_path / "manifest.json", manifest.entries[0], tmp_path


def _skeleton(private):
    corpus, manifest, _entry, tmp = private
    out = tmp / "truth.json"
    label.skeleton(manifest_path=manifest, corpus=corpus, out=out)
    return out


# --------------------------------------------------------------------------
# Skeleton
# --------------------------------------------------------------------------


def test_the_skeleton_holds_only_what_the_bytes_say(private):
    _corpus, _manifest, entry, _tmp = private
    document = json.loads(_skeleton(private).read_text())["documents"][0]
    assert document["id"] == entry.id and document["sha256"] == entry.sha256
    assert document["page_count"] == 1
    assert document["adjudication"] == "provisional"
    assert document["claims"] == [] and document["runs"] == []
    assert document["pages"] == [{"page": 1, "run": None, "role": label.TODO}]
    for key in ("format_family", "status"):
        assert document[key] == label.TODO
    assert document["printed"]["claim_count"] == label.TODO


def test_an_unfilled_skeleton_does_not_validate(private):
    corpus, manifest, _entry, _tmp = private
    truth = _skeleton(private)
    with pytest.raises(TruthError):
        runner.validate(manifest_path=manifest, truth_path=truth, corpus=corpus)


def test_the_skeleton_never_overwrites_truth(private):
    truth = _skeleton(private)
    truth.write_text("hand-written")
    with pytest.raises(SetupError):
        _skeleton(private)
    assert truth.read_text() == "hand-written"


def test_the_skeleton_refuses_changed_bytes(private):
    corpus, manifest, _entry, tmp = private
    (corpus / "client losses.pdf").write_bytes(loss_run_pdf(rows=rows(2)))
    with pytest.raises(SetupError):
        label.skeleton(manifest_path=manifest, corpus=corpus, out=tmp / "truth.json")


def test_nothing_is_written_inside_the_repository(private):
    corpus, manifest, _entry, _tmp = private
    with pytest.raises(SetupError):
        label.skeleton(manifest_path=manifest, corpus=corpus, out=REPO / "truth.json")
    assert not (REPO / "truth.json").exists()


# --------------------------------------------------------------------------
# Review sheet
# --------------------------------------------------------------------------


def _filled(private, *, adjudication="provisional"):
    _corpus, _manifest, entry, tmp = private
    spec = document_spec(ROWS, doc_id=entry.id, sha256=entry.sha256)
    spec["adjudication"] = adjudication
    path = tmp / "filled.json"
    path.write_text(json.dumps({"version": 1, "documents": [spec]}))
    return path


def test_the_review_sheet_shows_each_page_beside_its_labels(private):
    corpus, manifest, entry, tmp = private
    sheets = label.review(manifest_path=manifest, corpus=corpus,
                          truth_path=_filled(private), out_dir=tmp / "sheets")
    assert [s.name for s in sheets] == [f"{entry.id}.html"]
    page = sheets[0].read_text()
    assert page.count('src="data:image/png;base64,') == 1
    for number, *_rest in ROWS:
        assert number in page
    assert "complete" in page and "provisional" in page


def test_the_review_sheet_lists_what_is_still_to_label(private):
    corpus, manifest, _entry, tmp = private
    sheet = label.review(manifest_path=manifest, corpus=corpus,
                         truth_path=_skeleton(private), out_dir=tmp / "sheets")[0]
    page = sheet.read_text()
    for item in ("format_family", "status", "page 1 role", "printed claim_count"):
        assert item in page


def test_the_review_sheet_escapes_what_the_truth_holds(private):
    corpus, manifest, entry, tmp = private
    spec = document_spec(ROWS, doc_id=entry.id, sha256=entry.sha256)
    spec["claims"][0]["claim_number"] = "<script>x</script>"
    path = tmp / "hostile.json"
    path.write_text(json.dumps({"version": 1, "documents": [spec]}))
    page = label.review(manifest_path=manifest, corpus=corpus, truth_path=path,
                        out_dir=tmp / "sheets")[0].read_text()
    assert "<script>x</script>" not in page and "&lt;script&gt;" in page


def test_review_sheets_are_never_written_inside_the_repository(private):
    corpus, manifest, _entry, _tmp = private
    with pytest.raises(SetupError):
        label.review(manifest_path=manifest, corpus=corpus, truth_path=_filled(private),
                     out_dir=REPO / "sheets")


# --------------------------------------------------------------------------
# Sign-off
# --------------------------------------------------------------------------


def test_only_complete_truth_can_be_signed_off(private):
    _corpus, _manifest, entry, _tmp = private
    with pytest.raises(TruthError):
        label.sign_off(truth_path=_skeleton(private), document_id=entry.id)


def test_sign_off_marks_the_document_adjudicated_and_logs_it(private):
    _corpus, _manifest, entry, _tmp = private
    truth = _filled(private)
    label.sign_off(truth_path=truth, document_id=entry.id)
    assert load_truth(truth).documents[0].adjudication == "adjudicated"
    log = truth.with_name(truth.name + ".signoff.log").read_text()
    assert entry.id in log and entry.sha256 in log


def test_sign_off_names_an_existing_document(private):
    with pytest.raises(TruthError):
        label.sign_off(truth_path=_filled(private), document_id="doc-unknown")


# --------------------------------------------------------------------------
# Independence and the command line
# --------------------------------------------------------------------------


def test_the_helper_never_imports_the_pipeline_it_measures():
    tree = ast.parse(Path(label.__file__).read_text())
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imported |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                 for alias in node.names}
    assert not any(name and name.split(".")[0] == "core" for name in imported)


def test_the_command_line_writes_a_skeleton_and_a_sheet(private):
    corpus, manifest, entry, tmp = private
    truth = tmp / "cli-truth.json"
    assert cli.main(["skeleton", "--manifest", str(manifest), "--corpus", str(corpus),
                     "--out", str(truth)]) == 0
    assert cli.main(["review", "--manifest", str(manifest), "--corpus", str(corpus),
                     "--truth", str(truth), "--out", str(tmp / "sheets")]) == 0
    assert (tmp / "sheets" / f"{entry.id}.html").is_file()
    # An unfilled skeleton cannot be signed off.
    assert cli.main(["sign-off", "--truth", str(truth), "--document", entry.id]) == 2
