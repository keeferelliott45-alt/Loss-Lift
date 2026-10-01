"""Hazard operators for the silent-CLEAN hunt.

Each operator mutates a clean :class:`~tests.fuzz.generator.Case` in place. An
operator that does not apply to a case is a harmless no-op, so a fuzz seed can
draw any subset. Operators that print extra claim-like rows leave the ground
truth unchanged: a row the generator never counted as a claim is invented if
the pipeline reads it as one.
"""

from __future__ import annotations

import random
from dataclasses import replace
from decimal import Decimal

from tests.fuzz.generator import (
    CARRIERS,
    DATES,
    LETTER,
    LARGE_HEADERS,
    LINE,
    SHAPES,
    SMALL_HEADERS,
    VALUATION,
    Case,
    ClaimGT,
    PageSpec,
    _amounts,
    _finish,
    _numbered,
    _run_claims,
    money,
    parse_printed,
)

OPERATORS: dict[str, "Operator"] = {}


class Operator:
    __slots__ = ("name", "fn", "applies")

    def __init__(self, name, fn, applies=None):
        self.name = name
        self.fn = fn
        self.applies = applies or (lambda case: True)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Operator({self.name!r})"


def operator(name: str, applies=None):
    def register(fn):
        OPERATORS[name] = Operator(name, fn, applies)
        return fn
    return register


def _copy_page(spec: PageSpec) -> PageSpec:
    return PageSpec(
        run=spec.run,
        top=tuple(spec.top),
        headers=tuple(spec.headers),
        rows=[tuple(row) for row in spec.rows],
        total=None if spec.total is None else tuple(spec.total),
        after=list(spec.after),
        bottom=list(spec.bottom),
        blank=spec.blank,
    )


def _claim_rows(case: Case, page_index: int) -> list[int]:
    """Indexes into ``page.rows`` that print a real claim on that page."""
    numbers = {c.number for c in case.claims if c.page == page_index + 1}
    return [i for i, row in enumerate(case.pages[page_index].rows) if row and row[0] in numbers]


def _first_page_with_claim(case: Case, rng: random.Random) -> int | None:
    options = [i for i, spec in enumerate(case.pages) if _claim_rows(case, i)]
    return rng.choice(options) if options else None


# --------------------------------------------------------------------------
# Packet structure
# --------------------------------------------------------------------------


@operator("add_unnumbered_run")
def add_unnumbered_run(case: Case, rng: random.Random) -> None:
    """A trailing report with no page numbering after numbered pages."""
    run = case.run_count
    count = rng.randint(1, 3)
    page = len(case.pages) + 1
    claims = _run_claims(rng, run, count, [page] * count)
    case.claims.extend(claims)
    spec = PageSpec(
        run=run,
        top=(CARRIERS[run % len(CARRIERS)], LETTER, VALUATION),
        headers=LARGE_HEADERS if run == 0 else SMALL_HEADERS,
        rows=[c.row() for c in claims],
    )
    _finish(spec, claims)
    spec.after = [f"Number of claims: {count}"]
    case.pages.append(spec)
    case.run_count += 1


@operator("add_unnumbered_run_same_shape")
def add_unnumbered_run_same_shape(case: Case, rng: random.Random) -> None:
    """Another carrier's unnumbered report issues the first run's number shape.

    Nothing on its page says which run it belongs to, and its identifiers look
    exactly like the first run's, so a heading-blind reader can join it to the
    first run and read its claims without ever drawing a boundary.
    """
    run = case.run_count
    count = rng.randint(1, 3)
    page = len(case.pages) + 1
    made = []
    for n in range(count):
        paid, reserve = _amounts(rng)
        made.append(ClaimGT(
            number=SHAPES[0](n + 500 + run * 20),
            run=run,
            page=page,
            paid=paid,
            reserve=reserve,
            incurred=paid + reserve,
            status="CLOSED" if reserve == 0 else "OPEN",
            date=rng.choice(DATES),
        ))
    case.claims.extend(made)
    spec = PageSpec(
        run=run,
        top=(CARRIERS[run % len(CARRIERS)], LETTER, VALUATION),
        headers=LARGE_HEADERS,
        rows=[c.row() for c in made],
    )
    _finish(spec, made)
    spec.after = [f"Number of claims: {count}"]
    case.pages.append(spec)
    case.run_count += 1


