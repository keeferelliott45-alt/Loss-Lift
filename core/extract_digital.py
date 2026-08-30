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
from core.profiles import guess_field, header_score, looks_like_header
from core.records import (
    DATE_SHAPED,
    RecordLayout,
    consensus_shapes,
    detect_layout,
    group_records,
    is_identifier_candidate,
    leading_identifier,
    push_qualifiers,
)
from core.schema import DATE_FIELDS, MONEY_FIELDS, RawRow, RawTable

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

#: The blank channel that separates two columns, as a multiple of character
#: width. Narrower than CELL_GAP_FACTOR because a column boundary survives on
#: every line of the table, whereas words inside one cell only sometimes part.
COLUMN_GUTTER_FACTOR = 1.2

#: A line that says how many claims a section holds. Carriers print it beside
#: the section's amounts, directly under the last claim, which makes it the
#: line most likely to be mistaken for part of that claim.
CLAIM_COUNT_LABEL = re.compile(
    r"\b(?:claims?|losses|loss)\s*(?:count|cnt)\b|\bcount\s+of\s+(?:claims?|losses)",
    re.IGNORECASE,
)


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


def split_words(
    line: Line, char_width: float, gap_factor: float = CELL_GAP_FACTOR
) -> list[list[Word]]:
    """Group one line's words into cells on the wide gaps between them."""
    threshold = max(MIN_GUTTER, gap_factor * char_width)
    cells: list[list[Word]] = []
    buffer: list[Word] = []
    for word in line.words:
        if buffer and word.x0 - buffer[-1].x1 > threshold:
            cells.append(buffer)
            buffer = []
        buffer.append(word)
    if buffer:
        cells.append(buffer)
    return cells


def split_cells(
    line: Line, char_width: float, gap_factor: float = CELL_GAP_FACTOR
) -> list[tuple[str, float, float]]:
    """Split one line into cells on the wide gaps between words."""
    return [
        (" ".join(word.text for word in cell), cell[0].x0, cell[-1].x1)
        for cell in split_words(line, char_width, gap_factor)
    ]


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
    gutter = max(MIN_GUTTER, char_width * COLUMN_GUTTER_FACTOR)
    merged: list[list[float]] = [spans[0][:]]
    for start, end in spans[1:]:
        if start - merged[-1][1] < gutter:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _word_spacing(rows: Sequence[Line], start: float, end: float, fallback: float) -> float:
    """The space this column sets between its own words.

    Measured inside the column: across a whole row the widest gaps are the ones
    between columns, and taking those for word spacing would rule out every
    boundary there is.
    """
    gaps = [
        follower.x0 - word.x1
        for line in rows
        for word, follower in zip(line.words, line.words[1:])
        if word.x0 < end and word.x1 > start
        and follower.x0 < end and follower.x1 > start
        and follower.x0 > word.x1
    ]
    return statistics.median(gaps) if gaps else fallback


def _blank_point(
    rows: Sequence[Line], low: float, high: float, spacing: float
) -> float | None:
    """Where inside this gap most rows leave a channel clear, if most of them do.

    A column boundary is a channel the rows keep clear. ``column_bounds``
    requires *every* row to keep it clear, which is right when nothing else
    says a boundary is there, and too strict when something does: one claimant
    name long enough to touch the currency beside it, or a footer printed at
    its own positions, closes a channel forty other rows agree on.

    The header has already said there is a boundary here. This asks the rows
    where it is, and takes their answer only if they agree on a channel wider
    than the space they set between their own words -- so a two-word heading
    over running prose, where the words tile the line and every channel is
    crossed by one of them, still yields nothing and no description is cut in
    half. The channel need not be as wide as a column gutter: the header has
    already supplied the evidence a gutter would otherwise have to carry alone.
    """
    edges = sorted(
        {low, high}
        | {
            value
            for line in rows
            for word in line.words
            if word.x0 < high and word.x1 > low
            for value in (max(word.x0, low), min(word.x1, high))
        }
    )
    best: float | None = None
    fewest = len(rows)
    for left, right in zip(edges, edges[1:]):
        if right - left <= spacing:
            continue
        crossing = sum(
            1
            for line in rows
            if any(word.x0 < right and word.x1 > left for word in line.words)
        )
        if crossing < fewest:
            best, fewest = (left + right) / 2, crossing
    return best if best is not None and fewest * 2 < len(rows) else None


