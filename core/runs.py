"""Logical runs: where one loss run ends and the next begins inside a packet.

A physical PDF can bind several insurance loss runs -- a board or RFP packet
puts three carriers' reports and the policy documents between them into one
file. Each report numbers its own claims its own way, prints its own totals,
and has to be read, voted on and reconciled as itself. This module decides
which pages belong to which report, from evidence each page carries about
itself, and nothing else.

The evidence is page furniture: the "Page 3 of 8" a report prints in its
header or footer band. It is measured where it sits on the page, so the same
words in a paragraph, a table cell or an embedded form are never taken for it
(they are recorded, and ignored). The digital path measures the bands from
the text layer; the vision path asks the model what the page prints there and
marks the answer as a reading. Both produce the same :class:`PageEvidence`,
and one function turns a packet's evidence into runs.

What the evidence settles, and what it does not:

* A page printing "Page 1 of N" in its furniture opens a report.
* A page continuing a report's own count (k+1 of the same N) continues it.
* A page with no numbering after a report printed its last page ("3 of 3")
  cannot be a numbered page of that report. Under a different heading it
  opens another report. Under the same heading, or none, it may be an
  addendum to the report or another report, and nothing printed settles
  which: it is unsettled.
* A page with no numbering after a report stopped short of its last page is
  unsettled -- it may be the report's page with its footer missing, or the
  first page of another.

An unsettled run is kept apart and marked ambiguous: its pages are listed, its
claims are kept, and the document needs review. It does not get a claim-number
vote of its own, because voting alone is exactly the claim that it is a
separate report -- it is read under the vote of the run before it, and any
row that vote refuses is recorded and reported, never folded away.
* A page whose numbering breaks the count (5 of 8 after 2 of 8) is unsettled
  the same way.
* A report that restarts its numbering per section prints "Page 1" more than
  once. A restart under the same heading as the pages before it is a section
  of the same report; under a different heading it is a new report.
* Pages carrying no claims table (covers, policy documents) are not runs of
  their own. They join the run beside them, and nothing about them is
  unsettled because nothing on them can be lost.

Nothing here is inferred from claim numbers, totals or table headers. A packet
whose reports print no page numbers at all carries no boundary evidence, and
is one run -- as it always was.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from core.schema import LogicalRun, RunBoundary, RunConfidence, SourceMethod

#: A report numbering one of its own pages against its length. The count is
#: what makes it pagination: "see page 1" names a page, it does not number
#: this one.
PAGINATION = re.compile(r"\bpage\s*:?\s*(\d{1,4})\s*(?:of|/)\s*(\d{1,4})\b", re.IGNORECASE)

#: How much of the page, from the top and from the bottom, is furniture. A
#: report's page numbering and letterhead sit there; its claims table does not.
BAND_FRACTION = 0.15

#: Text that changes from page to page of one report without saying anything
#: about which report it is: print dates and times, and the page numbering.
_VOLATILE = re.compile(
    r"\b\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4}\b"      # dates
    r"|\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:[ap]\.?m\.?)?"  # times
    , re.IGNORECASE,
)

HEADER, FOOTER, MODEL = "header", "footer", "model"


@dataclass(frozen=True)
class Pagination:
    """One "Page k of N" a page prints about itself."""

    index: int
    count: int
    #: header | footer (measured on the page) | model (read by the vision model)
    source: str
    text: str


@dataclass(frozen=True)
class PageEvidence:
    """What one page prints about which report it belongs to.

    Both extraction paths produce this. ``paginations`` holds only numbering
    found in the page's furniture; ``ignored`` keeps any page-number text
    found anywhere else on the page, so a reviewer can see it was seen and
    why it did not count.
    """

    page: int
    method: SourceMethod = SourceMethod.DIGITAL
    paginations: tuple[Pagination, ...] = ()
    #: The letterhead's top line with its numbering, dates and times taken
    #: out: what stays the same on every page of one report and names it. A
    #: section title or an "ADDENDUM" line lower in the band does not change
    #: which report the page belongs to, so only the top line is used.
    identity: str | None = None
    #: The header band as printed, shortened, for display.
    heading: str | None = None
    ignored: tuple[str, ...] = ()


def paginations_in(text: str, source: str) -> list[Pagination]:
    """Every well-formed "Page k of N" in ``text`` (1 <= k <= N)."""
    found = []
    for match in PAGINATION.finditer(text or ""):
        index, count = int(match.group(1)), int(match.group(2))
        if 1 <= index <= count:
            found.append(Pagination(index, count, source, match.group(0)))
    return found


def identity_of(text: str | None) -> str | None:
    """The part of a header band that names the report, or None if nothing does."""
    if not text:
        return None
    stripped = _VOLATILE.sub(" ", PAGINATION.sub(" ", text))
    words = re.sub(r"[^\w#&]+", " ", stripped.lower()).split()
    if not any(any(ch.isalpha() for ch in word) for word in words):
        return None
    return " ".join(words)


def letterhead_identity(lines: Sequence[str]) -> str | None:
    """The identity of the top-most line that names anything."""
    for line in lines:
        identity = identity_of(line)
        if identity is not None:
            return identity
    return None


def heading_of(text: str | None, limit: int = 120) -> str | None:
    if not text:
        return None
    flat = " ".join(PAGINATION.sub(" ", text).split())
    return (flat[: limit - 1] + "…") if len(flat) > limit else (flat or None)


# --------------------------------------------------------------------------
# From page evidence to runs
# --------------------------------------------------------------------------


@dataclass
class _Segment:
    pages: list[int]
    confidence: RunConfidence
    evidence: list[RunBoundary]
    #: count -> last index seen, for each numbering the segment follows.
    tracks: dict[int, int] = field(default_factory=dict)
    ambiguous: bool = False
    ambiguity: str | None = None
    identity: str | None = None
    numbered: bool = False

    @property
    def exhausted(self) -> bool:
        """Whether the report printed its own last page."""
        if not self.tracks:
            return False
        count = min(self.tracks)  # the report's numbering, not a packet stamp
        return self.tracks[count] >= count

    def last(self) -> tuple[int, int] | None:
        if not self.tracks:
            return None
        count = min(self.tracks)
        return self.tracks[count], count


def _describe(label: Pagination, page: int) -> str:
    where = "the vision model read" if label.source == MODEL else f"the {label.source} of page {page} prints"
    return f"{where} “{label.text.strip()}”"


def _continues(segment: _Segment, labels: Sequence[Pagination]) -> list[Pagination]:
    return [label for label in labels
            if segment.tracks.get(label.count) == label.index - 1]


def _bridged(
    segment: _Segment,
    position: int,
    pages: Sequence[int],
    evidence: Mapping[int, PageEvidence],
) -> bool:
    """Whether a later page's numbering accounts for this unnumbered page exactly.

    A report's page whose footer did not read still sits inside its count: page
    2 of 4 followed by an unnumbered page and then page 4 of 4 leaves no doubt
    what the unnumbered page is. Only an exact fit bridges the gap.
    """
    last = segment.last()
    if last is None:
        return False
    index, count = last
    for ahead, page in enumerate(pages[position:], start=1):
        labels = evidence.get(page, PageEvidence(page)).paginations
        if not labels:
            continue
        return any(label.count == count and label.index == index + ahead for label in labels)
    return False


def _one_number_per_count(
    labels: Sequence[Pagination], current: "_Segment | None"
) -> tuple[list[Pagination], str | None]:
    """A page states one number for itself in any one numbering.

    Two numbers against the same count on one page ("Page 2 of 2" at the top,
    "continued from Page 1 of 2" just below it) cannot both be the page's own.
    The one continuing the report the page follows is; where neither does,
    nothing printed says which, and the page is unsettled.
    """
    by_count: dict[int, list[Pagination]] = {}
    for label in labels:
        by_count.setdefault(label.count, []).append(label)
    kept: list[Pagination] = []
    for count, group in by_count.items():
        indices = {label.index for label in group}
        if len(indices) == 1:
            kept.append(group[0])
            continue
        continuing = [
            label for label in group
            if current is not None and current.tracks.get(count) == label.index - 1
        ]
        if continuing:
            kept.append(continuing[0])
            continue
        shown = " and ".join(f"“{label.text.strip()}”" for label in group)
        return [], f"the page's furniture prints {shown}, and neither follows the page before"
    return kept, None


def _confidence(labels: Iterable[Pagination]) -> RunConfidence:
    labels = list(labels)
    if labels and all(label.source == MODEL for label in labels):
        return RunConfidence.MODEL
    return RunConfidence.PRINTED


def _segments(
    pages: Sequence[int], evidence: Mapping[int, PageEvidence]
) -> list[_Segment]:
    segments: list[_Segment] = []
    current: _Segment | None = None
    for position, page in enumerate(pages):
        page_evidence = evidence.get(page, PageEvidence(page))
        labels, conflict = _one_number_per_count(page_evidence.paginations, current)
        if conflict is not None:
            why = f"page {page}: {conflict}"
            current = _Segment(
                pages=[page], confidence=_confidence(page_evidence.paginations),
                evidence=[RunBoundary(page=page, kind="break", source="none", text=why)],
                ambiguous=True, ambiguity=why, identity=page_evidence.identity,
                numbered=True,
            )
            segments.append(current)
            continue
        openers = [label for label in labels if label.index == 1]
        continuing = _continues(current, labels) if current is not None else []

        if openers:
            opener = min(openers, key=lambda label: label.count)
            current = _Segment(
                pages=[page],
                confidence=_confidence(openers),
                evidence=[RunBoundary(
                    page=page, kind="opened", source=opener.source,
                    text=f"{_describe(opener, page)}: a report begins here",
                )],
                tracks={label.count: label.index for label in labels},
                identity=page_evidence.identity,
                numbered=True,
            )
            segments.append(current)
            continue

        if current is not None and continuing:
            current.pages.append(page)
            for label in continuing:
                current.tracks[label.count] = label.index
            continue

        if labels and current is not None and not current.numbered:
            # A report need not print its first page's number. "Page 3 of 5"
            # after unnumbered pages says its report began two pages earlier;
            # where those pages are here, unnumbered, they are its pages 1-2.
            adopted = next(
                (label for label in sorted(labels, key=lambda item: item.count)
                 if 1 <= label.index - 1 <= len(current.pages)),
                None,
            )
            if adopted is not None:
                back = adopted.index - 1
                moved = current.pages[-back:]
                current.pages = current.pages[:-back]
                if not current.pages:
                    segments.remove(current)
                opened = RunBoundary(
                    page=moved[0], kind="opened", source=adopted.source,
                    text=f"{_describe(adopted, page)}: its report began {back} page(s) "
                         f"earlier, on page {moved[0]}",
                )
                prior = [] if current.pages else list(current.evidence)
                current = _Segment(
                    pages=[*moved, page], confidence=_confidence([adopted]),
                    evidence=[opened, *prior],
                    tracks={label.count: label.index for label in labels},
                    identity=evidence.get(moved[0], page_evidence).identity
                    or page_evidence.identity,
                    numbered=True,
                )
                segments.append(current)
                continue

        if labels:
            # Numbered, but neither opening a report nor continuing the one
            # before it: pages are missing, out of order, or from elsewhere.
            label = labels[0]
            before = current.last() if current is not None else None
            why = (
                f"page {page} prints “{label.text.strip()}”, which does not "
                + (f"follow page {before[0]} of {before[1]}" if before else "begin a report")
            )
            current = _Segment(
                pages=[page], confidence=_confidence(labels),
                evidence=[RunBoundary(page=page, kind="break", source=label.source, text=why)],
                tracks={item.count: item.index for item in labels},
                ambiguous=True, ambiguity=why,
                identity=page_evidence.identity, numbered=True,
            )
            segments.append(current)
            continue

        # No numbering on this page.
        if current is None:
            current = _Segment(
                pages=[page], confidence=RunConfidence.NONE,
                evidence=[RunBoundary(
                    page=page, kind="leading", source="none",
                    text=f"page {page} prints no page number",
                )],
                identity=page_evidence.identity,
            )
            segments.append(current)
        elif not current.numbered:
            current.pages.append(page)
        elif current.exhausted:
            index, count = current.last()  # type: ignore[misc]
            ended = (
                f"the report on pages {current.pages[0]}-{current.pages[-1]} printed "
                f"its last page ({index} of {count}); page {page} prints no page number"
            )
            other = (
                page_evidence.identity is not None and current.identity is not None
                and page_evidence.identity != current.identity
            )
            if other:
                current = _Segment(
                    pages=[page], confidence=RunConfidence.INFERRED,
                    evidence=[RunBoundary(
                        page=page, kind="unnumbered", source="none",
                        text=f"{ended} under a different heading, so it begins another report",
                    )],
                    identity=page_evidence.identity,
                )
            else:
                heading = "the same heading" if page_evidence.identity else "no heading"
                why = (
                    f"{ended} under {heading}: it may be an addendum to that report "
                    f"or another report"
                )
                current = _Segment(
                    pages=[page], confidence=RunConfidence.NONE,
                    evidence=[RunBoundary(page=page, kind="unnumbered", source="none", text=why)],
                    ambiguous=True, ambiguity=why, identity=page_evidence.identity,
                )
            segments.append(current)
        elif _bridged(current, position, pages, evidence):
            current.pages.append(page)
            current.evidence.append(RunBoundary(
                page=page, kind="continued", source="none",
                text=f"page {page} prints no page number; the report's numbering "
                     f"continues across it",
            ))
            index, count = current.last()  # type: ignore[misc]
            current.tracks[count] = index + 1
        else:
            index, count = current.last()  # type: ignore[misc]
            why = (
                f"the report on pages {current.pages[0]}-{current.pages[-1]} stopped at "
                f"page {index} of {count}, and page {page} prints no page number: it may "
                f"continue that report or begin another"
            )
            current = _Segment(
                pages=[page], confidence=RunConfidence.NONE,
                evidence=[RunBoundary(page=page, kind="unnumbered", source="none", text=why)],
                ambiguous=True, ambiguity=why, identity=page_evidence.identity,
            )
            segments.append(current)
    return segments


def _merge(into: _Segment, other: _Segment, boundary: RunBoundary | None = None) -> None:
    into.pages.extend(other.pages)
    into.pages.sort()
    into.evidence.extend(other.evidence)
    if boundary is not None:
        into.evidence.append(boundary)
    into.tracks = other.tracks or into.tracks
    into.numbered = into.numbered or other.numbered
    if other.ambiguous and not into.ambiguous:
        into.ambiguous, into.ambiguity = True, other.ambiguity


def plan_runs(
    pages: Sequence[int],
    evidence: Mapping[int, PageEvidence],
    table_pages: Iterable[int],
) -> list[_Segment]:
    """Group a document's pages into logical runs.

    ``table_pages`` are the pages carrying a claims table. A group holding
    none of them is not a run -- nothing on it can be a claim -- and joins
    the run beside it.
    """
    pages = sorted(pages)
    tables = set(table_pages)
    segments = _segments(pages, evidence)
    if len(segments) > 1 and segments[0].confidence is RunConfidence.NONE \
            and not segments[0].ambiguous:
        # Pages ahead of a report's own page 1 cannot be part of that report.
        first = segments[1].pages[0]
        segments[0].confidence = RunConfidence.INFERRED
        segments[0].evidence.append(RunBoundary(
            page=segments[0].pages[-1], kind="unnumbered", source="none",
            text=f"the report beginning on page {first} prints its page 1 there, "
                 f"so the pages before it are not part of it",
        ))

    # A report numbering each section from 1 restarts under its own heading.
    merged: list[_Segment] = []
    for segment in segments:
        previous = merged[-1] if merged else None
        opened = segment.evidence and segment.evidence[0].kind == "opened"
        if (
            previous is not None and opened and previous.numbered
            and previous.exhausted and not segment.ambiguous
            and segment.identity is not None and segment.identity == previous.identity
        ):
            _merge(previous, segment, RunBoundary(
                page=segment.pages[0], kind="section", source=segment.evidence[0].source,
                text=f"numbering restarts on page {segment.pages[0]} under the same "
                     f"heading: a section of the same report",
            ))
            continue
        merged.append(segment)

    # Pages with no claims table are not a run of their own.
    runs: list[_Segment] = []
    pending: list[_Segment] = []
    for segment in merged:
        if tables & set(segment.pages):
            for held in pending:
                _merge_quietly(segment, held)
            pending = []
            runs.append(segment)
        elif runs:
            _merge_quietly(runs[-1], segment)
        else:
            pending.append(segment)
    if pending:
        if runs:
            for held in pending:
                _merge_quietly(runs[0], held)
        else:
            whole = pending[0]
            for held in pending[1:]:
                _merge_quietly(whole, held)
            runs.append(whole)
    return runs


def _merge_quietly(into: _Segment, other: _Segment) -> None:
    """Attach pages that carry no claims table; their numbering settles nothing."""
    into.pages.extend(other.pages)
    into.pages.sort()
    if other.pages:
        into.evidence.append(RunBoundary(
            page=min(other.pages), kind="attached", source="none",
            text=f"page(s) {_span(other.pages)} carry no claims table and join this run",
        ))


def _span(pages: Sequence[int]) -> str:
    pages = sorted(pages)
    return f"{pages[0]}" if len(pages) == 1 else f"{pages[0]}-{pages[-1]}"


def logical_runs(
    pages: Sequence[int],
    evidence: Mapping[int, PageEvidence],
    table_pages: Iterable[int],
    methods: Mapping[int, SourceMethod] | None = None,
) -> list[LogicalRun]:
    """The document's logical runs; empty when it is a single loss run."""
    segments = plan_runs(pages, evidence, table_pages)
    if len(segments) < 2:
        return []
    tables = set(table_pages)
    methods = methods or {}
    runs = []
    for number, segment in enumerate(segments, start=1):
        used = sorted({methods.get(page, SourceMethod.DIGITAL) for page in segment.pages
                       if page in tables}, key=lambda method: method.value)
        runs.append(LogicalRun(
            run_id=f"run-{number}",
            pages=sorted(segment.pages),
            confidence=segment.confidence,
            ambiguous=segment.ambiguous,
            ambiguity=segment.ambiguity,
            evidence=segment.evidence,
            source_methods=used,
            table_pages=sorted(tables & set(segment.pages)),
        ))
    return runs


