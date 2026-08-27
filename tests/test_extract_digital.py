"""Stages 0, 1 and 2a against the synthetic fixtures."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from core.classify import SCANNED_CHAR_THRESHOLD, classify_pdf
from core.extract_digital import (
    Line,
    Word,
    cluster_lines,
    column_bounds,
    extract_metadata,
    extract_pdf,
    split_cells,
)
from core.ingest import CACHE, ExtractionCache, IngestError, discard, ingest, ingest_path, sha256_bytes
from core.schema import ExtractionMethod


# --- Stage 0 ---------------------------------------------------------------


def test_ingest_hashes_and_stages(tmp_path):
    data = b"%PDF-1.7\nnot really a pdf but it starts right"
    staged = ingest(data, "loss run.pdf", tmp_path)
    assert staged.sha256 == sha256_bytes(data)
    assert staged.path.read_bytes() == data
    assert staged.source_filename == "loss run.pdf"
    assert staged.size_bytes == len(data)


def test_ingest_refuses_non_pdf(tmp_path):
    with pytest.raises(IngestError, match="not a PDF"):
        ingest(b"PK\x03\x04 spreadsheet", "claims.xlsx", tmp_path)


def test_ingest_refuses_empty(tmp_path):
    with pytest.raises(IngestError, match="empty"):
        ingest(b"", "empty.pdf", tmp_path)


def test_ingest_strips_directory_traversal(tmp_path):
    staged = ingest(b"%PDF-1.7 x", "../../etc/passwd.pdf", tmp_path)
    assert staged.source_filename == "passwd.pdf"
    assert staged.path.parent == tmp_path


def test_identical_bytes_hash_identically(tmp_path, golden_dir):
    first = ingest_path(golden_dir / "us_basic.pdf", tmp_path)
    second = ingest_path(golden_dir / "us_basic.pdf", tmp_path)
    assert first.sha256 == second.sha256
    assert first.document_id != second.document_id


def test_cache_is_keyed_by_hash():
    cache = ExtractionCache()
    assert cache.get("abc") is None
    cache.put("abc", {"claims": 3})
    assert "abc" in cache and len(cache) == 1
    assert cache.get("abc") == {"claims": 3}
    cache.clear()
    assert len(cache) == 0


def test_discard_removes_the_file(tmp_path):
    staged = ingest(b"%PDF-1.7 x", "a.pdf", tmp_path)
    assert staged.exists
    discard(staged, remove_directory=False)
    assert not staged.exists


# --- Stage 1 ---------------------------------------------------------------


def test_digital_pages_classify_as_digital(golden_dir):
    classification = classify_pdf(golden_dir / "us_basic.pdf")
    assert classification.page_count == 1
    assert classification.scanned_pages == []
    assert classification.extraction_method is ExtractionMethod.DIGITAL
    assert classification.pages[0].char_count > SCANNED_CHAR_THRESHOLD


def test_scanned_pages_classify_as_scanned(golden_dir):
    classification = classify_pdf(golden_dir / "scanned.pdf")
    assert classification.scanned_pages == [1]
    assert classification.digital_pages == []
    assert classification.extraction_method is ExtractionMethod.VISION
    assert classification.is_scanned(1) is True


def test_classification_is_per_page(golden_dir):
    classification = classify_pdf(golden_dir / "multipage_repeat_header.pdf")
    assert classification.page_count == 3
    assert classification.digital_pages == [1, 2, 3]


# --- Word geometry ---------------------------------------------------------


def _word(text, x0, x1, top=100.0):
    return Word(text=text, x0=x0, x1=x1, top=top, bottom=top + 8)


def test_cluster_lines_groups_by_vertical_position():
    words = [_word("a", 0, 10, 100), _word("b", 20, 30, 101), _word("c", 0, 10, 130)]
    lines = cluster_lines(words)
    assert len(lines) == 2
    assert lines[0].text == "a b"
    assert lines[1].text == "c"


def test_split_cells_breaks_on_wide_gaps():
    line = Line(
        words=(_word("Claim", 0, 24), _word("Number", 26, 60), _word("Status", 200, 230)),
        index=0,
    )
    cells = split_cells(line, char_width=4.0)
    assert [text for text, _, _ in cells] == ["Claim Number", "Status"]


def test_column_bounds_finds_the_gutters():
    lines = [
        Line(words=(_word("aaa", 0, 30, 100), _word("bbb", 60, 90, 100)), index=0),
        Line(words=(_word("ccc", 2, 28, 120), _word("ddd", 62, 88, 120)), index=1),
    ]
    bounds = column_bounds(lines, char_width=4.0)
    assert len(bounds) == 2
    assert bounds[0][0] == 0 and bounds[0][1] == 30
    assert bounds[1][0] == 60


# --- Stage 2a --------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "strategy", "rows"),
    [
        ("us_basic", "words", 6),
        ("ruled_table", "ruled", 6),
        ("wc_medical", "words", 6),
        ("mainframe_trailing_minus", "words", 6),
    ],
)
def test_tables_are_found(golden_dir, name, strategy, rows):
    extraction = extract_pdf(golden_dir / f"{name}.pdf")
    assert extraction.tables, f"no table found in {name}"
    assert extraction.tables[0].strategy == strategy
    assert sum(len(table.rows) for table in extraction.tables) == rows


def test_positioned_text_columns_align(golden_dir):
    table = extract_pdf(golden_dir / "us_basic.pdf").tables[0]
    assert table.headers == [
        "Claim Number", "Date of Loss", "Date Reported", "Status", "Claimant",
        "Description of Loss", "Paid", "Reserve", "Recovery", "Total Incurred",
    ]
    assert table.rows[0].cells[0] == "GL-2024-0001"
    assert table.rows[0].cells[6] == "15,700.50"
    # A blank recovery stays blank rather than borrowing from a neighbour.
    assert table.rows[0].cells[8] == ""


def test_right_aligned_money_stays_in_its_column(golden_dir):
    """The gutter method's whole point: numbers do not drift left or right."""
    table = extract_pdf(golden_dir / "wc_medical.pdf").tables[0]
    for row in table.rows:
        assert row.cells[0].startswith("WC24-")
        for index in range(5, 14):
            cell = row.cells[index]
            assert cell == "" or cell.replace(",", "").replace(".", "").isdigit(), cell