@operator("bridge_foreign_page_into_count", applies=lambda case: case.run_count >= 2 and len(case.pages) >= 2)
def bridge_foreign_page_into_count(case: Case, rng: random.Random) -> None:
    """A second carrier's page claims to be page 2 of the first carrier's report."""
    if len(case.pages) < 2:
        return
    case.pages[0].top = (_numbered(1, 2), *case.pages[0].top[1:])
    case.pages[1].top = (_numbered(2, 2), *case.pages[1].top[1:])


@operator("duplicate_page")
def duplicate_page(case: Case, rng: random.Random) -> None:
    """The same page printed twice: the same claims appear on two pages."""
    copy = _copy_page(case.pages[0])
    case.pages.append(copy)


@operator("blank_page")
def blank_page(case: Case, rng: random.Random) -> None:
    """An extra page with nothing on it."""
    case.pages.append(PageSpec(run=case.pages[0].run, blank=True))


@operator("missing_page", applies=lambda case: len(case.pages) >= 3)
def missing_page(case: Case, rng: random.Random) -> None:
    """A page of a report is lost from the packet."""
    if len(case.pages) < 3:
        return
    index = rng.randrange(1, len(case.pages))
    case.pages.pop(index)
    for position, spec in enumerate(case.pages):
        if position >= index and spec.top and spec.top[0].lower().startswith("page "):
            spec.top = _renumber(spec.top)
    kept = []
    for claim in case.claims:
        if claim.page == index + 1:
            continue
        kept.append(replace(claim, page=claim.page - 1) if claim.page > index + 1 else claim)
    case.claims = kept


def _renumber(top: tuple[str, ...]) -> tuple[str, ...]:
    import re
    m = re.match(r"Page (\d+) of (\d+)", top[0])
    if not m:
        return top
    number = int(m.group(1))
    return (f"Page {max(1, number - 1)} of {m.group(2)}", *top[1:])


@operator("strip_numbering")
def strip_numbering(case: Case, rng: random.Random) -> None:
    """One page loses the only label that said which page it was."""
    candidates = [p for p in case.pages if p.top and p.top[0].lower().startswith("page ")]
    if not candidates:
        return
    page = rng.choice(candidates)
    page.top = page.top[1:]


@operator("restart_numbering", applies=lambda case: any(
    p.top and p.top[0].lower().startswith("page 2 of") for p in case.pages))
def restart_numbering(case: Case, rng: random.Random) -> None:
    """A later page renumbers itself as page 1, as a new section would."""
    candidates = [p for p in case.pages if p.top and p.top[0].lower().startswith("page 2 of")]
    if not candidates:
        return
    page = rng.choice(candidates)
    import re
    m = re.match(r"Page (\d+) of (\d+)", page.top[0])
    page.top = (f"Page 1 of {m.group(2)}", *page.top[1:])


# --------------------------------------------------------------------------
# Page furniture
# --------------------------------------------------------------------------


@operator("label_in_table_body")
def label_in_table_body(case: Case, rng: random.Random) -> None:
    """A "Page X of N" line lands inside the table as if it were a row."""
    index = _first_page_with_claim(case, rng)
    if index is None:
        return
    case.pages[index].rows.append(("Page 2 of 2", "", "", "", "", ""))


@operator("repeated_running_header")
def repeated_running_header(case: Case, rng: random.Random) -> None:
    """The carrier name is printed twice in the header band."""
    index = _first_page_with_claim(case, rng)
    if index is None:
        return
    spec = case.pages[index]
    if len(spec.top) >= 2:
        spec.top = (spec.top[0], spec.top[1], spec.top[1], *spec.top[2:])


# --------------------------------------------------------------------------
# Rows
# --------------------------------------------------------------------------


