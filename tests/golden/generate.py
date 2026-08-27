"""Render the synthetic fixtures to PDF and write the expected CSVs.

The PDFs are built into a temporary directory at test time and never
committed — ``.gitignore`` excludes ``*.pdf`` and the spec forbids shipping
loss-run documents (section 9).

The expected CSVs *are* committed, so a reviewer can read what the pipeline is
supposed to produce.  Regenerate them deliberately::

    python -m tests.golden.generate --write-expected
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

import pymupdf

from tests.golden.fixtures import ALL_FIXTURES, Column, Fixture

EXPECTED_DIR = Path(__file__).parent / "expected"

PAGE_SIZE = (792.0, 612.0)          # landscape US letter
PORTRAIT_SIZE = (612.0, 792.0)
MARGIN = 26.0
COLUMN_GAP = 7.0                     # guarantees a real gutter between columns
CELL_PAD = 3.0
LINE_HEIGHT = 13.0
HEADER_BLOCK_HEIGHT = 96.0


# --------------------------------------------------------------------------
# Value formatting — one carrier convention per fixture
# --------------------------------------------------------------------------


def format_money(
    value: Decimal, style: str, negative_style: str | None = None
) -> str:
    """Render an amount in a carrier's convention.

    ``style`` picks the separators (and, by default, the negative style);
    ``negative_style`` overrides just the negatives, which is how a European
    report writes a credit as ``200,00-``.
    """
    if negative_style is None:
        negative_style = {
            "paren": "paren", "trailing_minus": "trailing"
        }.get(style, "leading")
    separators = "eu" if style == "eu" else "us"

    if negative_style == "trailing" and value == 0:
        return "-0-"
    magnitude = f"{abs(value):,.2f}"
    if separators == "eu":
        magnitude = magnitude.translate(str.maketrans({",": ".", ".": ","}))
    if value >= 0:
        return magnitude
    if negative_style == "paren":
        return f"({magnitude})"
    if negative_style == "trailing":
        return f"{magnitude}-"
    return f"-{magnitude}"


def format_date(value: date, style: str) -> str:
    if style == "dmy_dots":
        return value.strftime("%d.%m.%Y")
    if style == "dmy":
        return value.strftime("%d/%m/%Y")
    if style == "iso":
        return value.strftime("%Y-%m-%d")
    if style == "spelled":
        return value.strftime("%d-%b-%Y")
    return value.strftime("%m/%d/%Y")


def _with_symbol(text: str, fixture: Fixture) -> str:
    if not fixture.currency_symbol or not text:
        return text
    if fixture.currency_position == "suffix":
        return f"{text} {fixture.currency_symbol}"
    return f"{fixture.currency_symbol}{text}"


def money_cell(value: Decimal, fixture: Fixture, field_name: str = "") -> str:
    """Render an amount the way this carrier prints it.

    A carrier that states recoveries as credits prints the recovery column
    with a trailing minus and, in the documents this mirrors, without the
    currency symbol the other money columns carry.
    """
    if fixture.recovery_as_credit and field_name == "recovery_total":
        return format_money(-abs(value), fixture.number_format, "trailing")
    return _with_symbol(format_money(value, fixture.number_format), fixture)


def cell_text(fixture: Fixture, claim: dict[str, Any], column: Column) -> str:
    """What this cell prints, honouring any per-row display override."""
    if column.field is None:
        return ""
    override = claim.get(f"{column.field}_display")
    if override is not None:
        return str(override)

    value = claim.get(column.field)
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return money_cell(value, fixture, column.field)
    if isinstance(value, date):
        return format_date(value, fixture.date_format)
    if isinstance(value, bool):
        return "Y" if value else "N"
    return str(value)


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------


#: PyMuPDF's base-14 fonts are Latin-1 and have no euro sign — it silently
#: renders as a middle dot, which would make this generator produce a document
#: no parser could read and blame the parser for it. When a fixture prints a
#: non-ASCII symbol, a TrueType face is embedded instead.
_TTF_REGULAR = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)
_TTF_BOLD = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)

_FONT_CACHE: dict[str, "pymupdf.Font"] = {}


def _first_existing(paths: tuple[str, ...]) -> str | None:
    for path in paths:
        if Path(path).exists():
            return path
    return None


def font_files(fixture: Fixture) -> tuple[str | None, str | None]:
    """The regular and bold faces this fixture needs, or ``(None, None)``."""
    symbol = fixture.currency_symbol or ""
    if all(ord(char) < 128 for char in symbol):
        return None, None
    regular = _first_existing(_TTF_REGULAR)
    if regular is None:
        raise RuntimeError(
            f"{fixture.name} prints {symbol!r}, which the built-in fonts cannot "
            f"encode, and no TrueType face was found to embed."
        )
    return regular, _first_existing(_TTF_BOLD) or regular


def _loaded(path: str) -> "pymupdf.Font":
    if path not in _FONT_CACHE:
        _FONT_CACHE[path] = pymupdf.Font(fontfile=path)
    return _FONT_CACHE[path]


def _width(text: str, font: str, size: float, fontfile: str | None = None) -> float:
    if fontfile:
        return _loaded(fontfile).text_length(text, fontsize=size)
    return pymupdf.get_text_length(text, fontname=font, fontsize=size)


def plan_layout(fixture: Fixture) -> tuple[float, list[float], list[float]]:
    """Pick a font size at which nothing has to be truncated.

    Truncated cells would bleed across column gutters, which is a failure mode
    this generator would be inventing rather than reproducing.
    """
    page_width = (PORTRAIT_SIZE if not fixture.landscape else PAGE_SIZE)[0]
    available = page_width - 2 * MARGIN - COLUMN_GAP * (len(fixture.columns) - 1)

    rows = list(fixture.claims)
    regular, _ = font_files(fixture)
    for size in (fixture.font_size, 7.0, 6.5, 6.0, 5.5, 5.0, 4.5):
        widths: list[float] = []
        for column in fixture.columns:
            longest = _width(column.label, fixture.font, size, regular)
            for claim in rows:
                longest = max(
                    longest,
                    _width(cell_text(fixture, claim, column), fixture.font, size, regular),
                )
            if column.field in fixture.money_fields:
                total = sum(
                    (claim[column.field] for claim in rows if claim.get(column.field) is not None),
                    Decimal("0"),
                )
                longest = max(
                    longest,
                    _width(
                        money_cell(total, fixture, column.field),
                        fixture.font, size, regular,
                    ),
                )
            widths.append(longest + 2 * CELL_PAD)
        if sum(widths) <= available:
            starts, cursor = [], MARGIN
            for width in widths:
                starts.append(cursor)
                cursor += width + COLUMN_GAP
            return size, widths, starts

    raise RuntimeError(f"{fixture.name}: columns do not fit even at 4.5pt")


def _draw(
    page, text: str, x: float, y: float, font: str, size: float,
    bold: bool = False, embedded: tuple[str | None, str | None] = (None, None),
) -> None:
    if not text:
        return
    regular, bold_file = embedded
    if regular:
        fontfile = (bold_file or regular) if bold else regular
        name = "bodyb" if bold and bold_file else "body"
        page.insert_font(fontname=name, fontfile=fontfile)
        page.insert_text((x, y), text, fontname=name, fontsize=size)
        return
    name = font
    if bold:
        name = "cobo" if font == "cour" else "hebo"
    page.insert_text((x, y), text, fontname=name, fontsize=size)


def _draw_row(
    page,
    fixture: Fixture,
    values: Sequence[str],
    starts: Sequence[float],
    widths: Sequence[float],
    y: float,
    size: float,
    bold: bool = False,
) -> None:
    embedded = font_files(fixture)
    for column, text, start, width in zip(fixture.columns, values, starts, widths):
        if not text:
            continue
        if column.align == "right":
            x = start + width - CELL_PAD - _width(text, fixture.font, size, embedded[0])
        else:
            x = start + CELL_PAD
        _draw(page, text, x, y, fixture.font, size, bold, embedded)


def _draw_document_header(page, fixture: Fixture, size: float, page_no: int, pages: int) -> float:
    """Carrier block at the top of every page; returns the first table y."""
    font = fixture.font
    embedded = font_files(fixture)
    y = MARGIN + 10
    letterhead = fixture.carrier
    if fixture.report_title:
        letterhead = f"{fixture.carrier} - {fixture.report_title}"
    _draw(page, letterhead, MARGIN, y, font, size + 3.5, bold=True, embedded=embedded)
    right = (PORTRAIT_SIZE if not fixture.landscape else PAGE_SIZE)[0] - MARGIN
    label = f"Page {page_no} of {pages}"
    _draw(page, label, right - _width(label, font, size, embedded[0]), y, font, size, embedded=embedded)

    y += LINE_HEIGHT + 2
    _draw(page, "LOSS RUN REPORT", MARGIN, y, font, size + 1, bold=True, embedded=embedded)

    y += LINE_HEIGHT
    _draw(page, f"Named Insured: {fixture.named_insured}", MARGIN, y, font, size, embedded=embedded)
    valuation = format_date(fixture.valuation_date, fixture.date_format)
    _draw(page, f"Valuation Date: {valuation}", MARGIN + 340, y, font, size, embedded=embedded)

    y += LINE_HEIGHT
    _draw(page, f"Policy Number: {fixture.policy_number}", MARGIN, y, font, size, embedded=embedded)
    start, end = fixture.policy_period
    period = (
        f"Policy Period: {format_date(start, fixture.date_format)} to "
        f"{format_date(end, fixture.date_format)}"
    )
    _draw(page, period, MARGIN + 340, y, font, size, embedded=embedded)

    y += LINE_HEIGHT
    _draw(page, f"Line of Business: {fixture.line_of_business}", MARGIN, y, font, size, embedded=embedded)
    _draw(page, f"Currency: {fixture.currency}", MARGIN + 340, y, font, size, embedded=embedded)

    return MARGIN + HEADER_BLOCK_HEIGHT


def _draw_rules(page, starts, widths, top: float, bottom: float, rows: int) -> None:
    """Ruled grid for the fixture that exercises pdfplumber's table detector."""
    left, right = starts[0] - CELL_PAD, starts[-1] + widths[-1] + CELL_PAD
    for index in range(rows + 1):
        y = top + index * LINE_HEIGHT
        page.draw_line(pymupdf.Point(left, y), pymupdf.Point(right, y), width=0.4)
    edges = [start - COLUMN_GAP / 2 for start in starts] + [right]
    for x in edges:
        page.draw_line(pymupdf.Point(x, top), pymupdf.Point(x, bottom), width=0.4)


