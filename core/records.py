"""Logical claims printed across several physical lines.

Most loss runs put one claim on one line. Some put it on several: the claimant
on one, the identifier and status on the next, the dates and money on a third,
under a header block with one line per record line. Read line by line, such a
document yields a claim carrying only the identifier, and every date and
amount comes back null.

The danger in fixing that is worse than the fault. Attaching the line beneath
one claim to the claim above it, when that line actually opens the next claim,
produces a complete-looking record whose money belongs to someone else — and
nothing downstream can detect it, because the totals still add up. So the
evidence has to come from the document, in two independent halves:

* the **header block** proposes a shape — how many lines a record has, and
  which of them names the claim;
* the **body** confirms it — every claim number found becomes an anchor, and a
  record is only the fixed span of lines around one anchor.

A span is accepted only when it holds exactly one identifier, contains nothing
that ends a record, sits closer together than records sit apart, and overlaps
no other span. An anchor that fails any of those keeps its lines to itself and
the claim keeps its null fields.

An incomplete claim is a question. A wrong one is an answer nobody can check.
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass
from typing import Sequence

from core.profiles import guess_field
from core.schema import DATE_FIELDS, MONEY_FIELDS

#: A cell of printed text and the horizontal span it occupies.
Cell = tuple[str, float, float]

#: A record line may sit at most this many times the page's tightest line gap
#: from its predecessor. Beyond it, the next line belongs to the next record.
MAX_INTRA_GAP_RATIO = 1.5

#: A cell shorter than this is a code or a stray digit, never a claim number.
MIN_IDENTIFIER_LENGTH = 3

#: A whole cell that is just a date. Claim numbers are not dates.
DATE_SHAPED = re.compile(r"\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4}")


# --------------------------------------------------------------------------
# What a claim number looks like in this document
# --------------------------------------------------------------------------


def identifier_shape(text: str) -> str:
    """The cell's pattern with the specifics removed: WC550C44573 -> AA999A99999.

    Claim numbers inside one document are issued by one system and share a
    shape. Comparing shapes rather than values is what lets a document say
    which of its own cells are identifiers without anyone naming the carrier.
    """
    return "".join(
        "9" if char.isdigit() else "A" if char.isalpha() else char for char in text
    )


def is_identifier_candidate(text: str) -> bool:
    """The tests any claim number passes, before the document has its say.

    A continuation line that lands in the claim-number column fails at least
    one of these: injury text carries no digit, a bare code is too short, and
    a date belongs to a date column that has bled left.
    """
    if len(text) < MIN_IDENTIFIER_LENGTH:
        return False
    if not any(char.isdigit() for char in text):
        return False
    # Date-shaped, not date-parseable: an ambiguous date like 11/11/19 does
    # not resolve without document evidence and so parses to None, which would
    # otherwise read as "not a date" and let a date column bleed in as an
    # identifier.
    return not DATE_SHAPED.fullmatch(text)


def consensus_shapes(candidates: Sequence[str]) -> set[str]:
    """Which shapes this document uses for claim numbers.

    A claim number is normally one token. Where a document has such cells they
    define what an identifier looks like here, and anything shaped differently
    is a continuation line. Where it has none — some carriers print the
    insured's name or a bled neighbouring column into the same cell — the
    shapes used most often stand in, since a layout repeats itself even when
    it is not tidy.
    """
    if not candidates:
        return set()

    single = [text for text in candidates if " " not in text]
    shapes = collections.Counter(
        identifier_shape(text) for text in (single or candidates)
    )

    # A shape has to recur to count as this document's. A claim number is
    # issued in a series, so its shape appears once per claim; a policy number
    # printed on a section total, or a mangled code that slipped the tests
    # above, appears once. The fraction admits a genuine second series -- a
    # book with both WC and GL numbering -- without admitting one-offs.
    # The floor can never exceed the most common shape's own count: that shape
    # is by definition what this document uses, and a report with a single
    # claim on it is ordinary. Requiring two would extract nothing from it.
    most_common = shapes.most_common(1)[0][1]
    floor = min(most_common, max(2, int(most_common * 0.25)))
    return {shape for shape, count in shapes.items() if count >= floor}


def leading_identifier(cell: str, shapes: set[str]) -> str | None:
    """The claim number this cell opens, or None if it opens no claim.

    A neighbouring column bleeds into the identifier cell on some pages and
    not others, so the same claim series arrives clean here and as
    "502-124958-001/8459543132US Acc/Ben: FL/" there. An identifier leads its
    cell and the contamination trails it, which is what separates this from a
    continuation line: the junk that detail layouts put in this column does
    not begin with an identifier either.
    """
    cell = cell.strip()
    if not cell or not is_identifier_candidate(cell):
        return None
    if not shapes or identifier_shape(cell) in shapes:
        return cell
    leading = cell.split()[0]
    return leading if identifier_shape(leading) in shapes else None


# --------------------------------------------------------------------------
# Reading a header block
# --------------------------------------------------------------------------


def _family(field_name: str | None) -> str | None:
    """The money group a field belongs to: paid, reserve, recovery, incurred."""
    if field_name in MONEY_FIELDS:
        return field_name.split("_")[0]
    return None


def refined_label(parent: str, own: str) -> tuple[str, bool]:
    """Join a qualifier to the label beneath it: "Ind/BI" over "Paid".

    Whether the pair *is* a qualifier and a label is not decided here — see
    ``push_qualifiers``, which asks the page rather than the vocabulary.

    Returns the label to use and whether the parent was consumed.
    """
    parent, own = parent.strip(), own.strip()
    if not parent or not own:
        return own or parent, bool(parent and not own)
    return f"{parent} {own}", True


def _overlap(left: Cell, right: Cell) -> float:
    return min(left[2], right[2]) - max(left[1], right[1])


def _partner(cell: Cell, line: Sequence[Cell]) -> Cell | None:
    """The cell in ``line`` sitting most squarely above or below ``cell``."""
    best: Cell | None = None
    widest = 0.0
    for other in line:
        overlap = _overlap(cell, other)
        if overlap > widest:
            best, widest = other, overlap
    return best


def _joins(upper: Sequence[Cell], lower: Sequence[Cell]) -> bool:
    """Whether these two printed lines are halves of one row of labels.

    They are not, if any column names one canonical field above and a
    different one below: two meanings at one position cannot be two halves of
    one label. A refinement is exempt — "Ind/BI" over "Paid" names paid and
    paid-indemnity, which is one meaning stated twice, not two.
    """
    for cell in lower:
        above = _partner(cell, upper)
        if above is None:
            continue
        upper_field = guess_field(above[0]).field
        lower_field = guess_field(cell[0]).field
        if upper_field is None or lower_field is None or upper_field == lower_field:
            continue
        if not refined_label(above[0], cell[0])[1]:
            return False
    return True


def _merge_label_lines(upper: Sequence[Cell], lower: Sequence[Cell]) -> list[Cell]:
    """Fold a wrapped header's upper half into its lower half."""
    prefixes: dict[int, list[str]] = collections.defaultdict(list)
    spans: dict[int, list[float]] = {}
    orphans: list[Cell] = []
    for cell in upper:
        partner = _partner(cell, lower)
        if partner is None:
            orphans.append(cell)
            continue
        index = lower.index(partner)
        prefixes[index].append(cell[0])
        span = spans.setdefault(index, [partner[1], partner[2]])
        span[0], span[1] = min(span[0], cell[1]), max(span[1], cell[2])

    merged: list[Cell] = list(orphans)
    for index, cell in enumerate(lower):
        text = " ".join([*prefixes.get(index, []), cell[0]]).strip()
        left, right = spans.get(index, [cell[1], cell[2]])
        merged.append((text, left, right))
    return sorted(merged, key=lambda cell: cell[1])