def subdivided(
    bounds: Sequence[tuple[float, float]],
    block: Sequence[Line],
    data_lines: Sequence[Line],
    char_width: float,
) -> list[tuple[float, float]]:
    """Split a detected column that the header names more than once.

    Two independent sources have to agree before a column is cut: the header
    puts two labels over it with clear space between them, and the column's own
    rows leave a point in that space blank. Either alone would be wrong --
    a centred two-word heading is not two columns, and rows leave gaps between
    ordinary words all the time.
    """
    gutter = max(MIN_GUTTER, char_width * COLUMN_GUTTER_FACTOR)
    labels = [
        cell
        for line in block
        for cell in split_cells(line, char_width, COLUMN_GUTTER_FACTOR)
    ]
    result: list[tuple[float, float]] = []
    for start, end in bounds:
        over = sorted(
            (cell for cell in labels if min(cell[2], end) - max(cell[1], start) > 0),
            key=lambda cell: cell[1],
        )
        rows = [
            line
            for line in data_lines
            if any(word.x0 < end and word.x1 > start for word in line.words)
        ]
        spacing = _word_spacing(rows, start, end, gutter)
        cuts: list[float] = []
        for left, right in zip(over, over[1:]):
            if right[1] - left[2] < gutter:
                continue  # one label wrapped, not two labels
            point = _blank_point(
                rows, max(left[2], start), min(right[1], end), spacing
            )
            if point is not None and start < point < end:
                cuts.append(point)
        edges = [start, *sorted(cuts), end]
        result.extend(zip(edges, edges[1:]))
    return result


def label_bounds(
    block: Sequence[Line], bounds: Sequence[tuple[float, float]], char_width: float
) -> list[str]:
    """Name each column from every line of the header block.

    A header word that falls between two columns belongs to whichever its own
    label belongs to. "Payment Indemnity" sits left of the amounts it heads,
    as right-aligned money always does, and its first word lands nearer the
    column before -- so following the word alone splits the label in two and
    leaves both halves meaning nothing.
    """
    parts: list[list[str]] = [[] for _ in bounds]
    for line in block:
        placed: list[list[Word]] = [[] for _ in bounds]
        for cell in split_words(line, char_width, COLUMN_GUTTER_FACTOR):
            span = (cell[0].x0, cell[-1].x1)
            over = [
                index
                for index, (start, end) in enumerate(bounds)
                if min(span[1], end) - max(span[0], start) > 0
            ]
            for word in cell:
                target = next(
                    (
                        index
                        for index, (start, end) in enumerate(bounds)
                        if start - MIN_GUTTER <= word.middle <= end + MIN_GUTTER
                    ),
                    None,
                )
                if target is None:
                    target = (
                        over[0]
                        if len(over) == 1
                        else min(
                            range(len(bounds)),
                            key=lambda i: min(
                                abs(word.middle - bounds[i][0]),
                                abs(word.middle - bounds[i][1]),
                            ),
                        )
                    )
                placed[target].append(word)
        for index, words in enumerate(placed):
            if words:
                parts[index].append(
                    " ".join(w.text for w in sorted(words, key=lambda w: w.x0))
                )
    return [" ".join(group) for group in parts]

def _clusters_cut(
    bounds: Sequence[tuple[float, float]], clusters: Sequence[tuple[float, float]]
) -> int:
    """How many runs of text on the page this set of boundaries cuts through.

    The rows themselves say where the text runs together. A candidate boundary
    landing inside one of those runs is claiming a column edge where the page
    shows none, and every value in that column arrives in two pieces.
    """
    edges = {edge for bound in bounds for edge in bound}
    return sum(
        1
        for start, end in clusters
        if any(start < edge < end for edge in edges)
    )


