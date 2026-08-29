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
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Any, Iterable, Sequence

import pdfplumber

from core.normalize import clean_text, parse_int
from core.profiles import header_score, looks_like_header
from core.schema import RawRow, RawTable

#: A line whose leading words say "total" closes the table.
TOTAL_ROW_PATTERN = re.compile(r"\b(?:grand\s+)?(?:report\s+)?totals?\b", re.IGNORECASE)

#: Carriers that group claims by policy period label each subtotal with the
#: period first — "01/01/2018 - 12/31/2018 Totals:" — pushing the keyword past
#: the leading cell. A trailing "Total(s):" is specific enough to catch those
#: without matching a loss description that merely mentions a total.
TRAILING_TOTAL_LABEL = re.compile(r"\btotals?\s*:", re.IGNORECASE)

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
    if TOTAL_ROW_PATTERN.search(line.leading_text(3)):
        return True
    return bool(TRAILING_TOTAL_LABEL.search(line.leading_text(8)))


def _merge_total_row(label: RawRow, values: Sequence[str]) -> RawRow:
    """Fold a totals label and the amounts line beneath it into one row."""
    width = max(len(label.cells), len(values))
    merged = [
        (values[index] if index < len(values) and values[index] else "")
        or (label.cells[index] if index < len(label.cells) else "")
        for index in range(width)
    ]
    return RawRow(
        cells=merged,
        page=label.page,
        line_index=label.line_index,
        bbox=label.bbox,
        kind="total",
    )


def _money_token_count(line: Line) -> int:
    """Words that look like printed amounts rather than labels or dates."""
    return sum(
        1
        for word in line.words
        if any(char.isdigit() for char in word.text)
        and any(char in word.text for char in "$€£")
    )


def _merge_header_fragments(
    lines: Sequence[Line], header_index: int, char_width: float
) -> Line | None:
    """Fold a wrapped header's upper half into the line that scored as header.

    Loss runs routinely stack a column label across two lines — "Date of" above
    "Loss", "Outstanding" above "Reserves". Only the lower line scores as the
    header, so without this the labels arrive truncated and map to nothing.
    Returns None unless merging makes the header more recognisable, so a layout
    that simply has text above its header is left alone.
    """
    header = next((line for line in lines if line.index == header_index), None)
    above = next((line for line in lines if line.index == header_index - 1), None)
    if header is None or above is None or not above.words:
        return None
    if any(char.isdigit() for word in above.words for char in word.text):
        return None

    # Only a line sitting directly on top of the header can be its upper half.
    gap = header.words[0].top - above.words[0].top
    height = max(word.bottom - word.top for word in header.words)
    if not 0 < gap <= height * 2:
        return None

    merged = Line(
        words=tuple(sorted(above.words + header.words, key=lambda word: word.x0)),
        index=header_index,
    )
    # Accept a tie. Merging only ever adds the qualifying word a wrapped label
    # left on the line above -- "Loss"/"Occur"/"Closed" over three columns that
    # all read "Date" on their own. Those score the same as one bare "Date" but
    # are the difference between knowing which date is the date of loss and
    # not. A merge with a line that is not a header still loses: its words land
    # inside the real labels and the score drops, which this rejects.
    before = [text for text, _, _ in split_cells(header, char_width)]
    after = [text for text, _, _ in split_cells(merged, char_width)]
    return merged if header_score(after) >= header_score(before) else None


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
    # A ruled grid drawn around the header block alone is still a grid, and
    # pdfplumber returns it as a table: a header, no data, and the real rows
    # sitting below it outside the ruling. A table with nothing in it has not
    # found the table, so where the word pass sees rows and the grid does not,
    # the rows win.
    if len(ruled.rows) < 2 and positioned is not None and len(positioned.rows) > len(ruled.rows):
        return positioned

    if not ruled.total_rows and positioned is not None and positioned.total_rows:
        if positioned.column_count == ruled.column_count:
            ruled.total_rows = positioned.total_rows
        else:
            # The two passes disagree on how many columns there are, so the
            # totals cannot be transplanted by index without landing money in
            # the wrong field — a wrong total is worse than no total. The word
            # pass is at least internally consistent, and a table with no
            # printed totals cannot run R-04, the check that sells the product.
            return positioned
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

    # Two ways to find the columns, each with its own failure mode: whitespace
    # gutters miss a column that is blank in every data row, and header gaps
    # merge labels that sit close together — which right-aligned money headers
    # routinely do. Rather than trust either, take whichever one reads the
    # header row into more recognisable columns, since identifying the columns
    # is the whole job.
    # Assign the header's individual words, not the gap-split cells: splitting
    # first is what merged "Paid Total Recovery Total Incurred Total" into one
    # label, and re-using that merged blob would defeat the comparison below.
    wrapped = _merge_header_fragments(lines, header_index, char_width)
    if wrapped is not None:
        header_cells = split_cells(wrapped, char_width)
    header_line = wrapped or next(
        (line for line in lines if line.index == header_index),
        Line(
            words=tuple(
                Word(text=text, x0=x0, x1=x1, top=0.0, bottom=1.0)
                for text, x0, x1 in header_cells
            ),
            index=header_index,
        ),
    )

    best: tuple[int, int, list[tuple[float, float]], list[str]] | None = None
    for candidate in (
        column_bounds(data_lines, char_width),
        _bounds_from_header(header_cells),
    ):
        if len(candidate) < 2:
            continue
        labels = assign_to_columns(header_line, candidate)
        score = (header_score(labels), len(candidate))
        if best is None or score > best[0]:
            best = (score, len(candidate), list(candidate), labels)

    if best is None:
        return None
    bounds, headers = best[2], best[3]

    rows: list[RawRow] = []
    total_rows: list[RawRow] = []
    # A totals label does not always sit on the same line as its amounts: real
    # footers print "<period> Totals:" and drop the count and money onto the
    # next line. Fold that pair back into one row so the amounts stay attached
    # to the label that says which total they are.
    pending_label: RawRow | None = None
    for line in body:
        cells = assign_to_columns(line, bounds)
        is_total = _is_total_line(line)
        money_here = _money_token_count(line)

        if pending_label is not None:
            if not is_total and money_here >= 2:
                total_rows.append(_merge_total_row(pending_label, cells))
                pending_label = None
                continue
            total_rows.append(pending_label)
            pending_label = None

        row = RawRow(
            cells=cells,
            page=page_number,
            line_index=line.index,
            bbox=line.bbox,
            kind="total" if is_total else "data",
        )
        if row.is_blank():
            continue
        if row.kind == "total":
            if money_here < 2:
                pending_label = row  # its amounts are on the line below
            else:
                total_rows.append(row)
        elif looks_like_header(cells):
            continue  # a header repeated mid-page
        else:
            rows.append(row)
    if pending_label is not None:
        total_rows.append(pending_label)

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
    #: Every period declared anywhere in the document. A loss run grouped by
    #: policy period declares one per section, and the document covers them all.
    policy_periods: list[tuple[str, str]] = dataclass_field(default_factory=list)


