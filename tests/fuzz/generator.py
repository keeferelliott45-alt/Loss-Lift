"""Deterministic synthetic loss-run generator and ground truth.

A :class:`Case` is the truth about one printed document: the pages as they were
laid out, and every claim that was really printed, with its money as
:class:`Decimal`. Rendering it to a PDF is deterministic, so the same case
always produces the same bytes (spec section 9: synthetic only).

The clean family renders documents a correct reader should read perfectly.
Mutating operators (``mutations.py``) turn a clean case into a hostile one.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import pymupdf

#: Geometry copied from ``tests/test_packet_claim_series.py`` so a clean case
#: renders the way the fixtures those tests prove are read cleanly.
LEFT = 40.0
LINE = 14.0
COLUMNS = (0.0, 95.0, 180.0, 255.0, 340.0, 425.0)
PAGE_WIDTH = 612
PAGE_HEIGHT = 792

LETTER = "LOSS RUN REPORT"
VALUATION = "Valuation Date: 12/31/2022"

LARGE_HEADERS = (
    "Claim Number", "Loss Date", "Status", "Paid Total", "Reserve Total", "Incurred Total",
)
SMALL_HEADERS = (
    "Claim #", "Date of Loss", "Claim Status", "Total Paid", "Outstanding", "Total Incurred",
)

CARRIERS = (
    "NORTHFIELD AUTO INSURANCE COMPANY",
    "HARBOR CREST SPECIALTY INSURANCE COMPANY",
    "GENERAL CASUALTY COMPANY",
    "ALPHA MUTUAL INSURANCE",
)

#: A claim-number shape per logical run, so two runs' identifiers are visibly
#: different systems and the document's vote can keep them apart.
SHAPES = (
    lambda n: f"{71004410 + n}",
    lambda n: f"CR-{40100 + n}",
    lambda n: f"HC{90000 + n}",
    lambda n: f"A-{2000 + n}",
)

DATES = ("03/14/2022", "04/14/2022", "06/21/2022", "09/30/2022", "11/23/2022")
STATUSES = ("OPEN", "CLOSED", "CLOSED", "OPEN")


def money(value: Decimal) -> str:
    """US-formatted money exactly as it would be printed."""
    return f"{value:,.2f}"


def parse_money(text: str) -> Decimal:
    return Decimal(text.replace(",", "").replace("$", "").strip())


def parse_printed(text: str) -> Decimal:
    """Read a printed money cell in US or European convention, signed or not."""
    value = text.strip()
    negative = False
    # A trailing sign can sit either side of an accounting parenthesis:
    # "(1,250.00)-" and "1,250.00-" both mean minus.
    if value.endswith("-"):
        negative = True
        value = value[:-1]
    if value.startswith("(") and value.endswith(")"):
        negative = True
        value = value[1:-1]
    if value.startswith("-"):
        negative = True
        value = value[1:]
    value = value.replace("$", "").replace(" ", "")
    if "," in value and "." in value:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif "," in value:
        if len(value.split(",")[-1]) == 2:
            value = value.replace(",", ".")
        else:
            value = value.replace(",", "")
    number = Decimal(value)
    return -number if negative else number


@dataclass(frozen=True)
class ClaimGT:
    """One claim that was really printed, and where."""

    number: str
    run: int
    page: int
    paid: Decimal
    reserve: Decimal
    incurred: Decimal
    status: str = "CLOSED"
    date: str = "03/14/2022"

    def row(self, *, money_fmt=money) -> tuple[str, ...]:
        return (self.number, self.date, self.status,
                money_fmt(self.paid), money_fmt(self.reserve), money_fmt(self.incurred))


@dataclass
class PageSpec:
    """One printed page. ``run`` is the logical run whose furniture it carries."""

    run: int
    top: tuple[str, ...] = ()
    headers: tuple[str, ...] = LARGE_HEADERS
    rows: list[tuple[str, ...]] = field(default_factory=list)
    total: tuple[str, ...] | None = None
    after: list[str] = field(default_factory=list)
    bottom: list[str] = field(default_factory=list)
    blank: bool = False


@dataclass
class Case:
    """Truth for one generated document."""

    pages: list[PageSpec]
    claims: list[ClaimGT]
    run_count: int
    family: str = "clean"
    operators: tuple[str, ...] = ()
    seed: int = 0

    # -- truth helpers ---------------------------------------------------

    def claims_of_run(self, run: int) -> list[ClaimGT]:
        return [c for c in self.claims if c.run == run]

    def expected_total(self, run: int) -> Decimal:
        return sum((c.incurred for c in self.claims_of_run(run)), Decimal("0"))

    def expected_count(self, run: int) -> int:
        return len(self.claims_of_run(run))

    def claim_by_number(self) -> dict[str, ClaimGT]:
        return {c.number: c for c in self.claims}

    def run_of_page(self, page: int) -> int | None:
        if 1 <= page <= len(self.pages):
            return self.pages[page - 1].run
        return None

    def copy(self) -> "Case":
        return Case(
            pages=[PageSpec(**vars(p)) for p in self.pages],
            claims=list(self.claims),
            run_count=self.run_count,
            family=self.family,
            operators=self.operators,
            seed=self.seed,
        )


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def _render_page(page: pymupdf.Page, spec: PageSpec) -> None:
    if spec.blank:
        return
    y = 36.0
    for line in spec.top:
        page.insert_text((LEFT, y), line, fontsize=9)
        y += LINE
    y += LINE
    for offset, label in zip(COLUMNS, spec.headers):
        page.insert_text((LEFT + offset, y), label, fontsize=8.5)
    y += LINE
    for row in [*spec.rows, *([spec.total] if spec.total else [])]:
        for offset, cell in zip(COLUMNS, row):
            if cell:
                page.insert_text((LEFT + offset, y), cell, fontsize=8.5)
        y += LINE
    y += LINE
    for line in spec.after:
        page.insert_text((LEFT, y), line, fontsize=8)
        y += LINE
    for index, line in enumerate(spec.bottom):
        page.insert_text(
            (LEFT, PAGE_HEIGHT - 22 - LINE * (len(spec.bottom) - 1 - index)),
            line, fontsize=8,
        )


def heading_of(spec: PageSpec) -> str:
    """The page's own naming line, ignoring its page numbering."""
    lines = [line for line in spec.top if line]
    if lines and lines[0].lower().startswith("page "):
        return lines[1] if len(lines) > 1 else ""
    return lines[0] if lines else ""