def _detached_footer_removed(lines: Sequence[Line]) -> list[Line]:
    """Drop the strapline printed at the foot of the page.

    A footer is set at its own positions and owes the table nothing: its words
    run across the channel between two columns and close it for every row above.
    What marks it is the space above it — a table sets its rows at one pitch,
    and the footer sits many times that below the last of them. Only trailing
    lines are dropped, so a subtotal printed directly under the claims, which
    is part of the table and shares its columns, stays.
    """
    if len(lines) < 3:
        return list(lines)
    tops = [min(word.top for word in line.words) for line in lines]
    gaps = [tops[i + 1] - tops[i] for i in range(len(tops) - 1)]
    limit = 2 * statistics.median(gaps)
    end = len(lines)
    while end > 2 and gaps[end - 2] > limit:
        end -= 1
    return list(lines[:end])


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


# --------------------------------------------------------------------------
# Claims printed across several lines
# --------------------------------------------------------------------------


def _is_label_line(line: Line | None) -> bool:
    """A line that could be part of a header block: words, and no figures.

    Every claim row carries a date, an amount or an identifier, so a digit is
    what separates a line of labels from the table beneath it.
    """
    if line is None or not line.words:
        return False
    return not any(char.isdigit() for word in line.words for char in word.text)


def header_block(
    lines: Sequence[Line], header_index: int, char_width: float
) -> list[Line]:
    """The run of label lines ending at the line that scored as the header.

    A carrier that prints a claim over three lines heads it with three lines of
    labels, one per record line. Walking up from the header collects them:
    label lines carry no digits, and each sits one line-height above the next.
    The walk stops at the first line that fails either test, which is what
    keeps the policy heading and the valuation date out of the block.
    """
    header = next((line for line in lines if line.index == header_index), None)
    if header is None or not header.words:
        return []

    height = max(word.bottom - word.top for word in header.words)
    pitch: list[float] = []

    def joins(gap: float) -> bool:
        """Whether a line that close belongs to the block.

        The first line has only the type size to go on. After that the block
        has shown its own spacing, and that is the better measure: a header
        block sets its lines at one pitch, and a short line in the middle of
        one -- Liberty prints "Applied Recovery" alone -- opens a gap wider
        than the type but nothing like the space before the table itself.
        """
        limit = 2 * (statistics.median(pitch) if pitch else height)
        return 0 < gap <= limit

    block = [header]
    for index in range(header_index - 1, -1, -1):
        above = next((line for line in lines if line.index == index), None)
        if not _is_label_line(above):
            break
        gap = min(word.top for word in block[0].words) - min(
            word.top for word in above.words
        )
        if not joins(gap):
            break
        block.insert(0, above)
        pitch.append(gap)

    # A label can wrap downwards just as readily: "Trigger Date" over "Claim
    # Status", "Gross Reserve" over "Indemnity". Only one line scores as the
    # header, so without this the rest is left to be read as claims -- and
    # worse, its words close the very gutters between the columns it names.
    last = next(index for index, line in enumerate(lines) if line is header)
    for below in lines[last + 1 :]:
        if not _is_label_line(below):
            break
        gap = min(word.top for word in below.words) - min(
            word.top for word in block[-1].words
        )
        if not joins(gap):
            break
        block.append(below)
        pitch.append(gap)
    return block


def _ends_a_record(line: Line, cells: Sequence[tuple[str, float, float]]) -> bool:
    """Whether this line closes whatever came before it, rather than continuing it.

    Section totals, claim counts, repeated headers and printed metadata all sit
    flush against the claims around them, and a record that reached across one
    would take its money for a claim's own. A label followed by a colon marks
    the metadata: "Policy :", "Pol-Asco-Mod:". No claim number contains one.
    """
    if _is_total_line(line) or CLAIM_COUNT_LABEL.search(line.text):
        return True
    if looks_like_header([text for text, _, _ in cells]):
        return True
    label, separator, _ = (cells[0][0] if cells else "").partition(":")
    return bool(separator) and bool(label) and label.replace(" ", "").replace(
        "-", ""
    ).isalpha()


