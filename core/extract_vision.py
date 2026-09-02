"""Stage 2b — vision extraction (spec section 5).

Scanned pages only. A page with a text layer never reaches this module: using
an LLM to read numbers off a digital PDF is explicitly out of scope (spec
section 13), because deterministic parsing is both cheaper and correct.

Two rules hold everywhere here:

* The model **transcribes, it does not calculate**. It returns the characters
  printed on the page — separators, parentheses, trailing minus, ``-0-`` — and
  the same :mod:`core.normalize` parser that handles digital text turns them
  into values. A vision-read ``1.234,56`` is parsed exactly like a digital one.
* Confidence is capped at 0.85 no matter what the model claims, and every
  field is marked ``source_method="vision"`` so the review screen and the
  export can show which numbers a human should check.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

import pymupdf

from core.normalize import clean_text
from core.schema import RawRow, RawTable

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "extract_vision.md"

#: Spec section 5: render scanned pages at 300 DPI.
RENDER_DPI = 300

#: Spec section 5: cap vision confidence regardless of what the model says.
VISION_CONFIDENCE_CAP = 0.85

DEFAULT_MODEL = "gemini-2.0-flash"

#: The JSON the prompt asks for.
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "headers": {"type": "array", "items": {"type": "string"}},
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "cells": {"type": "array", "items": {"type": "string"}},
                    "kind": {"type": "string", "enum": ["data", "total"]},
                },
                "required": ["cells"],
            },
        },
        "printed_claim_count": {"type": "integer", "nullable": True},
        "valuation_date": {"type": "string", "nullable": True},
    },
    "required": ["headers", "rows"],
}


class VisionUnavailable(RuntimeError):
    """Vision extraction was asked for but cannot run."""


@dataclass
class VisionExtraction:
    """Successful page tables plus failures from the same vision batch."""

    tables: list[RawTable]
    failures: dict[int, str]

    def __iter__(self) -> Iterator[RawTable]:
        return iter(self.tables)

    def __len__(self) -> int:
        return len(self.tables)


@dataclass
class VisionPage:
    """One rendered page, ready to send."""

    page: int
    image: bytes
    mime_type: str = "image/png"
    width: int = 0
    height: int = 0


def vision_enabled() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def render_page(path: str | Path, page_number: int, dpi: int = RENDER_DPI) -> VisionPage:
    """Rasterise one 1-based page."""
    with pymupdf.open(path) as document:
        if not 1 <= page_number <= len(document):
            raise VisionUnavailable(
                f"Page {page_number} does not exist in {Path(path).name}."
            )
        pixmap = document[page_number - 1].get_pixmap(dpi=dpi)
        return VisionPage(
            page=page_number,
            image=pixmap.tobytes("png"),
            width=pixmap.width,
            height=pixmap.height,
        )


def render_pages(
    path: str | Path, pages: Sequence[int], dpi: int = RENDER_DPI
) -> list[VisionPage]:
    return [render_page(path, page, dpi) for page in pages]


def load_prompt() -> str:
    """The committed prompt, without its explanatory preamble."""
    text = PROMPT_PATH.read_text(encoding="utf-8")
    marker = "## User"
    return text.split(marker, 1)[1].strip() if marker in text else text.strip()


def parse_vision_response(payload: str | dict[str, Any], page_number: int) -> RawTable:
    """Turn the model's JSON into the same RawTable the digital path produces.

    Rows are padded or trimmed to the header width so a hallucinated extra cell
    cannot silently shift a whole row into the wrong columns.
    """
    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text, flags=re.IGNORECASE)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as error:
            raise VisionUnavailable(
                f"Page {page_number}: the model did not return JSON ({error})."
            ) from error
    else:
        data = payload

    if not isinstance(data, dict):
        raise VisionUnavailable(f"Page {page_number}: the model's JSON is not an object.")

    headers = [clean_text(header) for header in data.get("headers") or []]
    if not headers:
        raise VisionUnavailable(f"Page {page_number}: the model found no column headers.")

    rows: list[RawRow] = []
    totals: list[RawRow] = []
    for index, entry in enumerate(data.get("rows") or []):
        if not isinstance(entry, dict):
            continue
        cells = [clean_text(cell) for cell in entry.get("cells") or []]
        if not cells:
            continue
        cells = (cells + [""] * len(headers))[: len(headers)]
        kind = "total" if str(entry.get("kind", "data")).lower() == "total" else "data"
        row = RawRow(cells=cells, page=page_number, line_index=index, kind=kind)
        if row.is_blank():
            continue
        (totals if kind == "total" else rows).append(row)

    count = data.get("printed_claim_count")
    valuation = data.get("valuation_date")
    return RawTable(
        page=page_number,
        headers=headers,
        rows=rows,
        total_rows=totals,
        strategy="vision",
        printed_claim_count=count if isinstance(count, int) else None,
        valuation_date_text=clean_text(valuation) or None if valuation else None,
    )


def _client(client: Any | None = None) -> Any:
    if client is not None:
        return client
    if not vision_enabled():
        raise VisionUnavailable(
            "Vision extraction needs a Gemini key. Set GEMINI_API_KEY, or "
            "upload a PDF that has a text layer."
        )
    try:
        from google import genai
    except ImportError as error:  # pragma: no cover - dependency missing
        raise VisionUnavailable("google-genai is not installed") from error
    return genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


def extract_page(
    rendered: VisionPage,
    *,
    client: Any | None = None,
    model: str | None = None,
) -> RawTable:
    """Send one rendered page and parse what comes back."""
    active = _client(client)
    target = model or os.getenv("LOSSLIFT_GEMINI_MODEL", DEFAULT_MODEL)

    try:
        from google.genai import types

        contents = [
            types.Part.from_bytes(data=rendered.image, mime_type=rendered.mime_type),
            load_prompt(),
        ]
        config: dict[str, Any] | None = {
            "response_mime_type": "application/json",
            "response_schema": RESPONSE_SCHEMA,
        }
    except ImportError:  # a stand-in client in tests needs no types module
        contents = [rendered.image, load_prompt()]
        config = None

    try:
        response = active.models.generate_content(
            model=target, contents=contents, config=config
        )
        text = getattr(response, "text", None) or ""
    except Exception as error:  # noqa: BLE001 - any transport failure is the same
        raise VisionUnavailable(
            f"Page {rendered.page}: the vision call failed ({error})."
        ) from error

    return parse_vision_response(text, rendered.page)


def extract_scanned_pages(
    path: str | Path,
    pages: Sequence[int],
    *,
    client: Any | None = None,
    model: str | None = None,
    dpi: int = RENDER_DPI,
) -> VisionExtraction:
    """Extract every scanned page of a document.

    Successful pages remain usable when another page fails, but every partial
    failure is returned with them so reconciliation can preserve the resulting
    source uncertainty. A batch in which every page fails is still unavailable.
    """
    tables: list[RawTable] = []
    failures: dict[int, str] = {}
    for rendered in render_pages(path, pages, dpi):
        try:
            tables.append(extract_page(rendered, client=client, model=model))
        except VisionUnavailable as error:
            failures[rendered.page] = str(error)
    if not tables and failures:
        raise VisionUnavailable(" ".join(failures.values()))
    return VisionExtraction(tables=tables, failures=failures)