def scanned_payloads(case: Case) -> dict[int, dict]:
    """The synthetic replay recording for a case: one payload per page.

    A correct model reads exactly what the page prints, so the payload is the
    page's own headers and rows. It is derived from the ground truth, not from
    a live model, and never from a real document.
    """
    payloads: dict[int, dict] = {}
    for page, spec in enumerate(case.pages, start=1):
        payloads[page] = {
            "headers": list(spec.headers),
            "rows": [{"cells": list(row), "kind": "data"} for row in spec.rows],
            "printed_claim_count": case.expected_count(spec.run),
            "valuation_date": "12/31/2022",
            "page_label": {"text": f"Page {page} of {len(case.pages)}",
                           "number": page, "of": len(case.pages), "position": "header"},
            "report_heading": heading_of(spec),
        }
    return payloads


def render(case: Case, path: Path | str) -> Path:
    """Write the case to a PDF, deterministically."""
    path = Path(path)
    document = pymupdf.open()
    for spec in case.pages:
        _render_page(document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT), spec)
    # Fixed metadata so the same case always produces the same bytes.
    document.set_metadata({
        "producer": "losslift-fuzz",
        "creator": "losslift-fuzz",
        "creationDate": "D:20200101000000Z",
        "modDate": "D:20200101000000Z",
    })
    document.save(path, garbage=0, deflate=True)
    document.close()
    return path


# --------------------------------------------------------------------------
# Clean family
# --------------------------------------------------------------------------