def _pair_labels(
    header_cells: Sequence[tuple[str, float, float]],
    bounds: Sequence[tuple[float, float]],
) -> list[str]:
    """Name each detected column, and leave it unnamed rather than guess.

    Money prints right-aligned under a left-aligned label, so the label and its
    own values need not overlap at all; matching purely on geometry puts every
    amount one column to the left of where it belongs. Matching purely on order
    is worse when a column is missing. So order is used, and only where it
    agrees with geometry everywhere geometry has an opinion — a label that
    overlaps a column must be that column's. Where the two disagree, only the
    columns a label demonstrably sits over are named, and the rest carry no
    label and so assert nothing.
    """

    def over(cell: tuple[str, float, float]) -> list[int]:
        return [
            index
            for index, (start, end) in enumerate(bounds)
            if min(cell[2], end) - max(cell[1], start) > 0
        ]

    if len(header_cells) == len(bounds) and all(
        not (hit := over(cell)) or index in hit
        for index, cell in enumerate(header_cells)
    ):
        return [text for text, _, _ in header_cells]

    labels: list[str] = []
    for start, end in bounds:
        sitting = [
            text
            for text, x0, x1 in header_cells
            if min(x1, end) - max(x0, start) > 0
        ]
        labels.append(sitting[0] if len(sitting) == 1 else "")
    return labels


#: A printed amount: digits, separators, and the punctuation carriers wrap them
#: in. Enough to tell an amount from a date or a word, which is all it is for.
MONEY_SHAPED = re.compile(r"[($€£]?-?[\d,. ]*\d[\d,. ]*[)%\-]?$")


def _agrees(value: str, label: str) -> bool:
    """Whether this cell holds the kind of thing its column claims to hold."""
    field_name = guess_field(label).field
    if field_name in MONEY_FIELDS:
        return bool(MONEY_SHAPED.fullmatch(value)) and not DATE_SHAPED.fullmatch(value)
    if field_name in DATE_FIELDS:
        return bool(DATE_SHAPED.fullmatch(value))
    if field_name == "claim_number":
        return is_identifier_candidate(value)
    return False


def _fitting_slice(
    line: Line, slices: Sequence[tuple[list[tuple[float, float]], list[str]]]
) -> int:
    """Which record line's columns this stray line was printed under.

    A leftover line is one no record could claim: a section total, or the
    remains of a record whose membership could not be established. Its columns
    still have to be named, and the three sets of columns on the page overlap,
    so position alone often fits all of them equally. What separates them is
    meaning — an amount under a column labelled for amounts, an identifier
    under the one labelled for claim numbers — with position settling the rest.
    Naming a stray line's columns wrongly is how a claim disappears into the
    description of the one above it.
    """

    def score(index: int) -> tuple[int, int]:
        bounds, labels = slices[index]
        cells = assign_to_columns(line, bounds)
        return (
            sum(
                1
                for value, label in zip(cells, labels)
                if value.strip() and _agrees(value.strip(), label)
            ),
            sum(
                1
                for word in line.words
                for start, end in bounds
                if start <= word.middle <= end
            ),
        )

    return max(range(len(slices)), key=score)


