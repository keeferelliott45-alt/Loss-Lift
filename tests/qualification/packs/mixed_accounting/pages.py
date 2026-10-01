"""Multi-page documents, their scanned pages, and truth from what they print.

A document is a list of :class:`Page` specs. :func:`write` draws each page as
text, then replaces the pages marked ``scanned`` with a picture of themselves,
so they carry no text layer. :func:`transcription` is what a vision model
would read off a scanned page: exactly its printed headers, rows, total,
numbering, valuation date and heading -- and nothing it does not print. A
claim count is transcribed only when the page prints one.

Truth is authored from the same specs with this pack's own readers of printed
amounts and dates, never from LossLift.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

import pymupdf

from core.extract_vision import VisionExtraction, parse_vision_response

SIZE = (612.0, 792.0)
LEFT = 36.0
LINE = 14.0
FONT = 8.0
MONEY = ("paid_total", "reserve_total", "recovery_total", "incurred_total")

#: Label, canonical field, x offset.
COLUMNS = (
    ("Claim Number", "claim_number", 0),
    ("Loss Date", "date_of_loss", 95),
    ("Status", "claim_status", 170),
    ("Paid Total", "paid_total", 240),
    ("Reserve Total", "reserve_total", 325),
    ("Incurred Total", "incurred_total", 410),
)


@dataclass(frozen=True)
class Row:
    cells: tuple[str, ...]
    #: The claim number a person reads here; None when the row is not a claim.
    claim: str | None


@dataclass(frozen=True)
class Page:
    top: tuple[str, ...] = ()
    rows: tuple[Row, ...] = ()
    total: tuple[str, ...] | None = None
    after: tuple[str, ...] = ()
    footer: tuple[str, ...] = ()
    #: Prose instead of a table (a cover letter).
    prose: tuple[str, ...] = ()
    scanned: bool = False

    @property
    def has_table(self) -> bool:
        return bool(self.rows)


def write(pages: Sequence[Page], path: Path) -> Path:
    digital = pymupdf.open()
    for spec in pages:
        page = digital.new_page(width=SIZE[0], height=SIZE[1])
        y = 40.0
        for line in (*spec.top, *spec.prose):
            page.insert_text((LEFT, y), line, fontsize=9)
            y += LINE
        if spec.has_table:
            y += LINE
            for label, _field, x in COLUMNS:
                page.insert_text((LEFT + x, y), label, fontsize=FONT)
            y += LINE
            for row in (*spec.rows, *((Row(spec.total, None),) if spec.total else ())):
                for (_label, _field, x), cell in zip(COLUMNS, row.cells):
                    if cell:
                        page.insert_text((LEFT + x, y), cell, fontsize=FONT)
                y += LINE
        y += LINE
        for line in spec.after:
            page.insert_text((LEFT, y), line, fontsize=FONT)
            y += LINE
        for index, line in enumerate(spec.footer):
            page.insert_text((LEFT, SIZE[1] - 24 - LINE * (len(spec.footer) - 1 - index)), line,
                             fontsize=FONT)
    out = pymupdf.open()
    for index, spec in enumerate(pages):
        if spec.scanned:
            pixmap = digital[index].get_pixmap(dpi=100)
            page = out.new_page(width=SIZE[0], height=SIZE[1])
            page.insert_image(page.rect, pixmap=pixmap)
        else:
            out.insert_pdf(digital, from_page=index, to_page=index)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.save(path)
    out.close()
    digital.close()
    return path


# --------------------------------------------------------------------------
# What a vision model reads off a scanned page
# --------------------------------------------------------------------------

_PAGE_LABEL = re.compile(r"Page\s+(\d+)\s+of\s+(\d+)")
_REPORT_COUNT = re.compile(r"Total Claims:\s*(\d+)")


def transcription(spec: Page) -> dict[str, Any]:
    label = None
    for line in spec.footer:
        match = _PAGE_LABEL.search(line)
        if match:
            label = {"text": match.group(0), "number": int(match.group(1)),
                     "of": int(match.group(2)), "position": "footer"}
    valuation = next((line.split(":", 1)[1].strip() for line in spec.top
                      if line.startswith("Valuation Date:")), None)
    heading = spec.top[0] if spec.top and not spec.top[0].startswith(
        ("LOSS RUN", "Valuation", "Named", "Policy")) else None
    count = None
    for line in spec.after:
        match = _REPORT_COUNT.search(line)
        if match:
            count = int(match.group(1))
    rows = [{"cells": list(row.cells), "kind": "data"} for row in spec.rows]
    if spec.total:
        rows.append({"cells": list(spec.total), "kind": "total"})
    return {
        "headers": [label for label, _field, _x in COLUMNS],
        "rows": rows,
        "printed_claim_count": count,
        "valuation_date": valuation,
        "page_label": label,
        "report_heading": heading,
    }


def offline_reader(pages: Sequence[Page]):
    """A vision extractor answering from the pages' transcriptions; no model."""
    payloads = {index: transcription(spec) for index, spec in enumerate(pages, start=1)
                if spec.scanned}

    def extract(path: Any, wanted: Sequence[int], **_kwargs: Any) -> VisionExtraction:
        return VisionExtraction(
            tables=[parse_vision_response(payloads[page], page) for page in wanted
                    if page in payloads],
            failures={page: "no transcription" for page in wanted if page not in payloads},
        )

    return extract