def _amounts(rng: random.Random) -> tuple[Decimal, Decimal]:
    paid = Decimal(rng.choice([0, 250, 500, 1000, 1800, 5000, 12345])) 
    reserve = Decimal(rng.choice([0, 0, 750, 1000, 2500]))
    if paid == 0 and reserve == 0:
        reserve = Decimal("1000")
    return paid, reserve


def _run_claims(rng, run: int, count: int, pages: list[int]) -> list[ClaimGT]:
    """``count`` claims of one run, each printed on ``pages[i]``."""
    made = []
    for n in range(count):
        paid, reserve = _amounts(rng)
        made.append(ClaimGT(
            number=SHAPES[run % len(SHAPES)](n + run * 100),
            run=run,
            page=pages[n],
            paid=paid,
            reserve=reserve,
            incurred=paid + reserve,
            # A closed claim never carries reserve, so R-08 has nothing to say
            # on a document that is otherwise clean.
            status="CLOSED" if reserve == 0 else rng.choice(("OPEN", "OPEN", "CLOSED")),
            date=rng.choice(DATES),
        ))
    return made


def _numbered(index: int, total: int) -> str:
    return f"Page {index} of {total}"


def _finish(spec: PageSpec, claims: list[ClaimGT], total_label: str = "TOTAL") -> None:
    paid = sum((c.paid for c in claims), Decimal("0"))
    reserve = sum((c.reserve for c in claims), Decimal("0"))
    incurred = paid + reserve
    spec.total = (total_label, "", "", money(paid), money(reserve), money(incurred))


def _single_run(rng, seed, *, pages=1, claims_n=None) -> Case:
    carrier = CARRIERS[0]
    claims_n = claims_n or rng.randint(1, 8)
    if pages == 2 and claims_n < 2:
        claims_n = 2
    page_count = pages if claims_n > 1 else 1
    per_page = max(1, -(-claims_n // page_count))
    page_of = [min(page_count, n // per_page + 1) for n in range(claims_n)]
    claims = _run_claims(rng, 0, claims_n, page_of)
    page_specs = []
    for page_number in range(1, page_count + 1):
        mine = [c for c in claims if c.page == page_number]
        spec = PageSpec(
            run=0,
            top=(_numbered(page_number, page_count), carrier, LETTER, VALUATION),
            headers=LARGE_HEADERS,
            rows=[c.row() for c in mine],
        )
        if page_number == page_count:
            _finish(spec, claims)
            spec.after = [f"Number of claims: {claims_n}"]
        page_specs.append(spec)
    return Case(pages=page_specs, claims=claims, run_count=1, seed=seed)


def _packet(rng, seed, runs: int) -> Case:
    pages: list[PageSpec] = []
    claims: list[ClaimGT] = []
    base = rng.randint(6, 10)
    for run in range(runs):
        count = max(2, base - run)
        made = _run_claims(rng, run, count, [run + 1] * count)
        claims.extend(made)
        spec = PageSpec(
            run=run,
            top=(_numbered(1, 1), CARRIERS[run % len(CARRIERS)], LETTER, VALUATION),
            headers=LARGE_HEADERS if run == 0 else SMALL_HEADERS,
            rows=[c.row() for c in made],
        )
        _finish(spec, made)
        spec.after = [f"Number of claims: {count}"]
        pages.append(spec)
    return Case(pages=pages, claims=claims, run_count=runs, seed=seed)


def clean_case(seed: int, *, force: str | None = None) -> Case:
    """A document a correct reader should read perfectly."""
    rng = random.Random(seed)
    kind = force or rng.choice(CLEAN_KINDS)
    if kind == "single":
        return _single_run(rng, seed, pages=1)
    if kind == "single2":
        return _single_run(rng, seed, pages=2)
    if kind == "packet2":
        return _packet(rng, seed, 2)
    if kind == "packet3":
        return _packet(rng, seed, 3)
    raise ValueError(f"unknown clean kind {kind!r}")


CLEAN_KINDS = ("single", "single2", "packet2")
