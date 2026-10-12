"""Stage 2 — worksheet extraction (spec section 5, Stage F intake).

Read a staged XLSX or CSV upload into the same shape the PDF readers produce:
one table per visible, non-empty worksheet, headers plus a grid of cells as
text, with the row each value was read from kept beside it.

Rules this module exists to enforce:

- **Never combined.** Each visible, non-empty worksheet is a separate source.
  Two sheets are two tables and, downstream, two logical runs. Nothing here
  merges, aligns or compares them.
- **Formulas are never executed.** openpyxl is asked for stored values only
  (``data_only=True``). A formula cell with a stored result reads that
  result, exactly as it is cached in the file. A formula cell with none --
  openpyxl wrote no cache and no spreadsheet engine has run -- is recorded
  as a formula gap: the cell's value is unknown, not zero, and the caller
  fails closed on it.
- **Hidden and empty sheets are recorded, not silently dropped.** A hidden
  worksheet is skipped but named; a visible worksheet holding no cells is
  skipped but named. Neither may contribute a claim, and neither may be read
  as "no claims, therefore clean".
- **Headers are found, never guessed.** The header row is the first row that
  names recognisable loss-run columns. A sheet with none is reported as such
  and produces no claims.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from core.profiles import header_score

#: What openpyxl reports for a formula cell read without ``data_only``.
_ARRAY_FORMULA = "ArrayFormula"


@dataclass(frozen=True)
class CellGap:
    """A formula cell with no stored result: the one reading that failed."""

    row: int  # 0-based index of the row inside the worksheet
    column: int  # 0-based index of the column inside the worksheet
    formula: str


@dataclass
class SheetSource:
    """One visible, non-empty worksheet, read as text."""

    name: str
    page: int  # 1-based source page assigned to this sheet, in workbook order
    headers: list[str]
    #: Data rows below the header, as text. The outer index is the row's
    #: 0-based position in the worksheet, so provenance points at the real
    #: spreadsheet row even when rows above the header were skipped.
    rows: list[tuple[int, list[str]]]
    total_rows: list[tuple[int, list[str]]]
    header_row: int  # 0-based index of the header row inside the worksheet
    #: Formula cells whose stored value is missing -- read as unknown.
    formula_gaps: list[CellGap] = field(default_factory=list)


@dataclass
class SheetExtraction:
    """Everything the pipeline routes on, per staged workbook."""

    sources: list[SheetSource] = field(default_factory=list)
    #: Worksheets that produced no source, named with why: "hidden" or "empty".
    ignored_sheets: dict[str, str] = field(default_factory=dict)
    #: The source page each ignored worksheet occupies, in workbook order. A
    #: worksheet exists in the file whether or not it was read, so it keeps a
    #: page: a hidden sheet's page is unreads source content, and page
    #: accounting (R-22) names it.
    ignored_sheet_pages: dict[str, int] = field(default_factory=dict)
    #: Text printed above the header of the first source, for carrier detection.
    title_text: str = ""
    #: Header defects seen and refused: blank cells, repeated labels, none.
    header_issues: list[str] = field(default_factory=list)


def extract_workbook(path: str | Path) -> SheetExtraction:
    """Read a staged .xlsx file: one source per visible, non-empty worksheet.

    The workbook is opened twice, both times read-only: once for stored
    values (``data_only=True``) and once for formulas (``data_only=False``).
    Neither pass evaluates anything; the second exists only so a formula
    without a stored result can be named instead of read as an empty cell.
    """
    extraction = SheetExtraction()
    values_book = load_workbook(path, read_only=True, data_only=True)
    try:
        formula_book = load_workbook(path, read_only=True, data_only=False)
        try:
            # Every worksheet occupies a page, in workbook order -- read or
            # not. A page nothing read is unaccounted source content unless
            # the pipeline records why, which it does from these numbers.
            page = 0
            for name in values_book.sheetnames:
                page += 1
                state = values_book[name].sheet_state
                if state != "visible":
                    extraction.ignored_sheets[name] = "hidden"
                    extraction.ignored_sheet_pages[name] = page
                    continue
                source = _read_sheet(
                    name,
                    values_book[name],
                    formula_book[name],
                )
                if source is None:
                    extraction.ignored_sheets[name] = "empty"
                    extraction.ignored_sheet_pages[name] = page
                    continue
                source.page = page
                extraction.sources.append(source)
                if not extraction.title_text and source.header_row > 0:
                    _collect_title(values_book[name], source.header_row, extraction)
        finally:
            formula_book.close()
    finally:
        values_book.close()
    _flag_headers(extraction)
    return extraction


def extract_csv(path: str | Path, name: str | None = None) -> SheetExtraction:
    """Read a staged delimited-text file as one source.

    The delimiter is whichever of the common ones the first populated line
    carries most of -- decided from the bytes, not guessed per column.
    """
    raw = Path(path).read_bytes()
    text = raw.decode("utf-8-sig")
    first_line = next((line for line in text.splitlines() if line.strip()), "")
    delimiter = max(
        (",", ";", "\t", "|"),
        key=lambda candidate: first_line.count(candidate),
        default=",",
    )
    grid = [
        [("" if cell is None else str(cell)) for cell in row]
        for row in csv.reader(io.StringIO(text), delimiter=delimiter)
    ]
    extraction = SheetExtraction()
    label = name or Path(path).stem or "CSV"
    source = _sheet_from_grid(label, grid)
    if source is None:
        extraction.ignored_sheets[label] = "empty"
    else:
        source.page = 1
        extraction.sources.append(source)
    _flag_headers(extraction)
    return extraction


def _read_sheet(name: str, values: Any, formulas: Any) -> SheetSource | None:
    """One worksheet, as text. None when the sheet holds no cells at all."""
    value_grid = [
        list(row) for row in values.iter_rows(values_only=True)
    ]
    if not any(
        cell is not None and str(cell).strip()
        for row in value_grid
        for cell in row
    ):
        return None
    formula_grid = [
        list(row) for row in formulas.iter_rows(values_only=True)
    ]

    header_row = next(
        (index for index, row in enumerate(value_grid) if _is_header(row)),
        None,
    )
    source = SheetSource(
        name=name,
        page=0,
        headers=[],
        rows=[],
        total_rows=[],
        header_row=header_row if header_row is not None else len(value_grid),
    )
    if header_row is not None:
        source.headers = [_cell_text(cell) for cell in value_grid[header_row]]

    data_rows = value_grid[header_row + 1:] if header_row is not None else []
    formula_rows = formula_grid[header_row + 1:] if header_row is not None else []
    for offset, row in enumerate(data_rows):
        row_index = (header_row + 1 + offset) if header_row is not None else offset
        cells = [_cell_text(cell) for cell in row]
        if not any(cell.strip() for cell in cells):
            continue
        if _is_total_row(cells):
            source.total_rows.append((row_index, cells))
        else:
            source.rows.append((row_index, cells))
        _collect_gaps(
            row,
            formula_rows[offset] if offset < len(formula_rows) else [],
            row_index,
            source,
        )

    if header_row is None:
        # No header was found; keep the rows so nothing disappears, but the
        # caller sees a sheet that names no columns and can map none.
        source.rows = [
            (index, cells)
            for index, cells in enumerate(
                [_cell_text(cell) for cell in row] for row in value_grid
            )
            if any(cell.strip() for cell in cells)
        ]
        source.total_rows = []
    return source


def _collect_title(values: Any, header_row: int, extraction: SheetExtraction) -> None:
    """Lines printed above the header, for carrier and title detection."""
    lines: list[str] = []
    for index, row in enumerate(values.iter_rows(values_only=True)):
        if index >= header_row:
            break
        text = " ".join(
            _cell_text(cell) for cell in row if _cell_text(cell).strip()
        ).strip()
        if text:
            lines.append(text)
    extraction.title_text = "\n".join(lines)


def _collect_gaps(
    value_row: list[Any],
    formula_row: list[Any],
    row_index: int,
    source: SheetSource,
) -> None:
    """Record formula cells whose stored value is missing.

    A formula read without ``data_only`` comes back as its text ("=SUM(...)").
    Read with it, the cell holds the stored result -- or None when no
    spreadsheet engine ever stored one. That None is not an empty cell: the
    source carries a formula whose value is unknown, and it is recorded as a
    gap. Nothing is evaluated here, ever.
    """
    for column, cell in enumerate(formula_row):
        if not _is_formula(cell):
            continue
        cached = value_row[column] if column < len(value_row) else None
        if cached is None or (isinstance(cached, str) and not cached.strip()):
            source.formula_gaps.append(
                CellGap(row=row_index, column=column, formula=_formula_text(cell))
            )


def _is_formula(cell: Any) -> bool:
    if isinstance(cell, str):
        return cell.lstrip().startswith("=")
    return type(cell).__name__ == _ARRAY_FORMULA


def _formula_text(cell: Any) -> str:
    text = getattr(cell, "text", None)
    return text if isinstance(text, str) else str(cell)


def _is_header(row: list[Any]) -> bool:
    """A header row names recognisable loss-run columns.

    Two named columns are enough on a spreadsheet, where the grid has no
    typography to lean on: a title row carries none, and a data row carries
    at most a cell that happens to spell like a label. Requiring three would
    silently unmap the amount of a two-label-plus-one-unreadable header --
    exactly the header a spreadsheet that stopped being edited produces --
    and the unmapped column would be flagged, not guessed, either way.
    """
    cells = [_cell_text(cell) for cell in row]
    return header_score(cells) >= 2


def _is_total_row(cells: list[str]) -> bool:
    """Whether this row prints totals, by the same wording the PDF reader uses.

    Reusing the PDF pattern keeps the two readers agreeing on what a totals
    row is, so R-04 checks the same kind of row on either path.
    """
    from core.extract_digital import TOTAL_ROW_PATTERN, TRAILING_TOTAL_LABEL

    leading = " ".join(cell for cell in cells[:3] if cell.strip())
    if TOTAL_ROW_PATTERN.search(leading):
        return True
    trailing = " ".join(cell for cell in cells[:8] if cell.strip())
    return bool(TRAILING_TOTAL_LABEL.search(trailing))


def _cell_text(cell: Any) -> str:
    """One cell as deterministic text, preserving what is stored.

    Numbers become their plain spelling (never a float artefact), dates the
    ISO date, booleans TRUE/FALSE, and everything else its stored text. A
    formula whose value was never stored arrives as None from the values
    pass; the gap is recorded elsewhere, and here it is an empty cell.
    """
    if cell is None:
        return ""
    if isinstance(cell, bool):
        return "TRUE" if cell else "FALSE"
    if isinstance(cell, int):
        return str(cell)
    if isinstance(cell, float):
        if cell != cell or cell in (float("inf"), float("-inf")):
            return ""
        return str(int(cell)) if cell == int(cell) else repr(cell)
    if isinstance(cell, Decimal):
        return format(cell, "f")
    if isinstance(cell, datetime):
        if cell.time() == time(0, 0):
            return cell.date().isoformat()
        return cell.isoformat()
    if isinstance(cell, date):
        return cell.isoformat()
    if isinstance(cell, time):
        return cell.isoformat()
    return str(cell)


def _sheet_from_grid(name: str, grid: list[list[str]]) -> SheetSource | None:
    """Build one source from a text grid (the CSV path)."""
    if not any(any(cell.strip() for cell in row) for row in grid):
        return None
    header_row = next(
        (index for index, row in enumerate(grid) if _is_header(row)),
        None,
    )
    source = SheetSource(
        name=name,
        page=0,
        headers=[],
        rows=[],
        total_rows=[],
        header_row=header_row if header_row is not None else len(grid),
    )
    if header_row is not None:
        source.headers = list(grid[header_row])
    data = grid[header_row + 1:] if header_row is not None else []
    for offset, row in enumerate(data):
        row_index = (header_row + 1 + offset) if header_row is not None else offset
        if not any(cell.strip() for cell in row):
            continue
        if _is_total_row(row):
            source.total_rows.append((row_index, list(row)))
        else:
            source.rows.append((row_index, list(row)))
    if header_row is None:
        source.rows = [
            (index, list(row))
            for index, row in enumerate(grid)
            if any(cell.strip() for cell in row)
        ]
        source.total_rows = []
    return source


def _flag_headers(extraction: SheetExtraction) -> None:
    """Record header defects: no header, blank labels, repeated labels.

    Each is stated against the worksheet it was seen on. The mapping still
    runs on whatever labels exist -- an unrecognised or blank column is
    unmapped, and the rules report unmapped columns -- but the defect itself
    is named here so a reviewer sees why the column was refused.
    """
    for source in extraction.sources:
        if not source.headers or not any(label.strip() for label in source.headers):
            extraction.header_issues.append(
                f"Worksheet '{source.name}' has no recognisable header row; "
                "no columns could be mapped from it."
            )
            continue
        seen: dict[str, int] = {}
        for index, label in enumerate(source.headers, start=1):
            if not label.strip():
                extraction.header_issues.append(
                    f"Worksheet '{source.name}': column {index} has a blank "
                    "header cell; the column was not mapped."
                )
                continue
            key = " ".join(label.split()).lower()
            if key in seen:
                extraction.header_issues.append(
                    f"Worksheet '{source.name}': duplicate column label "
                    f"'{label.strip()}' in columns {seen[key]} and {index}; "
                    "only one of them was mapped."
                )
            else:
                seen[key] = index
