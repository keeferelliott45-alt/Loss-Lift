"""Money printed on row-labelled lines: ``Inc:``, ``Pd:``, ``O/S:``.

Some carriers print a claim on one line and its money on the lines beneath it,
one line per kind of amount, each carrying its own label: incurred, paid,
outstanding. The columns above those lines name a *component* -- Total, Claim,
Medical, Expense -- not a field. The field is the pair: the paid line under
Medical is ``paid_medical``; the outstanding line under Total is
``reserve_total``.

This module only reads such a line. Whether it belongs to the claim above it is
decided where claims are built, and the rule there is the one every multi-line
reading in this codebase keeps: a wrong attachment is worse than none.

Both vocabularies are closed. A label or a column heading outside them leaves
the line unread, so it stays reported as unplaced money rather than being
guessed into a field.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence

from core.normalize import clean_text

#: A cell that is nothing but a row label for a kind of money.
_LABEL = re.compile(
    r"(inc|incurred|pd|paid|o/s|os|outstanding|rec|recovery|recoveries)\s*:?",
    re.IGNORECASE,
)
_KIND = {
    "inc": "incurred", "incurred": "incurred",
    "pd": "paid", "paid": "paid",
    "o/s": "reserve", "os": "reserve", "outstanding": "reserve",
    "rec": "recovery", "recovery": "recovery", "recoveries": "recovery",
}
#: The component a column heading names, by its words.
_COMPONENT = {
    "total": "total",
    "claim": "indemnity", "loss": "indemnity", "indemnity": "indemnity", "ind": "indemnity",
    "medical": "medical", "med": "medical",
    "expense": "expense", "exp": "expense", "alae": "expense", "legal": "expense",
}
#: The canonical field for a kind of money in a component's column. Incurred
#: and recovery components have no field of their own: they are read, and
#: deliberately go nowhere.
FIELD = {
    ("incurred", "total"): "incurred_total",
    ("paid", "total"): "paid_total",
    ("reserve", "total"): "reserve_total",
    ("recovery", "total"): "recovery_total",
    ("paid", "indemnity"): "paid_indemnity",
    ("paid", "medical"): "paid_medical",
    ("paid", "expense"): "paid_expense",
    ("reserve", "indemnity"): "reserve_indemnity",
    ("reserve", "medical"): "reserve_medical",
    ("reserve", "expense"): "reserve_expense",
}
#: An amount as printed: sign, currency mark, separators, parentheses, a
#: trailing minus or CR. Anything else in a value cell refuses the line.
_AMOUNT = re.compile(
    r"\(?-?\s*[$€£]?\s*-?\d[\d,.\s]*\)?\s*(?:-|CR)?|-", re.IGNORECASE
)


@dataclass(frozen=True)
class LabelledLine:
    #: incurred | paid | reserve | recovery
    kind: str
    #: Canonical field -> the cell as printed.
    values: dict[str, str] = field(default_factory=dict)
    #: Whatever the line prints left of its label (a wrapped description, or
    #: the wording of a total row).
    text: str = ""


def component(heading: str) -> str | None:
    words = re.findall(r"[a-z]+", heading.lower())
    if len(words) != 1:
        return None
    return _COMPONENT.get(words[0])


def read_line(cells: Sequence[str], headers: Sequence[str]) -> LabelledLine | None:
    """The line as labelled money, or None when it is not one.

    It is one only when exactly one cell is a money label, every non-empty
    cell to its right is an amount under a column whose heading names a
    component, and at least one amount is printed.
    """
    labels = [i for i, cell in enumerate(cells) if _LABEL.fullmatch(cell.strip())]
    if len(labels) != 1:
        return None
    at = labels[0]
    kind = _KIND[cells[at].strip().rstrip(":").strip().lower()]
    values: dict[str, str] = {}
    printed = False
    for index in range(at + 1, len(cells)):
        cell = cells[index].strip()
        if not cell:
            continue
        heading = headers[index] if index < len(headers) else ""
        part = component(heading)
        if part is None or not _AMOUNT.fullmatch(cell):
            return None
        printed = True
        name = FIELD.get((kind, part))
        if name is not None:
            if name in values:
                return None  # two columns claim one field: not this layout
            values[name] = cell
    if not printed:
        return None
    return LabelledLine(kind, values, clean_text(" ".join(c for c in cells[:at] if c.strip())))


def total_blocks(table, headers: Sequence[str]) -> dict[tuple[int, int], list]:
    """Each labelled total, with every labelled line printed as part of it.

    A subtotal or grand total in this layout is the same stack as a claim's,
    and carriers lay it out in more than one order: a heading line, then
    ``Inc:``, then the claim count sharing its line with ``Pd:``, then
    ``O/S:`` -- or the heading on the ``Inc:`` line itself. So the stack is
    gathered on both sides of a labelled total row, in printed order:

    * forward, while each next line is labelled money of a kind the stack has
      not had, printing nothing left of its label unless it is itself a total
      row (whose wording is the count);
    * backward, the same way -- but only if the walk ends at a line that is not
      labelled money. A walk that ends on a repeated kind has reached the
      stack of the claim above, and then nothing behind the total is taken:
      those lines stay reported rather than risk taking a claim's money.

    Returns ``{(page, line): [joined rows]}`` keyed by each stack's leading
    total row. A total row joined into another stack is not a total of its
    own and is listed under ``(page, -line)`` so callers can skip it.
    """
    totals = {id(row) for row in table.total_rows}
    ordered = sorted([*table.rows, *table.total_rows], key=lambda r: r.line_index)
    blocks: dict[tuple[int, int], list] = {}
    taken: set[int] = set()

    def joins(row, kinds) -> LabelledLine | None:
        line = read_line(row.cells, headers)
        if line is None or line.kind in kinds:
            return None
        if line.text and id(row) not in totals:
            return None
        return line

    for at, total in enumerate(ordered):
        if id(total) not in totals or id(total) in taken:
            continue
        first = read_line(total.cells, headers)
        if first is None:
            continue
        kinds = {first.kind}
        joined: list = []
        back: list = []
        back_kinds: set[str] = set()
        index = at - 1
        bounded = True
        while index >= 0:
            row = ordered[index]
            if id(row) in taken:
                break
            line = read_line(row.cells, headers)
            if line is None:
                break
            if line.kind in kinds | back_kinds or (line.text and id(row) not in totals):
                bounded = False
                break
            back.append(row)
            back_kinds.add(line.kind)
            index -= 1
        if bounded:
            joined.extend(reversed(back))
            kinds |= back_kinds
        index = at + 1
        while index < len(ordered):
            row = ordered[index]
            line = joins(row, kinds)
            if line is None:
                break
            kinds.add(line.kind)
            joined.append(row)
            index += 1
        for row in joined:
            taken.add(id(row))
            if id(row) in totals:
                blocks[(row.page, -row.line_index)] = []
        taken.add(id(total))
        blocks[(total.page, total.line_index)] = joined
    return blocks


def block_values(total, joined: Sequence, headers: Sequence[str]) -> dict[str, str]:
    """Field -> printed cell for a labelled total row and its continuations."""
    values: dict[str, str] = {}
    for row in (total, *joined):
        line = read_line(row.cells, headers)
        if line is not None:
            values.update(line.values)
    return values
