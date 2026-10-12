"""Page writing and truth authoring for the digital-format pack.

A case is written from a :class:`Sheet` -- the lines, columns and cells the
page prints -- and its truth is authored from the same Sheet by reading the
printed strings the way a person reads a page. Nothing here asks LossLift
anything: the truth is what was printed, and the pack's own small readers
(:func:`printed_money`, :func:`printed_date`) are independent of
``core.normalize``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

import pymupdf

LETTER = (612.0, 792.0)
A4 = (595.0, 842.0)
LETTER_LANDSCAPE = (792.0, 612.0)
LEFT = 36.0
LINE = 14.0
FONT = 8.0

MONEY_FIELDS = ("paid_indemnity", "paid_medical", "paid_expense", "paid_total",
                "reserve_total", "recovery_total", "incurred_total")


@dataclass(frozen=True)
class Column:
    #: The printed label; a "\n" wraps it onto a second header line.
    label: str
    #: The canonical field the column prints, or None (description, notes).
    field: str | None
    x: float


@dataclass(frozen=True)
class Row:
    #: Printed text per column, in column order ("" prints nothing).
    cells: tuple[str, ...]
    #: The claim number a person reads off this row; None for a row that is
    #: not a claim (furniture), "?" for a claim whose number cannot be read.
    claim: str | None
    #: Further printed lines under the row: (column index, text).
    continuation: tuple[tuple[int, str], ...] = ()


@dataclass(frozen=True)
class Sheet:
    size: tuple[float, float]
    letterhead: tuple[str, ...]
    columns: tuple[Column, ...]
    rows: tuple[Row, ...]
    total: tuple[str, ...] | None = None
    after: tuple[str, ...] = ()
    footer: tuple[str, ...] = ("Page 1 of 1",)


def write(sheet: Sheet, path: Path) -> Path:
    document = pymupdf.open()
    width, height = sheet.size
    page = document.new_page(width=width, height=height)
    y = 40.0
    for line in sheet.letterhead:
        page.insert_text((LEFT, y), line, fontsize=9)
        y += LINE
    y += LINE
    header_lines = max(label.count("\n") + 1 for label in (c.label for c in sheet.columns))
    for line in range(header_lines):
        for column in sheet.columns:
            parts = column.label.split("\n")
            if line < len(parts):
                page.insert_text((LEFT + column.x, y), parts[line], fontsize=FONT)
        y += LINE
    for row in sheet.rows:
        for column, cell in zip(sheet.columns, row.cells):
            if cell:
                page.insert_text((LEFT + column.x, y), cell, fontsize=FONT)
        y += LINE
        for index, text in row.continuation:
            page.insert_text((LEFT + sheet.columns[index].x, y), text, fontsize=FONT)
            y += LINE
    if sheet.total:
        for column, cell in zip(sheet.columns, sheet.total):
            if cell:
                page.insert_text((LEFT + column.x, y), cell, fontsize=FONT)
        y += LINE
    y += LINE
    for line in sheet.after:
        page.insert_text((LEFT, y), line, fontsize=FONT)
        y += LINE
    for index, line in enumerate(sheet.footer):
        page.insert_text((LEFT, height - 24 - LINE * (len(sheet.footer) - 1 - index)), line,
                         fontsize=FONT)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    document.close()
    return path


# --------------------------------------------------------------------------
# Reading printed strings as a person does
# --------------------------------------------------------------------------

_AMOUNT = re.compile(r"^\$?\d{1,3}(,\d{3})*(\.\d{2})?$|^\$?\d+(\.\d{2})?$")


def printed_money(text: str) -> str | None:
    """The amount a printed money cell states, as a decimal string; None if blank.

    ``(1,234.56)`` and ``1,234.56-`` are negative; commas and ``$`` are
    presentation.
    """
    text = text.strip()
    if not text:
        return None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]
    elif text.endswith("-"):
        negative, text = True, text[:-1]
    elif text.startswith("-"):
        negative, text = True, text[1:]
    if not _AMOUNT.match(text):
        raise ValueError("not a printed amount")
    value = Decimal(text.replace(",", "").replace("$", ""))
    return str(-value if negative else value)


def printed_date(text: str) -> str:
    return datetime.strptime(text.strip(), "%m/%d/%Y").date().isoformat()


def _label(value: str | None) -> Any:
    return {"state": "absent"} if value is None else value


def claim_truth(sheet: Sheet, row: Row) -> dict[str, Any]:
    by_field = {c.field: cell for c, cell in zip(sheet.columns, row.cells) if c.field}
    fields: dict[str, Any] = {}
    for name in ("paid_total", "reserve_total", "recovery_total", "incurred_total",
                 "paid_indemnity", "paid_expense", "paid_medical"):
        if name in by_field:
            fields[name] = _label(printed_money(by_field[name]))
        elif name in ("paid_total", "reserve_total", "recovery_total", "incurred_total"):
            fields[name] = {"state": "absent"}  # the column is not printed at all
    fields["date_of_loss"] = printed_date(by_field["date_of_loss"])
    status = by_field.get("claim_status", "").strip()
    fields["claim_status"] = status if status else {"state": "absent"}
    entry: dict[str, Any] = {"anchor": {"page": 1}, "fields": fields}
    if row.claim == "?":
        entry["identity"] = "ambiguous"
    else:
        entry["claim_number"] = row.claim
    return entry


def total_truth(sheet: Sheet) -> dict[str, Any]:
    if not sheet.total:
        return {}
    totals: dict[str, Any] = {}
    for column, cell in zip(sheet.columns, sheet.total):
        if column.field in MONEY_FIELDS:
            value = printed_money(cell)
            if value is not None:
                totals[column.field] = value
    return totals


def document_spec(sheet: Sheet, *, status: str = "CLEAN",
                  printed_count: int | None = None) -> dict[str, Any]:
    """A complete truth-v1 document (without id and hash) for a one-page sheet."""
    return {
        "format_family": "digital",
        "adjudication": "adjudicated",
        "page_count": 1,
        "pages": [{"page": 1, "run": None, "role": "claims"}],
        "runs": [],
        "status": status,
        "printed": {
            "claim_count": {"state": "absent"} if printed_count is None else printed_count,
            "totals": total_truth(sheet),
        },
        "claims": [claim_truth(sheet, row) for row in sheet.rows if row.claim is not None],
    }


def amount(value: Decimal) -> str:
    """How the pack prints an amount: US thousands, two decimals, parentheses if negative."""
    text = f"{abs(value):,.2f}"
    return f"({text})" if value < 0 else text


def total_row(sheet_columns: Sequence[Column], rows: Sequence[Row], label: str = "TOTAL"
              ) -> tuple[str, ...]:
    """The printed total of every money column, summed from what the rows print."""
    cells = []
    for index, column in enumerate(sheet_columns):
        if index == 0:
            cells.append(label)
        elif column.field in MONEY_FIELDS:
            values = [printed_money(row.cells[index]) for row in rows if row.claim is not None]
            cells.append(amount(sum((Decimal(v) for v in values if v is not None),
                                    Decimal("0"))))
        else:
            cells.append("")
    return tuple(cells)


__all__ = ["A4", "Column", "LETTER", "LETTER_LANDSCAPE", "Row", "Sheet", "amount",
           "document_spec", "printed_date", "printed_money", "total_row", "write"]
