"""The R1 labelling helper, end to end on synthetic documents only.

The worksheets, the truth file and the measure report are exercised through
the real pipeline (one golden fixture) and through the command's exit codes,
plus the accuracy math against hand-computed expectations on a tiny
synthetic truth file. The privacy test reads the committed package itself:
no absolute user paths, no owner staging area name, no embedded document
text from the golden fixtures.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
from pathlib import Path

import pytest

from tests.golden.fixtures import DIGITAL_FIXTURES
from tests.golden.generate import render
from tools.corpus_gate import labels as corpus_labels
from tools.corpus_gate import manifest as manifests
from tools.corpus_gate.manifest import Entry, Manifest
from tools.corpus_gate.labeller import accuracy
from tools.corpus_gate.labeller import worksheet as sheets
from tools.corpus_gate.labeller.__main__ import main as labeller
from tools.corpus_gate.labeller.adjudicated import load_truth

REPO = Path(__file__).resolve().parents[1]


def _digital_fixture(name: str):
    return next(fixture for fixture in DIGITAL_FIXTURES if fixture.name == name)


US_BASIC = _digital_fixture("us_basic")
NULLS_NOT_ZEROS = _digital_fixture("nulls_not_zeros")


def _run(argv: list[str]) -> int:
    return labeller(argv)


@pytest.fixture(scope="module")
def golden_pdf(tmp_path_factory) -> Path:
    return render(US_BASIC, tmp_path_factory.mktemp("labeller-golden") / "us_basic.pdf")


@pytest.fixture()
def session(tmp_path, golden_pdf):
    """A fresh synthetic corpus: one verified manifest, one staged copy."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    shutil.copy2(golden_pdf, corpus / "us_basic.pdf")
    manifest_path = tmp_path / "manifest.json"
    manifest = manifests.create(corpus, manifest_path)
    return tmp_path, corpus, manifest_path, manifest


def _worksheet_of(out: Path, manifest: Manifest) -> Path:
    return sheets.worksheet_path(out, manifest.entries[0].id)


def _adjudicate(path: Path, rule) -> None:
    rows = sheets.read_worksheet(path)
    for row in rows:
        row["TRUTH"] = rule(row)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=sheets.WORKSHEET_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _confirm_extraction(row: dict[str, str]) -> str:
    return "EMPTY" if not row["extracted_value"] else "AGREE"


# --------------------------------------------------------------------------
# init: worksheets from the real pipeline
# --------------------------------------------------------------------------


def test_init_writes_worksheet_with_provenance_and_blank_truth(tmp_path, session):
    _tmp, corpus, manifest_path, manifest = session
    out = tmp_path / "worksheets"
    assert _run(["init", "--corpus", str(corpus), "--manifest", str(manifest_path),
                 "--out", str(out), "--repo", str(REPO)]) == 0

    rows = sheets.read_worksheet(_worksheet_of(out, manifest))
    assert rows
    assert all(row["TRUTH"] == "" for row in rows)

    doc_status = [row for row in rows if row["field"] == sheets.DOCUMENT_STATUS_FIELD]
    assert len(doc_status) == 1
    assert doc_status[0]["extracted_value"] in {"CLEAN", "NEEDS_REVIEW"}

    claim_rows = [row for row in rows if row["field"] == "claim_number"]
    assert claim_rows
    for row in claim_rows:
        assert row["extracted_value"], "the extraction read a claim number"
        assert row["page"], "provenance: the page the claim was read from"
        assert row["row_id"], "provenance: the row identity"
        assert row["method"] in {"digital", "vision", "manual", "sheet"}

    money = [row for row in rows if row["kind"] == sheets.MONEY]
    assert money
    assert any(row["extracted_value"] for row in money)


def test_init_refuses_without_a_manifest(tmp_path, session):
    _tmp, corpus, _manifest_path, _manifest = session
    assert _run(["init", "--corpus", str(corpus), "--manifest", str(tmp_path / "nope.json"),
                 "--out", str(tmp_path / "ws"), "--repo", str(REPO)]) == 3
    assert not (tmp_path / "ws").exists()


