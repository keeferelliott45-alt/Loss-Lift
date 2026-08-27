"""Stage 2b — vision extraction for scanned pages.

The model is replaced by a stand-in that transcribes the fixture exactly as it
was printed. What is under test is this app's handling of a transcription:
that the same deterministic parser reads it, that it is marked as vision-read,
and that its confidence is capped no matter what the model claims.
"""

from __future__ import annotations

import json
import os
from datetime import date

import pymupdf
import pytest

from core.extract_vision import (
    RENDER_DPI,
    RESPONSE_SCHEMA,
    VISION_CONFIDENCE_CAP,
    VisionUnavailable,
    extract_page,
    extract_scanned_pages,
    load_prompt,
    parse_vision_response,
    render_page,
    render_pages,
    vision_enabled,
)
from core.pipeline import run_pipeline
from core.schema import ExtractionMethod, SourceMethod
from tests.golden import fixtures as fx
from tests.golden.generate import cell_text, format_money


def transcription(fixture: fx.Fixture, page_number: int = 1) -> dict:
    """Exactly what is printed on the page, as the prompt asks for it."""
    rows = [
        {
            "cells": [cell_text(fixture, claim, column) for column in fixture.columns],
            "kind": "data",
        }
        for claim in fixture.claims
    ]
    totals = fixture.printed_totals()
    if fixture.print_totals:
        rows.append({
            "cells": [
                fixture.total_label if index == 0
                else (format_money(totals[column.field], fixture.number_format)
                      if column.field in totals else "")
                for index, column in enumerate(fixture.columns)
            ],
            "kind": "total",
        })
    return {
        "headers": [column.label for column in fixture.columns],
        "rows": rows,
        "printed_claim_count": len(fixture.claims) if fixture.print_claim_count else None,
        "valuation_date": fixture.valuation_date.strftime("%m/%d/%Y"),
    }


class StandInModel:
    """A Gemini stand-in that returns a fixed transcription."""

    def __init__(self, payload, confidence_claim: float = 0.99) -> None:
        self.payload = payload
        self.calls = 0
        self.contents: list = []
        self.confidence_claim = confidence_claim
        self.models = self

    def generate_content(self, model: str, contents, config=None):
        self.calls += 1
        self.contents.append(contents)
        body = self.payload
        if callable(body):
            body = body(self.calls)
        text = body if isinstance(body, str) else json.dumps(body)
        return type("Response", (), {"text": text})()


@pytest.fixture()
def scanned_model():
    return StandInModel(transcription(fx.SCANNED))


# --- Rendering -------------------------------------------------------------


def test_pages_render_at_300_dpi(golden_dir):
    assert RENDER_DPI == 300
    rendered = render_page(golden_dir / "scanned.pdf", 1)
    assert rendered.page == 1
    assert rendered.image[:8] == b"\x89PNG\r\n\x1a\n"
    # US letter landscape at 300 DPI.
    assert rendered.width == 3300 and rendered.height == 2550


def test_render_pages_returns_one_image_per_page(golden_dir):
    rendered = render_pages(golden_dir / "scanned.pdf", [1])
    assert [page.page for page in rendered] == [1]


def test_rendering_a_page_that_does_not_exist_fails_loudly(golden_dir):
    with pytest.raises(VisionUnavailable, match="does not exist"):
        render_page(golden_dir / "scanned.pdf", 99)


# --- Prompt and schema -----------------------------------------------------


def test_prompt_forbids_calculating():
    prompt = load_prompt()
    assert "Never compute" in prompt
    assert "character for character" in prompt
    assert "-0-" in prompt   # placeholders must survive transcription


def test_schema_requires_headers_and_rows():
    assert RESPONSE_SCHEMA["required"] == ["headers", "rows"]


# --- Response parsing ------------------------------------------------------


def test_parses_a_transcription_into_a_raw_table():
    table = parse_vision_response(json.dumps(transcription(fx.SCANNED)), 1)
    assert table.strategy == "vision"
    assert table.page == 1
    assert len(table.rows) == len(fx.SCANNED.claims)
    assert table.total_rows
    assert table.headers[0] == "Claim Number"


def test_tolerates_code_fences():
    table = parse_vision_response(
        '```json\n{"headers": ["A"], "rows": [{"cells": ["x"]}]}\n```', 2
    )
    assert table.rows[0].cells == ["x"]


def test_rows_are_padded_and_trimmed_to_the_header_width():
    table = parse_vision_response(
        {"headers": ["A", "B", "C"], "rows": [
            {"cells": ["1"]},                    # short
            {"cells": ["1", "2", "3", "4"]},     # long
        ]},
        1,
    )
    assert table.rows[0].cells == ["1", "", ""]
    assert table.rows[1].cells == ["1", "2", "3"]