@dataclass(frozen=True)
class RecordLayout:
    """How tall a logical record is, and what labels each of its lines uses."""

    #: One cell list per record line, already refined against the line above.
    line_headers: list[list[Cell]]
    #: Which record line names the claim number, or None if none does.
    identifier_line: int | None = None
    #: The claim-number cell's horizontal span, for finding it in the body.
    identifier_span: tuple[float, float] | None = None

    @property
    def height(self) -> int:
        return len(self.line_headers)

    @property
    def is_multi_line(self) -> bool:
        return self.height > 1 and self.identifier_line is not None


def _fields_of(line: Sequence[Cell]) -> set[str]:
    found = {guess_field(text).field for text, _, _ in line}
    found.discard(None)
    return found  # type: ignore[return-value]


def detect_layout(header_lines: Sequence[Sequence[Cell]]) -> RecordLayout:
    """Decide whether a header block describes one line per claim, or several.

    A record header uses each line for a different part of the claim: claimant
    here, identifier there, dates and money below. So the block proposes a
    record as many lines tall as it has, and the body is left to say whether
    that is really the shape of the page — which it does by refusing to group
    around it, since a wrapped header over a flat table puts a claim number on
    every line and no span of two can then hold exactly one.

    The shape only counts as a record when it separates the claim number from
    the claim's facts, because that separation is the whole problem being
    solved. Exactly one line must name the claim number, and some other line
    must carry a date or an amount. Anything else is a header this module has
    no business touching, and it comes back one line tall.
    """
    lines = [list(line) for line in header_lines if any(text.strip() for text, _, _ in line)]
    if not lines:
        return RecordLayout([[]])
    if len(lines) < 2:
        return RecordLayout(lines)

    naming = [
        (number, cell)
        for number, line in enumerate(lines)
        for cell in line
        if guess_field(cell[0]).field == "claim_number"
    ]
    if len(naming) != 1:
        return RecordLayout(lines)
    identifier_line, identifier_cell = naming[0]

    facts = set(MONEY_FIELDS) | set(DATE_FIELDS)
    if not any(
        _fields_of(line) & facts
        for number, line in enumerate(lines)
        if number != identifier_line
    ):
        return RecordLayout(lines)

    return RecordLayout(
        lines,
        identifier_line=identifier_line,
        identifier_span=(identifier_cell[1], identifier_cell[2]),
    )