def _extract_record_table(
    page_number: int,
    lines: Sequence[Line],
    header_index: int,
    layout: RecordLayout,
    char_width: float,
) -> RawTable | None:
    """Read a page whose claims each occupy several printed lines.

    The header block has already said how tall a record is and which of its
    lines names the claim. Here the body supplies the anchors: every line
    carrying a claim number of this document's own shape. A record is the fixed
    span of lines around one anchor and nothing else, so a section total under
    the last claim is never reached for, whatever it prints.

    Returns None when the body does not bear the shape out, leaving the caller
    to read the page one line at a time — which yields claims with null dates
    and amounts, and a document that says so.
    """
    body = [line for line in lines if line.index > header_index and line.words]
    if not body or layout.identifier_span is None:
        return None
    span = layout.identifier_span
    cells = {line.index: split_cells(line, char_width, COLUMN_GUTTER_FACTOR) for line in body}

    def identifier_cell(line: Line) -> str:
        for text, x0, x1 in cells[line.index]:
            if min(x1, span[1]) - max(x0, span[0]) > 0:
                return text
        return ""

    shapes = consensus_shapes(
        [
            text
            for line in body
            for text in (identifier_cell(line),)
            if text and is_identifier_candidate(text)
        ]
    )
    grouping = group_records(
        [min(word.top for word in line.words) for line in body],
        [leading_identifier(identifier_cell(line), shapes) is not None for line in body],
        [_ends_a_record(line, cells[line.index]) for line in body],
        layout,
    )
    if grouping is None:
        return None

    # Each record line has its own columns, and they differ: the claimant line
    # is four wide where the money line is ten. Both ways of finding them fail
    # differently -- whitespace gutters merge two columns when one bleeds into
    # the next, header gaps sit left of right-aligned money -- so each line
    # takes whichever names more of its columns, and the data's own gutters
    # settle a tie, since they are where the values actually are.
    members_by_line = [
        [body[record[position]] for record in grouping.records]
        for position in range(layout.height)
    ]
    # A label with nothing printed beneath it on its own record line is heading
    # the line below -- AIG's three bare "Total"s sit on the identifier line and
    # name the amounts a line lower. Moved down before the columns are read, so
    # they cannot claim columns the line does not have.
    line_headers = push_qualifiers(
        layout.line_headers,
        [column_bounds(members, char_width) for members in members_by_line],
    )

    slices: list[tuple[list[tuple[float, float]], list[str]]] = []
    for position in range(layout.height):
        members = members_by_line[position]
        header_cells = line_headers[position]
        best: tuple[int, list[tuple[float, float]], list[str]] | None = None
        for bounds in (
            column_bounds(members, char_width),
            _bounds_from_header(header_cells),
        ):
            if not bounds:
                continue
            labels = _pair_labels(header_cells, bounds)
            named = sum(1 for label in labels if label)
            if best is None or named > best[0]:
                best = (named, list(bounds), labels)
        if best is None:
            return None
        slices.append((best[1], best[2]))

    # A record table that cannot say which column holds the claim number has
    # not read the page, whatever else it recovered. Reading line by line at
    # least reports the claims it can see, so hand the page back.
    if not any(
        guess_field(label).field == "claim_number"
        for _, labels in slices
        for label in labels
    ):
        return None

    offsets = []
    running = 0
    for bounds, _ in slices:
        offsets.append(running)
        running += len(bounds)
    width = running

    def placed(line: Line, position: int) -> list[str]:
        """The line's words in their own slice of the record's columns."""
        row = [""] * width
        assigned = assign_to_columns(line, slices[position][0])
        row[offsets[position] : offsets[position] + len(assigned)] = assigned
        return row

    rows: list[RawRow] = []
    for record in grouping.records:
        members = [body[index] for index in record]
        merged = [""] * width
        for position, line in enumerate(members):
            for column, value in enumerate(placed(line, position)):
                if value:
                    merged[column] = value
        boxes = [line.bbox for line in members]
        rows.append(
            RawRow(
                cells=merged,
                page=page_number,
                line_index=members[layout.identifier_line or 0].index,
                source_lines=[line.index for line in members],
                bbox=(
                    min(box[0] for box in boxes),
                    min(box[1] for box in boxes),
                    max(box[2] for box in boxes),
                    max(box[3] for box in boxes),
                ),
                kind="data",
            )
        )

    total_rows: list[RawRow] = []
    for index in grouping.ungrouped:
        line = body[index]
        if looks_like_header([text for text, _, _ in cells[line.index]]):
            continue  # a header repeated mid-page
        row = RawRow(
            cells=placed(line, _fitting_slice(line, slices)),
            page=page_number,
            line_index=line.index,
            bbox=line.bbox,
            kind="total" if _is_total_line(line) else "data",
        )
        if row.is_blank():
            continue
        (total_rows if row.kind == "total" else rows).append(row)

    rows.sort(key=lambda row: row.line_index)
    return RawTable(
        page=page_number,
        headers=[label for _, labels in slices for label in labels],
        rows=rows,
        total_rows=total_rows,
        strategy="records",
        header_line_index=header_index,
        column_bounds=[bound for bounds, _ in slices for bound in bounds],
    )

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

    # Some carriers spend three printed lines on one claim, under three lines
    # of labels. Read singly those pages yield claims with nothing but a number
    # in them, so the block is asked first whether it describes a record; only
    # a block that says so, and a body that bears it out, takes this path.
    block = header_block(lines, header_index, char_width)
    layout = detect_layout(
        [split_cells(line, char_width, COLUMN_GUTTER_FACTOR) for line in block]
    )
    if layout.is_multi_line:
        reconstructed = _extract_record_table(
            page_number, lines, header_index, layout, char_width
        )
        if reconstructed is not None:
            return reconstructed

    # The table starts below the whole block, not below the one line that
    # scored as the header. A second line of labels left in the body is read
    # as a claim, and worse, its words close the gutters between the columns
    # it is naming -- so the table loses the boundaries its own header drew.
    block_end = max(line.index for line in block)
    body = [line for line in lines if line.index > block_end]
    # A strapline set well below the last row is not part of the table: not a
    # claim, and not a source of column boundaries either, since its words run
    # wherever the designer put them.
    attached = _detached_footer_removed(body)
    footer = {line.index for line in body if line not in attached}
    body = [line for line in body if line.index not in footer or _is_total_line(line)]
    data_lines = [
        line
        for line in body
        if not _is_total_line(line)
        and not looks_like_header(
            [text for text, _, _ in split_cells(line, char_width)]
        )
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

    # Three ways to find the columns, each with its own failure mode, and the
    # one that names the most of them wins. The third reads the whole header
    # block against the data's own gutters and splits a column the block names
    # twice, which is what recovers a table whose headings wrap downwards or
    # whose columns were closed by one overlong cell.
    gutters = column_bounds(data_lines, char_width)
    # Reading the whole block as one row of labels is right only when that is
    # what it is. Where the block describes a record line each -- Liberty gives
    # a claim seven lines and heads them with seven -- joining them per column
    # produces labels like "Incurred Indemnity Paid Indemnity Indemnity O/R"
    # that name nothing. The body has already refused that shape by the time
    # this runs, but the block still says what it is.
    candidates: list[tuple[list[tuple[float, float]], bool]] = [
        (gutters, False),
        (_bounds_from_header(header_cells), False),
    ]
    if not layout.is_multi_line:
        candidates.append((subdivided(gutters, block, data_lines, char_width), True))

    best: tuple[tuple[int, int], list[tuple[float, float]], list[str]] | None = None
    for candidate, labels in candidates:
        if len(candidate) < 2:
            continue
        named = (
            label_bounds(block, candidate, char_width)
            if labels
            else assign_to_columns(header_line, candidate)
        )
        # Where two readings name the same number of columns, the better one
        # is the one that recovers more of the schema: two columns both read
        # as "Gross Reserve" name one field between them, where "Gross Reserve
        # Indemnity" and "Payment Indemnity" name two.
        fields = {guess_field(label).field for label in named}
        fields.discard(None)
        # A boundary falling inside a run of text the rows never break is not a
        # boundary. "Accident" and "Description" set a hundred points apart head
        # one column of prose, and taking the gap between them for a column edge
        # cuts every description in half.
        # A subdivided candidate cuts a run of text on purpose, and proved each
        # cut against the header and the rows before making it. The other two
        # are cutting on geometry alone, so only they are charged for it.
        cuts = 0 if labels else _clusters_cut(candidate, gutters)
        score = (header_score(named), len(fields), -cuts, len(candidate))
        if best is None or score > best[0]:
            best = (score, list(candidate), named)

    if best is None:
        return None
    bounds, headers = best[1], best[2]

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
    # A report titled with the date it was struck: "Loss Run as per 13 Sep 2016".
    r"loss\s*runs?\s*(?:as\s*(?:per|of|at)|through|thru)\s*[:\-]?\s*(.+)",
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