def test_init_refuses_a_document_the_manifest_no_longer_matches(tmp_path, session):
    _tmp, corpus, manifest_path, _manifest = session
    for pdf in corpus.glob("*.pdf"):
        pdf.write_bytes(pdf.read_bytes() + b"tampered")
    assert _run(["init", "--corpus", str(corpus), "--manifest", str(manifest_path),
                 "--out", str(tmp_path / "ws"), "--repo", str(REPO)]) == 3
    assert not (tmp_path / "ws").exists()


def test_init_refuses_writing_inside_the_repository(tmp_path, session):
    _tmp, corpus, manifest_path, _manifest = session
    inside = REPO / "labeller-must-not-write-here"
    try:
        assert _run(["init", "--corpus", str(corpus), "--manifest", str(manifest_path),
                     "--out", str(inside), "--repo", str(REPO)]) == 3
        assert not inside.exists()
    finally:
        shutil.rmtree(inside, ignore_errors=True)


def test_init_reports_a_failed_extraction_and_still_writes_the_rest(
    tmp_path, golden_pdf
):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    shutil.copy2(golden_pdf, corpus / "us_basic.pdf")
    (corpus / "junk.pdf").write_bytes(b"not a pdf at all")
    manifest_path = tmp_path / "manifest.json"
    manifest = manifests.create(corpus, manifest_path)
    out = tmp_path / "ws"

    assert _run(["init", "--corpus", str(corpus), "--manifest", str(manifest_path),
                 "--out", str(out), "--repo", str(REPO)]) == 4
    good = next(entry for entry in manifest.entries if entry.path == "us_basic.pdf")
    assert (out / f"{good.id}.csv").is_file()
    junk = next(entry for entry in manifest.entries if entry.path == "junk.pdf")
    assert not (out / f"{junk.id}.csv").exists()


def test_init_refuses_to_overwrite_a_worksheet_without_force(tmp_path, session):
    _tmp, corpus, manifest_path, _manifest = session
    out = tmp_path / "ws"
    assert _run(["init", "--corpus", str(corpus), "--manifest", str(manifest_path),
                 "--out", str(out), "--repo", str(REPO)]) == 0
    assert _run(["init", "--corpus", str(corpus), "--manifest", str(manifest_path),
                 "--out", str(out), "--repo", str(REPO)]) == 3
    assert _run(["init", "--corpus", str(corpus), "--manifest", str(manifest_path),
                 "--out", str(out), "--repo", str(REPO), "--force"]) == 0


# --------------------------------------------------------------------------
# import: truth, refused inputs, and the excluded count
# --------------------------------------------------------------------------


def _init(session, tmp_path) -> tuple[Path, Path, Manifest]:
    _tmp, corpus, manifest_path, manifest = session
    out = tmp_path / "ws"
    assert _run(["init", "--corpus", str(corpus), "--manifest", str(manifest_path),
                 "--out", str(out), "--repo", str(REPO)]) == 0
    return out, manifest_path, manifest