def render(fixture: Fixture, path: Path) -> Path:
    """Write one fixture to ``path`` and return it."""
    size, widths, starts = plan_layout(fixture)
    page_size = PAGE_SIZE if fixture.landscape else PORTRAIT_SIZE
    chunks = [
        fixture.claims[index : index + fixture.rows_per_page]
        for index in range(0, len(fixture.claims), fixture.rows_per_page)
    ] or [()]

    document = pymupdf.open()
    for page_index, chunk in enumerate(chunks, start=1):
        page = document.new_page(width=page_size[0], height=page_size[1])
        y = _draw_document_header(page, fixture, size, page_index, len(chunks))

        header_y = y
        _draw_row(
            page, fixture, [column.label for column in fixture.columns],
            starts, widths, y, size, bold=True,
        )
        # Underline the header the way carrier reports do.
        page.draw_line(
            pymupdf.Point(starts[0] - CELL_PAD, y + 3),
            pymupdf.Point(starts[-1] + widths[-1] + CELL_PAD, y + 3),
            width=0.6,
        )
        y += LINE_HEIGHT + 3

        for claim in chunk:
            _draw_row(
                page, fixture,
                [cell_text(fixture, claim, column) for column in fixture.columns],
                starts, widths, y, size,
            )
            y += LINE_HEIGHT

        if fixture.style == "ruled":
            _draw_rules(page, starts, widths, header_y - LINE_HEIGHT + 4,
                        y - LINE_HEIGHT + 4, len(chunk) + 1)

        if page_index == len(chunks):
            y += 4
            page.draw_line(
                pymupdf.Point(starts[0] - CELL_PAD, y - LINE_HEIGHT + 4),
                pymupdf.Point(starts[-1] + widths[-1] + CELL_PAD, y - LINE_HEIGHT + 4),
                width=0.6,
            )
            if fixture.print_totals:
                totals = fixture.printed_totals()
                row = []
                for index, column in enumerate(fixture.columns):
                    if index == 0:
                        row.append(fixture.total_label)
                    elif column.field in totals:
                        row.append(money_cell(totals[column.field], fixture, column.field))
                    elif index == 1 and fixture.claim_count_in_totals_row:
                        row.append(f"{fixture.claim_count_label} {len(fixture.claims)}")
                    else:
                        row.append("")
                _draw_row(page, fixture, row, starts, widths, y, size, bold=True)
                y += LINE_HEIGHT
            if fixture.print_claim_count and not fixture.claim_count_in_totals_row:
                y += 4
                _draw(
                    page,
                    f"{fixture.claim_count_label} {len(fixture.claims)}",
                    starts[0] + CELL_PAD, y, fixture.font, size, bold=True,
                    embedded=font_files(fixture),
                )

    path.parent.mkdir(parents=True, exist_ok=True)
    if fixture.scanned:
        _flatten_to_image(document, path)
    else:
        document.save(str(path))
    document.close()
    return path