def test_blank_rows_are_dropped():
    table = parse_vision_response(
        {"headers": ["A", "B"], "rows": [{"cells": ["", ""]}, {"cells": ["x", ""]}]}, 1
    )
    assert len(table.rows) == 1


def test_a_response_with_no_headers_fails_loudly():
    with pytest.raises(VisionUnavailable, match="no column headers"):
        parse_vision_response({"headers": [], "rows": []}, 1)


def test_non_json_fails_loudly():
    with pytest.raises(VisionUnavailable, match="did not return JSON"):
        parse_vision_response("The table shows six claims.", 1)


def test_page_printed_facts_are_carried():
    table = parse_vision_response(json.dumps(transcription(fx.SCANNED)), 1)
    assert table.printed_claim_count == len(fx.SCANNED.claims)
    assert table.valuation_date_text == "12/31/2024"


# --- Calling the model -----------------------------------------------------


def test_the_image_and_the_prompt_are_both_sent(golden_dir, scanned_model):
    rendered = render_page(golden_dir / "scanned.pdf", 1)
    extract_page(rendered, client=scanned_model)
    assert scanned_model.calls == 1
    sent = scanned_model.contents[0]
    assert any(isinstance(part, str) and "Never compute" in part for part in sent)

    images = [part for part in sent if not isinstance(part, str)]
    assert len(images) == 1, "exactly one page image per call"
    payload = images[0]
    data = payload if isinstance(payload, bytes) else payload.inline_data.data
    assert data == rendered.image
    if not isinstance(payload, bytes):
        assert payload.inline_data.mime_type == "image/png"


def test_a_failing_call_is_reported_not_raised_raw(golden_dir):
    class Broken(StandInModel):
        def generate_content(self, model, contents, config=None):
            raise RuntimeError("429 quota exceeded")

    rendered = render_page(golden_dir / "scanned.pdf", 1)
    with pytest.raises(VisionUnavailable, match="429"):
        extract_page(rendered, client=Broken(None))