def test_import_round_trips_and_measures(tmp_path, session):
    out, manifest_path, manifest = _init(session, tmp_path)
    worksheet = _worksheet_of(out, manifest)
    _adjudicate(worksheet, _confirm_extraction)
    truth = tmp_path / "truth.json"

    assert _run(["import", "--corpus", str(session[1]), "--manifest", str(manifest_path),
                 "--worksheets", str(out), "--truth", str(truth), "--repo", str(REPO)]) == 0

    payload = json.loads(truth.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["manifest_sha256"] == manifests.load(manifest_path).sha256
    fields = payload["documents"][manifest.entries[0].id]["fields"]
    assert fields
    assert all(record["truth"] is not None for record in fields.values())
    assert all(record["extracted"] != "" or record["truth"] == "EMPTY"
               for record in fields.values())

    report_dir = tmp_path / "report"
    labels_path = tmp_path / "labels.json"
    labels_payload = corpus_labels.template(manifest)
    labels_payload["labels"][manifest.entries[0].id]["carrier"] = "Synthetic Carrier"
    labels_payload["labels"][manifest.entries[0].id]["template_family"] = "synthetic-family"
    labels_path.write_text(json.dumps(labels_payload), encoding="utf-8")
    assert _run(["measure", "--truth", str(truth), "--manifest", str(manifest_path),
                 "--labels", str(labels_path), "--out", str(report_dir),
                 "--repo", str(REPO)]) == 0

    report = json.loads((report_dir / "measure.json").read_text(encoding="utf-8"))
    overall = report["overall"]
    assert overall["money"]["accuracy"] == 1.0
    assert overall["text"]["accuracy"] == 1.0
    assert overall["claim_status"]["agreement"] == 1.0
    assert overall["excluded_fields"] == 0
    assert overall["documents"] == 1
    assert "Synthetic Carrier" in report["per_carrier"]
    assert report["per_carrier"]["Synthetic Carrier"]["money"]["accuracy"] == 1.0
    assert report["per_template_family"]["synthetic-family"]["money"]["accuracy"] == 1.0
    markdown = (report_dir / "measure.md").read_text(encoding="utf-8")
    assert "What may be published" in markdown


def test_import_merges_sessions_and_counts_blanks(tmp_path, golden_pdf):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    shutil.copy2(golden_pdf, corpus / "us_basic.pdf")
    render(NULLS_NOT_ZEROS, corpus / "nulls.pdf")
    manifest_path = tmp_path / "manifest.json"
    manifest = manifests.create(corpus, manifest_path)
    out = tmp_path / "ws"
    truth = tmp_path / "truth.json"

    assert _run(["init", "--corpus", str(corpus), "--manifest", str(manifest_path),
                 "--out", str(out), "--repo", str(REPO)]) == 0
    first = sheets.worksheet_path(out, manifest.entries[0].id)
    second = sheets.worksheet_path(out, manifest.entries[1].id)

    _adjudicate(first, _confirm_extraction)
    assert _run(["import", "--corpus", str(corpus), "--manifest", str(manifest_path),
                 "--worksheets", str(out), "--truth", str(truth), "--repo", str(REPO)]) == 0
    payload = json.loads(truth.read_text(encoding="utf-8"))
    assert set(payload["documents"]) == {entry.id for entry in manifest.entries}
    first_fields = payload["documents"][manifest.entries[0].id]["fields"]
    assert all(record["truth"] is not None for record in first_fields.values())
    second_fields = payload["documents"][manifest.entries[1].id]["fields"]
    assert all(record["truth"] is None for record in second_fields.values())

    # A worksheet that comes back blank -- regenerated, or not yet adjudicated
    # -- must never revoke an adjudicated truth.
    _adjudicate(first, lambda row: "")
    _adjudicate(second, _confirm_extraction)
    assert _run(["import", "--corpus", str(corpus), "--manifest", str(manifest_path),
                 "--worksheets", str(out), "--truth", str(truth), "--repo", str(REPO)]) == 0
    payload = json.loads(truth.read_text(encoding="utf-8"))
    assert payload["documents"][manifest.entries[0].id]["fields"] == first_fields
    assert all(record["truth"] is not None
               for record in payload["documents"][manifest.entries[1].id]["fields"].values())


@pytest.mark.parametrize("field,truth", [
    ("claim_status", "BANANA"),
    ("paid_total", "12,000.00"),
    ("date_of_loss", "03/04/2024"),
])
def test_import_rejects_schema_invalid_truth(tmp_path, session, field, truth):
    out, manifest_path, manifest = _init(session, tmp_path)
    worksheet = _worksheet_of(out, manifest)
    rows = sheets.read_worksheet(worksheet)
    victim = next(row for row in rows if row["field"] == field)
    victim["TRUTH"] = truth
    with worksheet.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=sheets.WORKSHEET_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    assert _run(["import", "--corpus", str(session[1]), "--manifest", str(manifest_path),
                 "--worksheets", str(out), "--truth", str(tmp_path / "truth.json"),
                 "--repo", str(REPO)]) == 3
    assert not (tmp_path / "truth.json").exists()


@pytest.mark.parametrize("truth", ["NaN", "Infinity", "-inf"])
def test_import_rejects_non_finite_money_truth(tmp_path, session, truth):
    out, manifest_path, manifest = _init(session, tmp_path)
    worksheet = _worksheet_of(out, manifest)
    rows = sheets.read_worksheet(worksheet)
    victim = next(row for row in rows if row["field"] == "paid_total")
    victim["TRUTH"] = truth
    with worksheet.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=sheets.WORKSHEET_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    assert _run(["import", "--corpus", str(session[1]), "--manifest", str(manifest_path),
                 "--worksheets", str(out), "--truth", str(tmp_path / "truth.json"),
                 "--repo", str(REPO)]) == 3
    assert not (tmp_path / "truth.json").exists()


def test_import_refuses_an_unknown_field_row(tmp_path, session):
    out, manifest_path, manifest = _init(session, tmp_path)
    worksheet = _worksheet_of(out, manifest)
    rows = sheets.read_worksheet(worksheet)
    bogus = dict(rows[0])
    bogus["field"] = "not_a_real_field"
    bogus["TRUTH"] = "AGREE"
    rows.append(bogus)
    with worksheet.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=sheets.WORKSHEET_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    assert _run(["import", "--corpus", str(session[1]), "--manifest", str(manifest_path),
                 "--worksheets", str(out), "--truth", str(tmp_path / "truth.json"),
                 "--repo", str(REPO)]) == 3
    assert not (tmp_path / "truth.json").exists()


def test_import_refuses_a_document_the_manifest_no_longer_matches(tmp_path, session):
    out, manifest_path, manifest = _init(session, tmp_path)
    _adjudicate(_worksheet_of(out, manifest), _confirm_extraction)
    for pdf in session[1].glob("*.pdf"):
        pdf.write_bytes(b"replaced")
    assert _run(["import", "--corpus", str(session[1]), "--manifest", str(manifest_path),
                 "--worksheets", str(out), "--truth", str(tmp_path / "truth.json"),
                 "--repo", str(REPO)]) == 3
    assert not (tmp_path / "truth.json").exists()


def test_import_refuses_without_a_manifest(tmp_path, session):
    out, manifest_path, manifest = _init(session, tmp_path)
    assert _run(["import", "--corpus", str(session[1]), "--manifest", str(tmp_path / "nope.json"),
                 "--worksheets", str(out), "--truth", str(tmp_path / "truth.json"),
                 "--repo", str(REPO)]) == 3


def test_unadjudicated_truth_is_excluded_and_never_correct(tmp_path, session):
    out, manifest_path, manifest = _init(session, tmp_path)
    worksheet = _worksheet_of(out, manifest)
    _adjudicate(worksheet, lambda row: "")  # the owner adjudicated nothing
    truth = tmp_path / "truth.json"
    assert _run(["import", "--corpus", str(session[1]), "--manifest", str(manifest_path),
                 "--worksheets", str(out), "--truth", str(truth), "--repo", str(REPO)]) == 0

    report_dir = tmp_path / "report"
    assert _run(["measure", "--truth", str(truth), "--manifest", str(manifest_path),
                 "--out", str(report_dir), "--repo", str(REPO)]) == 0
    report = json.loads((report_dir / "measure.json").read_text(encoding="utf-8"))
    overall = report["overall"]
    assert overall["excluded_fields"] > 0
    assert overall["money"]["expected"] == 0
    assert overall["money"]["accuracy"] is None
    assert overall["text"]["accuracy"] is None
    assert overall["claim_status"]["adjudicated"] == 0
    assert overall["claim_status"]["agreement"] is None


def test_measure_refuses_truth_from_another_manifest(tmp_path, session, golden_pdf):
    out, manifest_path, manifest = _init(session, tmp_path)
    _adjudicate(_worksheet_of(out, manifest), _confirm_extraction)
    truth = tmp_path / "truth.json"
    assert _run(["import", "--corpus", str(session[1]), "--manifest", str(manifest_path),
                 "--worksheets", str(out), "--truth", str(truth), "--repo", str(REPO)]) == 0

    other_corpus = tmp_path / "other"
    other_corpus.mkdir()
    shutil.copy2(golden_pdf, other_corpus / "us_basic.pdf")
    other_manifest = tmp_path / "other-manifest.json"
    manifests.create(other_corpus, other_manifest)

    assert _run(["measure", "--truth", str(truth), "--manifest", str(other_manifest),
                 "--out", str(tmp_path / "report"), "--repo", str(REPO)]) == 3


def test_measure_refuses_truth_and_labels_inside_the_repository(tmp_path, session):
    out, manifest_path, manifest = _init(session, tmp_path)
    _adjudicate(_worksheet_of(out, manifest), _confirm_extraction)
    truth = tmp_path / "truth.json"
    assert _run(["import", "--corpus", str(session[1]), "--manifest", str(manifest_path),
                 "--worksheets", str(out), "--truth", str(truth), "--repo", str(REPO)]) == 0

    inside_truth = REPO / "labeller-truth-must-not-live-here.json"
    assert _run(["measure", "--truth", str(inside_truth), "--manifest", str(manifest_path),
                 "--out", str(tmp_path / "report-a"), "--repo", str(REPO)]) == 3
    assert not (tmp_path / "report-a").exists()

    inside_labels = REPO / "labeller-labels-must-not-live-here.json"
    assert _run(["measure", "--truth", str(truth), "--manifest", str(manifest_path),
                 "--labels", str(inside_labels), "--out", str(tmp_path / "report-b"),
                 "--repo", str(REPO)]) == 3
    assert not (tmp_path / "report-b").exists()


# --------------------------------------------------------------------------
# measure: the math, hand computed
# --------------------------------------------------------------------------

HAND_MANIFEST = Manifest(
    salt=bytes.fromhex("11" * 32),
    entries=(
        Entry("doc-1", "1.pdf", "a" * 64, 1),
        Entry("doc-2", "2.pdf", "b" * 64, 1),
        Entry("doc-3", "3.pdf", "c" * 64, 1),
    ),
    sha256="d" * 64,
)

HAND_LABELS = corpus_labels.Labels(
    {
        "doc-1": {"carrier": "Carrier A", "template_family": "fam-1"},
        "doc-2": {"carrier": "Carrier A"},
        "doc-3": {},
    }
)


def _hand_truth() -> dict:
    def field(kind, extracted, truth):
        return {"kind": kind, "field": "", "extracted": extracted, "truth": truth}

    return {
        "version": 1,
        "manifest_sha256": HAND_MANIFEST.sha256,
        "documents": {
            "doc-1": {
                "sha256": "a" * 64,
                "fields": {
                    "c0:paid_total": field("money", "100.00", "100.00"),
                    "c1:paid_total": field("money", "250.00", "999.00"),
                    "c0:claimant_name": field("text", "Person One", "AGREE"),
                    "c2:paid_total": field("money", "75.00", None),
                    "c0:claim_status": field("status", "OPEN", "OPEN"),
                    "document_status": field("doc_status", "NEEDS_REVIEW", "NEEDS_REVIEW"),
                },
            },
            "doc-2": {
                "sha256": "b" * 64,
                "fields": {
                    "c0:paid_total": field("money", "0.00", "EMPTY"),
                    "c1:claimant_name": field("text", "", "Person Two"),
                    "c2:claimant_name": field("text", "", None),
                    "c0:claim_status": field("status", "OPEN", "CLOSED"),
                    "document_status": field("doc_status", "CLEAN", "NEEDS_REVIEW"),
                },
            },
            "doc-3": {
                "sha256": "c" * 64,
                "fields": {
                    "c0:paid_total": field("money", "10.00", None),
                    "document_status": field("doc_status", "CLEAN", None),
                },
            },
        },
    }


def test_measure_matches_hand_computed_expectations(tmp_path):
    truth_path = tmp_path / "truth.json"
    truth_path.write_text(json.dumps(_hand_truth()), encoding="utf-8")
    truth = load_truth(truth_path, HAND_MANIFEST)

    report = accuracy.measure(truth, HAND_MANIFEST, HAND_LABELS)

    assert report["documents_listed"] == 3
    assert report["documents_adjudicated"] == 3
    assert report["documents_unlabelled"] == 1

    overall = report["overall"]
    money = overall["money"]
    assert (money["expected"], money["correct"], money["missing"]) == (2, 1, 0)
    assert money["accuracy"] == 0.5
    assert money["should_be_null"] == 1
    assert money["null_ok"] == 0
    assert money["extra_values"] == 1
    assert money["nulls_as_zeros"] == 1
    text = overall["text"]
    assert (text["expected"], text["correct"], text["missing"]) == (2, 1, 1)
    assert text["accuracy"] == 0.5
    status = overall["claim_status"]
    assert status == {"adjudicated": 2, "agree": 1, "disagree": 1, "agreement": 0.5}
    assert overall["document_status"] == {
        "flagged_agrees_right": 1,
        "flagged_agrees_wrong": 0,
        "clean_extraction_wrong": 1,
        "clean_agrees_right": 0,
        "unadjudicated": 1,
    }
    assert overall["excluded_fields"] == 4

    carrier_a = report["per_carrier"]["Carrier A"]
    assert carrier_a["money"]["accuracy"] == 0.5
    assert carrier_a["text"]["accuracy"] == 0.5
    assert carrier_a["claim_status"]["agreement"] == 0.5
    assert carrier_a["documents"] == 2
    assert carrier_a["excluded_fields"] == 2

    fam = report["per_template_family"]["fam-1"]
    assert fam["money"]["accuracy"] == 0.5
    assert fam["text"]["accuracy"] == 1.0
    assert fam["documents"] == 1

    doc1 = report["per_document"]["doc-1"]
    assert doc1["money"]["accuracy"] == 0.5
    assert doc1["text"]["accuracy"] == 1.0
    assert doc1["excluded_fields"] == 1

    markdown = accuracy.render_markdown(report)
    assert "50.00%" in markdown


def test_accuracy_counts_agree_on_empty_extraction_as_missing_not_correct():
    bucket = accuracy._value_bucket()
    accuracy._add_value_row(bucket, accuracy.TEXT, "", "AGREE")
    assert bucket["expected"] == 1
    assert bucket["missing"] == 1
    assert bucket["correct"] == 0


def test_measure_reports_no_adjudicated_group_as_null_not_perfect(tmp_path):
    truth_path = tmp_path / "truth.json"
    truth_path.write_text(json.dumps(_hand_truth()), encoding="utf-8")
    truth = load_truth(truth_path, HAND_MANIFEST)
    truth["documents"] = {
        "doc-3": truth["documents"]["doc-3"],
    }
    report = accuracy.measure(truth, HAND_MANIFEST, HAND_LABELS)
    overall = report["overall"]
    assert overall["money"]["expected"] == 0
    assert overall["money"]["accuracy"] is None
    assert overall["text"]["accuracy"] is None
    assert overall["excluded_fields"] == 2
    assert overall["documents"] == 1


# --------------------------------------------------------------------------
# privacy: what the committed tree may and may not contain
# --------------------------------------------------------------------------

PRIVATE_PATH_PATTERNS = (
    re.compile(r"[A-Za-z]:[\\/](?:Users|home|OneDrive|Documents)", re.IGNORECASE),
    re.compile(r"/Users/"),
    re.compile(r"(?i)local_test_corpus"),
    re.compile(r"(?i)losslift-worktrees"),
)


def test_committed_labeller_tree_is_privacy_clean():
    package = REPO / "tools" / "corpus_gate" / "labeller"
    files = sorted(package.glob("*.py"))
    assert files
    document_texts = [
        text
        for fixture in DIGITAL_FIXTURES
        for text in (fixture.carrier, fixture.named_insured, fixture.policy_number)
    ]
    for path in files:
        source = path.read_text(encoding="utf-8")
        for pattern in PRIVATE_PATH_PATTERNS:
            assert not pattern.search(source), f"{path.name} names a private path"
        for text in document_texts:
            assert text not in source, f"{path.name} embeds document text"