@operator("continuation_code")
def continuation_code(case: Case, rng: random.Random) -> None:
    """A cause-code line printed under a claim, in the claim-number column."""
    index = _first_page_with_claim(case, rng)
    if index is None:
        return
    code = rng.choice(("0HA-MATERIAL", "7UX-STRUCKBY", "4QE-CUTPUNCT"))
    case.pages[index].rows.append((code, "11/23/2022", "STRUCK BY", "FALLING", "STOCK", "AISLE"))


@operator("blank_status")
def blank_status(case: Case, rng: random.Random) -> None:
    """A claim row loses its status cell."""
    index = _first_page_with_claim(case, rng)
    if index is None:
        return
    row_index = rng.choice(_claim_rows(case, index))
    row = list(case.pages[index].rows[row_index])
    row[2] = ""
    case.pages[index].rows[row_index] = tuple(row)


@operator("blank_date")
def blank_date(case: Case, rng: random.Random) -> None:
    """A claim row loses its date of loss."""
    index = _first_page_with_claim(case, rng)
    if index is None:
        return
    row_index = rng.choice(_claim_rows(case, index))
    row = list(case.pages[index].rows[row_index])
    row[1] = ""
    case.pages[index].rows[row_index] = tuple(row)


@operator("missing_amounts")
def missing_amounts(case: Case, rng: random.Random) -> None:
    """A claim row loses every money cell."""
    index = _first_page_with_claim(case, rng)
    if index is None:
        return
    row_index = rng.choice(_claim_rows(case, index))
    row = list(case.pages[index].rows[row_index])
    row[3] = row[4] = row[5] = ""
    case.pages[index].rows[row_index] = tuple(row)


@operator("unplaced_money")
def unplaced_money(case: Case, rng: random.Random) -> None:
    """A money-bearing row whose claim number could not be identified."""
    index = _first_page_with_claim(case, rng)
    if index is None:
        return
    case.pages[index].rows.append(("", "", "", "500.00", "0.00", "500.00"))


@operator("wrapped_narrative")
def wrapped_narrative(case: Case, rng: random.Random) -> None:
    """A claim's narrative wraps onto the line below it."""
    index = _first_page_with_claim(case, rng)
    if index is None:
        return
    row_index = rng.choice(_claim_rows(case, index))
    case.pages[index].rows.insert(row_index + 1, ("", "COVERAGE CONTINUED", "", "", "", ""))


# --------------------------------------------------------------------------
# Values and totals
# --------------------------------------------------------------------------


def _euro(text: str) -> str:
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


@operator("european_amounts")
def european_amounts(case: Case, rng: random.Random) -> None:
    """Every money cell printed in the European convention."""
    def convert(spec: PageSpec) -> None:
        conv = []
        for row in spec.rows:
            row = list(row)
            for cell_index in (3, 4, 5):
                if row[cell_index] and any(ch.isdigit() for ch in row[cell_index]):
                    row[cell_index] = _euro(row[cell_index])
            conv.append(tuple(row))
        spec.rows = conv
        if spec.total:
            total = list(spec.total)
            for cell_index in (3, 4, 5):
                if total[cell_index]:
                    total[cell_index] = _euro(total[cell_index])
            spec.total = tuple(total)

    for spec in case.pages:
        convert(spec)


@operator("wrong_total", applies=lambda case: any(p.total for p in case.pages))
def wrong_total(case: Case, rng: random.Random) -> None:
    """The printed total is not the sum of the printed claims."""
    page = next(p for p in case.pages if p.total)
    total = list(page.total)
    total[5] = money(parse_printed(total[5]) + Decimal("1000.00"))
    page.total = tuple(total)


@operator("parentheses_total", applies=lambda case: any(p.total for p in case.pages))
def parentheses_total(case: Case, rng: random.Random) -> None:
    """The printed incurred total prints as an accounting negative."""
    page = next(p for p in case.pages if p.total)
    total = list(page.total)
    total[5] = f"({total[5]})"
    page.total = tuple(total)


@operator("wrong_count", applies=lambda case: any(p.after for p in case.pages))
def wrong_count(case: Case, rng: random.Random) -> None:
    """The printed claim count disagrees with the printed claims."""
    page = next(p for p in case.pages if p.after)
    page.after = [f"Number of claims: {rng.randint(2, 40)}" if "Number of claims" in line else line
                  for line in page.after]