# --------------------------------------------------------------------------
# Gathering the body into records
# --------------------------------------------------------------------------


def push_qualifiers(
    line_headers: Sequence[Sequence[Cell]], occupied: Sequence[Sequence[tuple[float, float]]]
) -> list[list[Cell]]:
    """Move a label that heads no column of its own down onto the one it heads.

    AIG prints "Ind/BI" and three bare "Total"s on the record line that carries
    the claim number, and the amounts they name a line lower, under "Paid",
    "Reserves", "Recoveries", "Incurred". Read as written, the identifier line
    gains six columns it has no values in and three of them are called Total.

    Liberty prints "Incurred Indemnity" one line above "Paid Indemnity" and
    both lines carry money. Those are two columns, not a label and its
    qualifier, and joining them loses the incurred figure entirely.

    The vocabulary cannot tell these apart and does not have to: the page can.
    A label with nothing printed beneath it on its own record line is heading
    the line below, so it is moved there. ``occupied`` is where each record
    line actually has values.
    """
    lines = [list(line) for line in line_headers]
    for upper in range(len(lines) - 1):
        kept: list[Cell] = []
        for cell in lines[upper]:
            if any(
                min(cell[2], end) - max(cell[1], start) > 0
                for start, end in occupied[upper]
            ):
                kept.append(cell)
                continue
            below = _partner(cell, lines[upper + 1])
            if below is None:
                kept.append(cell)  # nothing beneath it either; leave it be
                continue
            position = lines[upper + 1].index(below)
            label, _ = refined_label(cell[0], below[0])
            lines[upper + 1][position] = (
                label,
                min(cell[1], below[1]),
                max(cell[2], below[2]),
            )
        lines[upper] = kept
    return lines


@dataclass(frozen=True)
class Grouping:
    """Body lines gathered into logical records, and the ones left over."""

    records: list[list[int]]
    ungrouped: list[int]


def group_records(
    tops: Sequence[float],
    has_identifier: Sequence[bool],
    is_boundary: Sequence[bool],
    layout: RecordLayout,
) -> Grouping | None:
    """Gather body lines into records of the layout's height, around its anchors.

    Every line carrying a claim number anchors one record, which occupies the
    fixed span of lines the header block describes: so many above the anchor,
    so many below. Nothing outside a span is ever read, which is what keeps a
    section total printed under the last claim from being absorbed into it.

    An anchor is refused when its span would hold a second claim number,
    contain a line that ends a record, sit further apart than records do, or
    overlap a span already taken. Any such refusal abandons the page: a shape
    that fits some of the claims and not others is not this page's shape, and
    the spans it does fit are then reached for rather than read. A flat table
    with a description under every other claim offers exactly that -- pair the
    description above a claim with the claim, and every pair holds one number,
    no boundary and a tight gap, while every one of them is wrong.

    The one refusal that is not fatal is a record cut off by the foot of the
    page, which says nothing about the shape and leaves that claim incomplete.
    Returns None when the page is abandoned or no record could be formed.
    """
    if not layout.is_multi_line:
        return None
    assert layout.identifier_line is not None
    height, offset = layout.height, layout.identifier_line

    gaps = [tops[i + 1] - tops[i] for i in range(len(tops) - 1)]
    limit = max(min(gaps, default=0.0), 1.0) * MAX_INTRA_GAP_RATIO

    records: list[list[int]] = []
    claimed: set[int] = set()
    for anchor in (i for i, found in enumerate(has_identifier) if found):
        start = anchor - offset
        span = list(range(start, start + height))
        if span[-1] >= len(tops):
            continue  # cut off by the foot of the page; that claim stays partial
        if (
            start < 0
            or any(index in claimed for index in span)
            or sum(has_identifier[index] for index in span) != 1
            or any(is_boundary[index] for index in span)
            or any(
                tops[span[step + 1]] - tops[span[step]] > limit
                for step in range(height - 1)
            )
        ):
            return None
        records.append(span)
        claimed.update(span)

    if not records:
        return None
    return Grouping(records, [i for i in range(len(tops)) if i not in claimed])