def vote_groups(runs: Sequence[LogicalRun]) -> dict[int, int] | None:
    """Page -> the claim-number vote it takes part in; None for a single run.

    Every settled run votes on its own claim numbers. An unsettled run votes
    with the run before it: nothing printed says it is a separate report, and
    a vote of its own would say exactly that.
    """
    if len(runs) < 2:
        return None
    groups: dict[int, int] = {}
    group = -1
    for run in runs:
        if not run.ambiguous or group < 0:
            group += 1
        for page in run.pages:
            groups[page] = group
    return groups


def vision_evidence(tables: Iterable) -> dict[int, PageEvidence]:
    """The vision model's reading of each scanned page's furniture."""
    evidence: dict[int, PageEvidence] = {}
    for table in tables:
        if table.page in evidence:
            continue
        labels: tuple[Pagination, ...] = ()
        if table.page_label_index is not None and table.page_label_count is not None:
            labels = (Pagination(
                table.page_label_index, table.page_label_count, MODEL,
                table.page_label or f"Page {table.page_label_index} of {table.page_label_count}",
            ),)
        evidence[table.page] = PageEvidence(
            page=table.page,
            method=SourceMethod.VISION,
            paginations=labels,
            identity=letterhead_identity((table.heading or "").splitlines()),
            heading=heading_of(table.heading),
        )
    return evidence


def runs_overview(document, result=None) -> list[dict[str, object]]:
    """One row per logical run, for the review screen. Empty for a single run."""
    if not document.is_packet:
        return []
    statuses = result.run_status if result is not None else {}
    rows = []
    for run in document.runs:
        claims = document.run_claims(run)
        printed = run.printed_totals.get("incurred_total")
        status = statuses.get(run.run_id)
        rows.append({
            "Run": run.run_id,
            "Pages": run.page_range,
            "Settled": "no" if run.ambiguous else "yes",
            "How it is bounded": run.confidence.value,
            "Carrier": run.carrier or "",
            "Claims": len(claims),
            "Printed claim count": run.printed_claim_count,
            "Printed incurred": None if printed is None else float(printed),
            "Status": status.value if status is not None else "",
        })
    return rows


def unsettled_runs(document) -> list[tuple[str, str, str]]:
    """(run id, page range, why) for every run whose boundary is not settled."""
    return [
        (run.run_id, run.page_range, run.ambiguity or "not settled")
        for run in document.runs if run.ambiguous
    ]