def test_without_a_key_the_message_says_what_to_do(golden_dir, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    rendered = render_page(golden_dir / "scanned.pdf", 1)
    with pytest.raises(VisionUnavailable, match="GEMINI_API_KEY"):
        extract_page(rendered)


def test_one_bad_page_does_not_lose_the_good_ones(golden_dir):
    payloads = [transcription(fx.SCANNED), "not json at all"]
    model = StandInModel(lambda call: payloads[min(call - 1, 1)])
    tables = extract_scanned_pages(golden_dir / "scanned.pdf", [1, 1], client=model)
    assert len(tables) == 1


def test_all_pages_failing_is_reported(golden_dir):
    model = StandInModel("not json")
    with pytest.raises(VisionUnavailable):
        extract_scanned_pages(golden_dir / "scanned.pdf", [1], client=model)


# --- Through the pipeline --------------------------------------------------


def _extractor(model):
    def run(path, pages, **kwargs):
        return extract_scanned_pages(path, pages, client=model)
    return run


def test_a_scanned_document_extracts_through_vision(golden_dir, scanned_model):
    result = run_pipeline(
        golden_dir / "scanned.pdf",
        use_vision=True,
        vision_extractor=_extractor(scanned_model),
    )
    document = result.document
    assert document.extraction_method is ExtractionMethod.VISION
    assert document.scanned_pages == [1]
    assert len(document.claims) == len(fx.SCANNED.claims)
    assert document.claims[0].claim_number == "GL-2024-0001"


def test_vision_values_parse_exactly_like_digital_ones(golden_dir, scanned_model):
    """A transcription goes through the same parser, so the same characters
    give the same Decimal whichever path read them."""
    scanned = run_pipeline(
        golden_dir / "scanned.pdf",
        use_vision=True,
        vision_extractor=_extractor(scanned_model),
    )
    digital = run_pipeline(golden_dir / "us_basic.pdf", use_vision=False)

    by_number = {c.claim_number: c for c in digital.document.claims}
    for claim in scanned.document.claims:
        twin = by_number[claim.claim_number]
        assert claim.incurred_total == twin.incurred_total
        assert claim.paid_total == twin.paid_total
        assert claim.recovery_total == twin.recovery_total
        assert claim.date_of_loss == twin.date_of_loss


def test_every_vision_field_is_marked_and_capped(golden_dir, scanned_model):
    result = run_pipeline(
        golden_dir / "scanned.pdf",
        use_vision=True,
        vision_extractor=_extractor(scanned_model),
    )
    for claim in result.document.claims:
        assert claim.source_method is SourceMethod.VISION
        for field_name, score in claim.field_confidence.items():
            assert score <= VISION_CONFIDENCE_CAP, f"{field_name} was {score}"


def test_confidence_is_capped_even_when_the_model_is_certain(golden_dir):
    model = StandInModel(transcription(fx.SCANNED), confidence_claim=1.0)
    result = run_pipeline(
        golden_dir / "scanned.pdf", use_vision=True, vision_extractor=_extractor(model)
    )
    assert max(
        score
        for claim in result.document.claims
        for score in claim.field_confidence.values()
    ) == VISION_CONFIDENCE_CAP


def test_the_valuation_date_comes_from_the_page(golden_dir, scanned_model):
    """A scanned page has no text layer, so R-06 would fire on every scan if
    the vision pass did not report what the page prints."""
    result = run_pipeline(
        golden_dir / "scanned.pdf",
        use_vision=True,
        vision_extractor=_extractor(scanned_model),
    )
    assert result.document.valuation_date == date(2024, 12, 31)
    assert not [f for f in result.reconciliation.findings if f.rule_id == "R-06"]


def test_footer_totals_from_a_scan_still_drive_r04(golden_dir, scanned_model):
    result = run_pipeline(
        golden_dir / "scanned.pdf",
        use_vision=True,
        vision_extractor=_extractor(scanned_model),
    )
    assert result.document.printed_totals
    assert not [f for f in result.reconciliation.findings if f.rule_id == "R-04"]


def test_vision_off_says_what_was_skipped(golden_dir):
    result = run_pipeline(golden_dir / "scanned.pdf", use_vision=False)
    assert result.document.claims == []
    assert any("scans" in warning for warning in result.warnings)


def test_a_failing_vision_pass_does_not_lose_the_digital_pages(golden_dir, tmp_path):
    """A mixed document: page 1 digital, page 2 a scan. If vision fails the
    digital rows must still arrive, and R-05 reports what is missing."""
    mixed = pymupdf.open(golden_dir / "us_basic.pdf")
    scan = pymupdf.open(golden_dir / "scanned.pdf")
    mixed.insert_pdf(scan)
    path = tmp_path / "mixed.pdf"
    mixed.save(str(path))
    mixed.close()
    scan.close()

    def failing(pdf_path, pages, **kwargs):
        raise VisionUnavailable("the vision call failed (503)")

    result = run_pipeline(path, use_vision=True, vision_extractor=failing)
    assert result.classification.extraction_method is ExtractionMethod.MIXED
    assert result.classification.scanned_pages == [2]
    assert len(result.document.claims) == 6           # page 1 survived
    assert any("503" in warning for warning in result.warnings)


def test_a_mixed_document_merges_both_paths(golden_dir, tmp_path):
    mixed = pymupdf.open(golden_dir / "us_basic.pdf")
    scan = pymupdf.open(golden_dir / "scanned.pdf")
    mixed.insert_pdf(scan)
    path = tmp_path / "mixed.pdf"
    mixed.save(str(path))
    mixed.close()
    scan.close()

    payload = transcription(fx.SCANNED)
    for row in payload["rows"]:
        if row["kind"] == "data":
            row["cells"][0] = row["cells"][0].replace("GL-2024", "SC-2024")
    model = StandInModel(payload)

    result = run_pipeline(path, use_vision=True, vision_extractor=_extractor(model))
    methods = {claim.source_method for claim in result.document.claims}
    assert methods == {SourceMethod.DIGITAL, SourceMethod.VISION}
    assert len(result.document.claims) == 6 + len(fx.SCANNED.claims)
    assert result.document.extraction_method is ExtractionMethod.MIXED
    # Rows stay in page order so the review screen matches the document.
    pages = [claim.source_page for claim in result.document.claims]
    assert pages == sorted(pages)


# --- Optional live check ---------------------------------------------------


@pytest.mark.llm
@pytest.mark.skipif(
    os.getenv("LOSSLIFT_RUN_LLM_TESTS", "0") != "1" or not vision_enabled(),
    reason="set LOSSLIFT_RUN_LLM_TESTS=1 and GEMINI_API_KEY to call Gemini",
)
def test_live_gemini_reads_the_scanned_fixture(golden_dir):  # pragma: no cover
    rendered = render_page(golden_dir / "scanned.pdf", 1)
    table = extract_page(rendered)
    assert table.rows
    numbers = {cell for row in table.rows for cell in row.cells}
    assert any("15,700.50" in cell for cell in numbers)
