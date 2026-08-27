"""Stage 2a — digital extraction (spec section 5).

``pdfplumber.extract_tables()`` first.  Loss runs are usually positioned text
rather than ruled tables, so when table detection fails the fallback is word
clustering: group words into lines by y, find the header line, derive column
boundaries from the vertical whitespace that runs down the whole table, and
assign each word to a column.

No LLM is involved and no number is interpreted here.  This stage produces
text cells with provenance; stage 4 turns them into values.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import pdfplumber

from core.normalize import clean_text, parse_int
from core.profiles import header_score, looks_like_header
from core.schema import RawRow, RawTable

#: A line whose leading words say "total" closes the table.
TOTAL_ROW_PATTERN = re.compile(r"\b(?:grand\s+)?(?:report\s+)?totals?\b", re.IGNORECASE)

#: Minimum blank width, in points, that counts as a column gutter.
MIN_GUTTER = 4.0

#: Words closer than this many multiples of a character width belong together.
CELL_GAP_FACTOR = 1.8


@dataclass(frozen=True)
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float

    @property
    def middle(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def centre(self) -> float:
        return (self.top + self.bottom) / 2


@dataclass(frozen=True)
class Line:
    words: tuple[Word, ...]
    index: int

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (
            min(word.x0 for word in self.words),
            min(word.top for word in self.words),
            max(word.x1 for word in self.words),
            max(word.bottom for word in self.words),
        )

    def leading_text(self, words: int = 3) -> str:
        return " ".join(word.text for word in self.words[:words])


# --------------------------------------------------------------------------
# Word geometry
# --------------------------------------------------------------------------


def _to_words(raw_words: Iterable[dict[str, Any]]) -> list[Word]:
    words = []
    for raw in raw_words:
        text = clean_text(raw.get("text", ""))
        if not text:
            continue
        words.append(
            Word(
                text=text,
                x0=float(raw["x0"]),
                x1=float(raw["x1"]),
                top=float(raw["top"]),
                bottom=float(raw["bottom"]),
            )
        )
    return words


def _median_char_width(words: Sequence[Word]) -> float:
    widths = [
        (word.x1 - word.x0) / len(word.text) for word in words if word.text
    ]
    return statistics.median(widths) if widths else 4.0


def cluster_lines(words: Sequence[Word]) -> list[Line]:
    """Group words into printed lines by vertical position."""
    if not words:
        return []
    heights = [word.bottom - word.top for word in words]
    tolerance = max(1.0, statistics.median(heights) * 0.5)

    lines: list[list[Word]] = []
    current: list[Word] = []
    current_centre = None
    for word in sorted(words, key=lambda w: (w.centre, w.x0)):
        if current_centre is None or abs(word.centre - current_centre) <= tolerance:
            current.append(word)
            centres = [w.centre for w in current]
            current_centre = sum(centres) / len(centres)
        else:
            lines.append(current)
            current = [word]
            current_centre = word.centre
    if current:
        lines.append(current)

    return [
        Line(words=tuple(sorted(group, key=lambda w: w.x0)), index=index)
        for index, group in enumerate(lines)
    ]


def split_cells(line: Line, char_width: float) -> list[tuple[str, float, float]]:
    """Split one line into cells on the wide gaps between words."""
    threshold = max(MIN_GUTTER, CELL_GAP_FACTOR * char_width)
    cells: list[tuple[str, float, float]] = []
    buffer: list[Word] = []
    for word in line.words:
        if buffer and word.x0 - buffer[-1].x1 > threshold:
            cells.append(
                (" ".join(w.text for w in buffer), buffer[0].x0, buffer[-1].x1)
            )
            buffer = []
        buffer.append(word)
    if buffer:
        cells.append((" ".join(w.text for w in buffer), buffer[0].x0, buffer[-1].x1))
    return cells


def column_bounds(lines: Sequence[Line], char_width: float) -> list[tuple[float, float]]:
    """Find columns as the spans between full-height whitespace gutters.

    A gutter is x-space that *every* row leaves blank.  This is what makes
    right-aligned money columns work: the header may sit left of its numbers,
    but the blank channel between columns is present on every line.
    """
    spans: list[list[float]] = []
    for line in lines:
        for word in line.words:
            spans.append([word.x0, word.x1])
    if not spans:
        return []

    spans.sort()
    gutter = max(MIN_GUTTER, char_width * 1.2)
    merged: list[list[float]] = [spans[0][:]]
    for start, end in spans[1:]:
        if start - merged[-1][1] < gutter:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _bounds_from_header(
    header_cells: Sequence[tuple[str, float, float]]
) -> list[tuple[float, float]]:
    """Fallback boundaries: the midpoint of each gap between header cells."""
    bounds: list[tuple[float, float]] = []
    for index, (_, x0, x1) in enumerate(header_cells):
        left = x0 if index == 0 else (header_cells[index - 1][2] + x0) / 2
        right = (
            x1
            if index == len(header_cells) - 1
            else (x1 + header_cells[index + 1][1]) / 2
        )
        bounds.append((left, right))
    return bounds


def assign_to_columns(
    line: Line, bounds: Sequence[tuple[float, float]]
) -> list[str]:
    """Place each word in the column whose span contains its midpoint."""
    buckets: list[list[Word]] = [[] for _ in bounds]
    for word in line.words:
        target = None
        for index, (start, end) in enumerate(bounds):
            if start - MIN_GUTTER <= word.middle <= end + MIN_GUTTER:
                target = index
                break
        if target is None:
            # Outside every column: attach to the nearest one so nothing is lost.
            target = min(
                range(len(bounds)),
                key=lambda i: min(
                    abs(word.middle - bounds[i][0]), abs(word.middle - bounds[i][1])
                ),
            )
        buckets[target].append(word)
    return [
        " ".join(word.text for word in sorted(bucket, key=lambda w: w.x0))
        for bucket in buckets
    ]


# --------------------------------------------------------------------------
# Table detection
# --------------------------------------------------------------------------


def _is_total_line(line: Line) -> bool:
    return bool(TOTAL_ROW_PATTERN.search(line.leading_text(3)))


def find_header_line(
    lines: Sequence[Line], char_width: float
) -> tuple[int, list[tuple[str, float, float]]] | None:
    """The line that names the most recognisable loss-run columns."""
    best: tuple[int, int, list[tuple[str, float, float]]] | None = None
    for line in lines:
        cells = split_cells(line, char_width)
        if len(cells) < 3:
            continue
        score = header_score([text for text, _, _ in cells])
        if score < 3:
            continue
        if best is None or score > best[1]:
            best = (line.index, score, cells)
    if best is None:
        return None
    return best[0], best[2]


def extract_page_table(page: pdfplumber.page.Page, page_number: int) -> RawTable | None:
    """Find the claims table on one page.

    Tries ruled-table detection first (spec order), then word clustering.
    """
    positioned = _extract_positioned_table(page, page_number)
    ruled = _extract_ruled_table(page, page_number)
    if ruled is None:
        return positioned

    # A ruled grid usually stops above the footer, leaving the printed totals
    # outside the detected table. R-04 depends on those totals, so borrow them
    # from the word pass when the two agree on the column count.
    if not ruled.total_rows and positioned is not None:
        if positioned.column_count == ruled.column_count:
            ruled.total_rows = positioned.total_rows
    return ruled


def _extract_ruled_table(
    page: pdfplumber.page.Page, page_number: int
) -> RawTable | None:
    """``pdfplumber.extract_tables()`` — only trusted when it finds a header."""
    try:
        tables = page.extract_tables()
    except Exception:  # pragma: no cover - pdfplumber edge cases
        return None

    for table in tables or []:
        rows = [
            [clean_text(cell) if cell else "" for cell in row]
            for row in table
            if row is not None
        ]
        header_index = next(
            (
                index
                for index, row in enumerate(rows)
                if looks_like_header(row)
            ),
            None,
        )
        if header_index is None:
            continue

        headers = rows[header_index]
        data_rows: list[RawRow] = []
        total_rows: list[RawRow] = []
        for offset, row in enumerate(rows[header_index + 1 :], start=1):
            padded = list(row) + [""] * (len(headers) - len(row))
            raw = RawRow(
                cells=padded[: len(headers)],
                page=page_number,
                line_index=header_index + offset,
            )
            if raw.is_blank():
                continue
            leading = " ".join(cell for cell in padded[:3] if cell)
            if TOTAL_ROW_PATTERN.search(leading):
                raw.kind = "total"
                total_rows.append(raw)
            else:
                data_rows.append(raw)

        if data_rows:
            return RawTable(
                page=page_number,
                headers=headers,
                rows=data_rows,
                total_rows=total_rows,
                strategy="ruled",
                header_line_index=header_index,
            )
    return None


def _extract_positioned_table(
    page: pdfplumber.page.Page, page_number: int
) -> RawTable | None:
    """Word-position clustering, for tables drawn without rules."""
    words = _to_words(page.extract_words(use_text_flow=False, keep_blank_chars=False))
    if not words:
        return None

    char_width = _median_char_width(words)
    lines = cluster_lines(words)
    found = find_header_line(lines, char_width)
    if found is None:
        return None
    header_index, header_cells = found

    body = [line for line in lines if line.index > header_index]
    data_lines = [
        line
        for line in body
        if not _is_total_line(line)
        and not looks_like_header([text for text, _, _ in split_cells(line, char_width)])
    ]

    # Column boundaries come from the data rows: the header may be narrower
    # than its column, and total rows often overflow their first cell.
    bounds = column_bounds(data_lines, char_width)
    if len(bounds) != len(header_cells):
        bounds = _bounds_from_header(header_cells)

    headers = assign_to_columns(
        Line(
            words=tuple(
                Word(text=text, x0=x0, x1=x1, top=0.0, bottom=1.0)
                for text, x0, x1 in header_cells
            ),
            index=header_index,
        ),
        bounds,
    )

    rows: list[RawRow] = []
    total_rows: list[RawRow] = []
    for line in body:
        cells = assign_to_columns(line, bounds)
        row = RawRow(
            cells=cells,
            page=page_number,
            line_index=line.index,
            bbox=line.bbox,
            kind="total" if _is_total_line(line) else "data",
        )
        if row.is_blank():
            continue
        if row.kind == "total":
            total_rows.append(row)
        elif looks_like_header(cells):
            continue  # a header repeated mid-page
        else:
            rows.append(row)

    if not rows and not total_rows:
        return None

    return RawTable(
        page=page_number,
        headers=headers,
        rows=rows,
        total_rows=total_rows,
        strategy="words",
        header_line_index=header_index,
        column_bounds=list(bounds),
    )


# --------------------------------------------------------------------------
# Document metadata
# --------------------------------------------------------------------------


@dataclass
class DocumentMetadata:
    """Header-block facts, still as text — stage 4 parses them."""

    carrier: str | None = None
    named_insured: str | None = None
    policy_number: str | None = None
    valuation_date_text: str | None = None
    policy_period_start_text: str | None = None
    policy_period_end_text: str | None = None
    line_of_business: str | None = None
    currency: str | None = None
    printed_claim_count: int | None = None


_VALUATION_PATTERNS = (
    r"valuation\s*date\s*[:\-]?\s*(.+)",
    r"valued\s*(?:as\s*of|through)\s*[:\-]?\s*(.+)",
    r"evaluation\s*date\s*[:\-]?\s*(.+)",
    r"as\s*of\s*date\s*[:\-]?\s*(.+)",
    r"loss(?:es)?\s*valued\s*[:\-]?\s*(.+)",
)

_PERIOD_PATTERN = re.compile(
    r"policy\s*(?:period|term|dates?)\s*[:\-]?\s*(.+?)\s*(?:to|through|thru|[-–])\s*(\S+)",
    re.IGNORECASE,
)

_COUNT_PATTERNS = (
    r"(?:total|number\s*of|count\s*of)\s*(?:claims?|records?|rows?)\s*[:\-]?\s*(\d[\d,]*)",
    r"(\d[\d,]*)\s*(?:claims?|records?)\s*(?:shown|listed|reported|total)",
)


def _first_match(text: str, patterns: Iterable[str]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return clean_text(match.group(1))
    return None


def _labelled_value(text: str, *labels: str) -> str | None:
    for label in labels:
        match = re.search(rf"{label}\s*[:\-]\s*(.+)", text, flags=re.IGNORECASE)
        if match:
            value = clean_text(match.group(1))
            # The header block prints two labelled values per line.
            value = re.split(
                r"\s{2,}|\s+(?=(?:policy|valuation|currency|line\s+of|named)\b)",
                value,
                flags=re.IGNORECASE,
            )[0]
            if value:
                return clean_text(value)
    return None


def extract_metadata(page_text: str) -> DocumentMetadata:
    """Pull the letterhead facts out of a page's text."""
    from core.profiles import detect_carrier

    period = _PERIOD_PATTERN.search(page_text)
    count_text = _first_match(page_text, _COUNT_PATTERNS)

    return DocumentMetadata(
        carrier=detect_carrier(page_text),
        named_insured=_labelled_value(page_text, "named insured", "insured name", "insured"),
        policy_number=_labelled_value(page_text, "policy number", "policy no", "policy #"),
        valuation_date_text=_first_match(page_text, _VALUATION_PATTERNS),
        policy_period_start_text=clean_text(period.group(1)) if period else None,
        policy_period_end_text=clean_text(period.group(2)) if period else None,
        line_of_business=_labelled_value(page_text, "line of business", "lob", "coverage type"),
        currency=_labelled_value(page_text, "currency"),
        printed_claim_count=parse_int(count_text) if count_text else None,
    )