# --------------------------------------------------------------------------
# Truth, read off the specs as a person reads the page
# --------------------------------------------------------------------------


def printed_money(text: str) -> str | None:
    text = text.strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    value = Decimal(text.strip("()").replace(",", ""))
    return str(-value if negative else value)


def printed_date(text: str) -> str:
    return datetime.strptime(text.strip(), "%m/%d/%Y").date().isoformat()


def _absent_or(value: str | None) -> Any:
    return {"state": "absent"} if value is None else value


def claim_truth(row: Row, page: int) -> dict[str, Any]:
    cells = dict(zip((field for _label, field, _x in COLUMNS), row.cells))
    fields: dict[str, Any] = {
        "date_of_loss": printed_date(cells["date_of_loss"]),
        "claim_status": cells["claim_status"] or {"state": "absent"},
        "recovery_total": {"state": "absent"},  # no recovery column is printed
    }
    for name in ("paid_total", "reserve_total", "incurred_total"):
        fields[name] = _absent_or(printed_money(cells[name]))
    return {"claim_number": row.claim, "anchor": {"page": page}, "fields": fields}


def printed(count: int | None, total: Sequence[str] | None) -> dict[str, Any]:
    totals = {}
    if total:
        for (_label, field, _x), cell in zip(COLUMNS, total):
            if field in MONEY and cell:
                totals[field] = printed_money(cell)
    return {"claim_count": {"state": "absent"} if count is None else count, "totals": totals}


def total_of(rows: Sequence[Row], label: str = "TOTAL") -> tuple[str, ...]:
    def column(index: int) -> str:
        value = sum((Decimal(printed_money(r.cells[index]) or "0") for r in rows), Decimal("0"))
        return f"{value:,.2f}"
    return (label, "", "", column(3), column(4), column(5))


def document_spec(
    pages: Sequence[Page],
    *,
    status: str,
    roles: Mapping[int, str] | None = None,
    runs: Sequence[Mapping[str, Any]] = (),
    printed_facts: Mapping[str, Any] | None = None,
    sections: Sequence[Mapping[str, Any]] = (),
    family: str = "mixed",
) -> dict[str, Any]:
    """Complete truth (without id and hash). ``runs`` are
    ``{"id", "pages", "status", "printed"}``; page roles default to claims
    where a page prints rows and claim_free otherwise."""
    roles = dict(roles or {})
    membership = {page: run["id"] for run in runs for page in run["pages"]}
    page_entries = []
    for number, spec in enumerate(pages, start=1):
        role = roles.get(number, "claims" if spec.has_table else "claim_free")
        page_entries.append({"page": number, "run": membership.get(number), "role": role})
    claims = [claim_truth(row, number)
              for number, spec in enumerate(pages, start=1)
              for row in spec.rows if row.claim is not None]
    return {
        "format_family": family,
        "adjudication": "adjudicated",
        "page_count": len(pages),
        "pages": page_entries,
        "runs": [dict(run) for run in runs],
        "status": status,
        "printed": dict(printed_facts or printed(None, None)),
        "sections": [dict(section) for section in sections],
        "claims": claims,
    }
