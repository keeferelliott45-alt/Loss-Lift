"""Contract checks where packet and export-privacy work meet."""

from __future__ import annotations

import io
import json
import zipfile

from core import export
from core.xlsx_safety import WITHHELD_NOTE, safe_member_name
from tests.test_accounting_and_json import _packet
from tests.test_export_redaction import (
    CLAIMANT,
    DESC,
    SENTINELS,
    sentinel_document,
)


def test_packet_runs_sheet_uses_text_policy(tmp_path):
    result = _packet(tmp_path)
    document = result.document
    assert document.is_packet
    document.runs[0].carrier = "=2+2"
    workbook = export.build_workbook(document, result.reconciliation)
    assert workbook.sheetnames == list(export.workbook_sheets(document))
    runs = workbook["Runs"]
    carrier_column = [cell.value for cell in runs[1]].index("Carrier") + 1
    carrier = runs.cell(2, carrier_column)
    assert carrier.value == "=2+2"
    assert carrier.data_type == "s"
    assert carrier._style.quotePrefix
    with zipfile.ZipFile(io.BytesIO(export.to_bytes(document, result.reconciliation))) as archive:
        assert not any(b"<f>" in archive.read(name) for name in archive.namelist())


def test_redacted_json_scrubs_findings_and_withholds_review_notes():
    document, result = sentinel_document()
    document.claims[0].raw_cells["cause_of_loss"] = f"A loss for {CLAIMANT}"
    data = export.build_json(document, result, redact=True)
    encoded = json.dumps(data)
    for sentinel in SENTINELS:
        assert sentinel not in encoded
    assert "Sentinelclaimant" not in encoded
    assert data["review_log"][0]["note"] == WITHHELD_NOTE
    assert data["findings"][0]["rule_id"] == "R-01"
    assert data["claims"][0]["claim_number"] == "S-1"


def test_json_filename_applies_member_and_redaction_policy():
    document, _ = sentinel_document()
    document.source_filename = f"../{CLAIMANT}\x01\\report.pdf"
    name = export.suggested_json_filename(document, needs_review=True, redact=True)
    assert name == safe_member_name(name)
    assert name.endswith("-NEEDS-REVIEW.json")
    assert CLAIMANT not in name
    assert "/" not in name and "\\" not in name and "\x01" not in name


def test_batch_reports_only_successful_members_to_callback(monkeypatch):
    first, result = sentinel_document()
    second = first.model_copy(deep=True)
    second.document_id = "second"
    second.source_filename = "bad.pdf"
    original = export.to_bytes

    def maybe_fail(document, *args, **kwargs):
        if document.document_id == "second":
            raise ValueError("synthetic failure")
        return original(document, *args, **kwargs)

    monkeypatch.setattr(export, "to_bytes", maybe_fail)
    exported = []
    payload, failures = export.build_batch_zip(
        [(first, result, None), (second, result, None)],
        redact=True,
        on_success=lambda document: exported.append(document.document_id),
    )
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert len(archive.namelist()) == 1
    assert exported == [first.document_id]
    assert len(failures) == 1 and "bad.pdf" in failures[0]