@operator("total_from_other_run", applies=lambda case: case.run_count >= 2)
def total_from_other_run(case: Case, rng: random.Random) -> None:
    """A run prints another run's grand total."""
    with_total = [p for p in case.pages if p.total]
    if len(with_total) < 2:
        return
    with_total[1].total = tuple(with_total[0].total)


@operator("trailing_minus_amounts")
def trailing_minus_amounts(case: Case, rng: random.Random) -> None:
    """Every money cell prints its sign after the figure: ``1,000.00-``."""
    def convert(spec: PageSpec) -> None:
        rows = []
        for row in spec.rows:
            row = list(row)
            for cell_index in (3, 4, 5):
                if row[cell_index] and any(ch.isdigit() for ch in row[cell_index]):
                    row[cell_index] = f"{row[cell_index]}-"
            rows.append(tuple(row))
        spec.rows = rows
        if spec.total:
            total = list(spec.total)
            for cell_index in (3, 4, 5):
                if total[cell_index]:
                    total[cell_index] = f"{total[cell_index]}-"
            spec.total = tuple(total)

    for spec in case.pages:
        convert(spec)
    # The printed figure now means a negative amount: the truth moves with it.
    case.claims = [replace(c, paid=-c.paid, reserve=-c.reserve, incurred=-c.incurred)
                   for c in case.claims]


@operator("dash_zero_amounts")
def dash_zero_amounts(case: Case, rng: random.Random) -> None:
    """A zero money cell prints as ``-0-`` or ``--`` instead."""
    def blank_zero(spec: PageSpec) -> None:
        rows = []
        for row in spec.rows:
            row = list(row)
            for cell_index in (3, 4, 5):
                if row[cell_index] in ("0.00", "0"):
                    row[cell_index] = rng.choice(("-0-", "--"))
            rows.append(tuple(row))
        spec.rows = rows

    for spec in case.pages:
        blank_zero(spec)


@operator("mixed_locale", applies=lambda case: len(case.pages) >= 2)
def mixed_locale(case: Case, rng: random.Random) -> None:
    """One page prints US money, another European."""
    def convert(spec: PageSpec) -> None:
        rows = []
        for row in spec.rows:
            row = list(row)
            for cell_index in (3, 4, 5):
                if row[cell_index] and any(ch.isdigit() for ch in row[cell_index]):
                    row[cell_index] = _euro(row[cell_index])
            rows.append(tuple(row))
        spec.rows = rows
        if spec.total:
            total = list(spec.total)
            for cell_index in (3, 4, 5):
                if total[cell_index]:
                    total[cell_index] = _euro(total[cell_index])
            spec.total = tuple(total)

    convert(case.pages[0])


@operator("duplicate_claim_number")
def duplicate_claim_number(case: Case, rng: random.Random) -> None:
    """The same claim row is printed twice on one page."""
    index = _first_page_with_claim(case, rng)
    if index is None:
        return
    row_index = rng.choice(_claim_rows(case, index))
    case.pages[index].rows.insert(row_index + 1, tuple(case.pages[index].rows[row_index]))


@operator("split_claim_across_pages", applies=lambda case: any(
    case.pages[i].run == case.pages[i - 1].run for i in range(1, len(case.pages))))
def split_claim_across_pages(case: Case, rng: random.Random) -> None:
    """A claim's figures land on the next page, its number left behind."""
    candidates = [i for i in range(1, len(case.pages)) if case.pages[i].run == case.pages[i - 1].run]
    if not candidates:
        return
    index = rng.choice(candidates)
    above = case.pages[index - 1]
    row_indexes = _claim_rows(case, index - 1)
    if not row_indexes:
        return
    row_index = row_indexes[-1]
    row = list(above.rows[row_index])
    carried = (row[0], "", row[2], row[3], row[4], row[5])
    row[3] = row[4] = row[5] = ""
    above.rows[row_index] = tuple(row)
    case.pages[index].rows.insert(0, tuple(carried))


ALL_OPERATOR_NAMES = tuple(OPERATORS)