def test_multipage_collects_every_page(golden_dir):
    extraction = extract_pdf(golden_dir / "multipage_repeat_header.pdf")
    assert len(extraction.tables) == 3
    assert [table.page for table in extraction.tables] == [1, 2, 3]
    assert sum(len(table.rows) for table in extraction.tables) == 14
    # The header repeats on every page and is never mistaken for a claim.
    for table in extraction.tables:
        assert all("Claim Number" not in row.cells[0] for row in table.rows)


def test_footer_totals_are_captured(golden_dir):
    extraction = extract_pdf(golden_dir / "us_basic.pdf")
    totals = [row for table in extraction.tables for row in table.total_rows]
    assert totals
    assert "156,341.90" in totals[0].cells


def test_ruled_tables_borrow_totals_from_the_word_pass(golden_dir):
    table = extract_pdf(golden_dir / "ruled_table.pdf").tables[0]
    assert table.strategy == "ruled"
    assert table.total_rows, "R-04 needs the printed totals even on ruled tables"


def test_rows_carry_provenance(golden_dir):
    table = extract_pdf(golden_dir / "us_basic.pdf").tables[0]
    row = table.rows[0]
    assert row.page == 1
    assert row.bbox is not None and len(row.bbox) == 4
    assert row.line_index >= 0


def test_scanned_pages_yield_no_digital_table(golden_dir):
    extraction = extract_pdf(golden_dir / "scanned.pdf")
    assert extraction.tables == []


# --- Metadata --------------------------------------------------------------


def test_metadata_from_the_letterhead(golden_dir):
    metadata = extract_pdf(golden_dir / "us_basic.pdf").metadata
    assert metadata.carrier == "Meridian Casualty Company"
    assert metadata.named_insured == "Harbor Point Property Group LLC"
    assert metadata.policy_number == "GL-4471902-24"
    assert metadata.valuation_date_text == "12/31/2024"
    assert metadata.policy_period_start_text == "01/01/2024"
    assert metadata.policy_period_end_text == "12/31/2024"
    assert metadata.line_of_business == "GL"
    assert metadata.currency == "USD"
    assert metadata.printed_claim_count == 6


def test_printed_claim_count_found_on_the_last_page(golden_dir):
    metadata = extract_pdf(golden_dir / "multipage_repeat_header.pdf").metadata
    assert metadata.printed_claim_count == 14


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Valuation Date: 12/31/2024", "12/31/2024"),
        ("Valued as of 06/30/2024", "06/30/2024"),
        ("Evaluation Date - 2024-03-31", "2024-03-31"),
        ("As of Date: 31/12/2024", "31/12/2024"),
    ],
)
def test_valuation_date_phrasings(text, expected):
    assert extract_metadata(text).valuation_date_text == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Total Claims: 12", 12),
        ("Number of Claims: 7", 7),
        ("Total claims 1,204", 1204),
        ("14 claims listed", 14),
    ],
)
def test_claim_count_phrasings(text, expected):
    assert extract_metadata(text).printed_claim_count == expected


def test_missing_metadata_is_none_not_guessed():
    metadata = extract_metadata("A page with nothing useful on it.")
    assert metadata.valuation_date_text is None
    assert metadata.policy_number is None
    assert metadata.printed_claim_count is None