# --------------------------------------------------------------------------
# Whole-document entry point
# --------------------------------------------------------------------------


@dataclass
class DigitalExtraction:
    tables: list[RawTable]
    metadata: DocumentMetadata
    page_texts: dict[int, str]
    page_count: int

    @property
    def all_rows(self) -> list[RawRow]:
        return [row for table in self.tables for row in table.rows]

    def numeric_tokens(self) -> list[str]:
        """Every cell that might be a number — used for locale inference."""
        tokens: list[str] = []
        for table in self.tables:
            for row in list(table.rows) + list(table.total_rows):
                tokens.extend(cell for cell in row.cells if any(c.isdigit() for c in cell))
        return tokens


def extract_pdf(
    path: str | Path, pages: Sequence[int] | None = None
) -> DigitalExtraction:
    """Extract every digital page of a PDF.

    ``pages`` limits extraction to specific 1-based page numbers, which is how
    a mixed document skips the pages that have to go to vision.
    """
    tables: list[RawTable] = []
    page_texts: dict[int, str] = {}

    with pdfplumber.open(path) as pdf:
        page_count = len(pdf.pages)
        for index, page in enumerate(pdf.pages, start=1):
            if pages is not None and index not in pages:
                continue
            page_texts[index] = page.extract_text() or ""
            table = extract_page_table(page, index)
            if table is not None:
                tables.append(table)

    first_text = page_texts.get(min(page_texts), "") if page_texts else ""
    metadata = extract_metadata(first_text)
    if metadata.printed_claim_count is None:
        # The claim count is usually printed under the totals on the last page.
        for _, text in sorted(page_texts.items(), reverse=True):
            found = extract_metadata(text).printed_claim_count
            if found is not None:
                metadata.printed_claim_count = found
                break

    return DigitalExtraction(
        tables=tables,
        metadata=metadata,
        page_texts=page_texts,
        page_count=page_count,
    )