def _flatten_to_image(document, path: Path) -> None:
    """Re-emit every page as a bitmap so the PDF has no text layer at all."""
    scanned = pymupdf.open()
    for page in document:
        pixmap = page.get_pixmap(dpi=200)
        target = scanned.new_page(width=page.rect.width, height=page.rect.height)
        target.insert_image(target.rect, pixmap=pixmap)
    scanned.save(str(path))
    scanned.close()


# --------------------------------------------------------------------------
# Expected output
# --------------------------------------------------------------------------


def expected_fields(fixture: Fixture) -> list[str]:
    return [column.field for column in fixture.columns if column.field]


def _canonical(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "Y" if value else "N"
    return str(value)


_STATUS_CANON = {
    "open": "OPEN", "o": "OPEN",
    "closed": "CLOSED", "c": "CLOSED",
    "reopened": "REOPENED", "r": "REOPENED",
}


def expected_rows(fixture: Fixture) -> list[dict[str, str]]:
    rows = []
    for claim in fixture.claims:
        row = {}
        for field_name in expected_fields(fixture):
            value = claim.get(field_name)
            if field_name == "claim_status":
                row[field_name] = _STATUS_CANON.get(str(value).strip().lower(), "UNKNOWN")
            else:
                row[field_name] = _canonical(value)
        rows.append(row)
    return rows


def expected_meta(fixture: Fixture) -> dict[str, Any]:
    start, end = fixture.policy_period
    return {
        "name": fixture.name,
        "description": fixture.description,
        "carrier": fixture.carrier,
        "named_insured": fixture.named_insured,
        "policy_number": fixture.policy_number,
        "policy_period_start": start.isoformat(),
        "policy_period_end": end.isoformat(),
        "valuation_date": fixture.valuation_date.isoformat(),
        "line_of_business": fixture.line_of_business,
        "currency": fixture.currency,
        "locale_hint": fixture.locale_hint,
        "claim_count": len(fixture.claims),
        "printed_claim_count": len(fixture.claims) if fixture.print_claim_count else None,
        "printed_totals": (
            {k: f"{v:.2f}" for k, v in fixture.printed_totals().items()}
            if fixture.print_totals
            else {}
        ),
        "scanned": fixture.scanned,
        "fields": expected_fields(fixture),
    }


def write_expected(directory: Path = EXPECTED_DIR) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for fixture in ALL_FIXTURES:
        csv_path = directory / f"{fixture.name}.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=expected_fields(fixture))
            writer.writeheader()
            writer.writerows(expected_rows(fixture))
        meta_path = directory / f"{fixture.name}.meta.json"
        meta_path.write_text(json.dumps(expected_meta(fixture), indent=2) + "\n")
        written.extend([csv_path, meta_path])
    return written


def load_expected(name: str, directory: Path = EXPECTED_DIR) -> list[dict[str, str]]:
    with open(directory / f"{name}.csv", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_meta(name: str, directory: Path = EXPECTED_DIR) -> dict[str, Any]:
    return json.loads((directory / f"{name}.meta.json").read_text())


def build_all(target: Path) -> dict[str, Path]:
    """Render every fixture into ``target``."""
    target.mkdir(parents=True, exist_ok=True)
    return {
        fixture.name: render(fixture, target / f"{fixture.name}.pdf")
        for fixture in ALL_FIXTURES
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-expected", action="store_true",
                        help="regenerate the committed expected CSVs")
    parser.add_argument("--pdf-dir", type=Path, default=None,
                        help="also render the PDFs into this directory")
    args = parser.parse_args()

    if args.write_expected:
        for path in write_expected():
            print(f"wrote {path}")
    if args.pdf_dir:
        for name, path in build_all(args.pdf_dir).items():
            print(f"rendered {name} -> {path}")
    if not args.write_expected and not args.pdf_dir:
        parser.print_help()


if __name__ == "__main__":
    main()