#: Carriers phrase the valuation date many ways. "Report date" and "run date"
#: are deliberately absent: they say when the report was printed, which is
#: often not the date the values are stated as of. Reading one as the other
#: would put a wrong valuation date on a priced submission without anyone
#: noticing, and R-06 flagging a missing date is the safer failure.
_VALUATION_PATTERNS = (
    r"valuation\s*(?:date|dt)\s*[:\-]?\s*(.+)",
    r"val\s*date\s*[:\-]?\s*(.+)",
    r"valued\s*(?:as\s*of|through|thru)\s*[:\-]?\s*(.+)",
    r"evaluat(?:ion|ed)\s*(?:date|as\s*of)?\s*[:\-]?\s*(.+)",
    r"as\s*of\s*date\s*[:\-]?\s*(.+)",
    r"(?:data|values?|numbers?|amounts?)\s*as\s*of\s*[:\-]?\s*(.+)",
    r"loss(?:es)?\s*valued\s*[:\-]?\s*(.+)",
)

_PERIOD_PATTERN = re.compile(
    r"policy\s*(?:period|term|dates?)\s*[:\-]?\s*(.+?)\s*(?:to|through|thru|[-–])\s*(\S+)",
    re.IGNORECASE,
)

#: A grand-total label and the claim count beneath it, e.g. "Report Totals:"
#: on one line and "# Claims: 50" on the next.
GRAND_COUNT_PATTERN = re.compile(
    r"(?:grand|report|overall|final)\s+totals?\s*:?[\s#]*claims?\s*:?\s*(\d[\d,]*)",
    re.IGNORECASE,
)

_COUNT_PATTERNS = (
    r"(?:total|number\s*of|count\s*of)\s*(?:claims?|records?|rows?)\s*[:\-]?\s*(\d[\d,]*)",
    r"claims?\s*count\s*[:\-]?\s*(\d[\d,]*)",
    # Footer rows commonly read just "Claims: 2". The plural and the colon are
    # both required so a "Claim #: 12345" identifier is never read as a count.
    r"\bclaims\s*[:]\s*(\d[\d,]*)",
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

    # A document grouped by policy period prints a claim count per section as
    # well as one for the whole report. Page 1 carries the first section's
    # count, so the grand total has to win wherever it appears — otherwise R-05
    # compares every extracted claim against a single section's tally.
    grand_count = next(
        (
            parse_int(match.group(1))
            for _, text in sorted(page_texts.items(), reverse=True)
            if (match := GRAND_COUNT_PATTERN.search(text))
        ),
        None,
    )
    # A valuation date need not be printed on page 1. Some carriers state it
    # once, on whichever page the numbers begin. Missing it is a hard fail
    # (R-06), so it is worth looking at every page before concluding it is
    # absent -- the patterns are specific enough that a later page cannot
    # supply a false one.
    if metadata.valuation_date_text is None:
        for _, text in sorted(page_texts.items()):
            found = _first_match(text, _VALUATION_PATTERNS)
            if found:
                metadata.valuation_date_text = found
                break

    # Sections declare a period each, and only page 1's reached the metadata.
    # Collect them all so the document's span covers every claim it lists.
    seen: set[tuple[str, str]] = set()
    for _, text in sorted(page_texts.items()):
        for match in _PERIOD_PATTERN.finditer(text):
            period = (clean_text(match.group(1)), clean_text(match.group(2)))
            if period not in seen:
                seen.add(period)
                metadata.policy_periods.append(period)

    if grand_count is not None:
        metadata.printed_claim_count = grand_count
    elif metadata.printed_claim_count is None:
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
