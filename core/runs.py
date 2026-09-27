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
PAGINATION = re.compile(
    r"\b(?:page|pg\.?|sheet)\s*:?\s*(\d{1,4})\s*(?:of|/)\s*(\d{1,4})\b", re.IGNORECASE
)

#: How much of the page, from the top and from the bottom, is furniture. A
#: report's page numbering and letterhead sit there; its claims table does not.
BAND_FRACTION = 0.15

#: Text that changes from page to page of one report without saying anything
#: about which report it is: print dates and times, and the page numbering.
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_VOLATILE = re.compile(
    r"\b\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4}\b"                 # 03/31/2023
    r"|\b" + _MONTH + r"\s+\d{1,2},?\s+\d{2,4}\b"          # March 31, 2023
    r"|\b\d{1,2}[\s-]" + _MONTH + r"[\s-]\d{2,4}\b"        # 31-Mar-2023
    r"|\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:[ap]\.?m\.?)?",       # 10:15 AM
    re.IGNORECASE,
)

#: Where a page's numbering was found. "ocr" is a text layer recognised off a
#: picture: like the vision model's answer it is a reading, not a measurement.
HEADER, FOOTER, MODEL, OCR = "header", "footer", "model", "ocr"
_READ = (MODEL, OCR)


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
    #: The header band's words, less its numbering, dates, times and the
    #: claims table's own column labels: what stays the same on every page of
    #: one report and names it. Compared by containment (see
    #: :func:`same_heading`), so a heading that adds a section title or an
    #: "ADDENDUM" line is still the report's, while two carriers sharing a
    #: generic top line are not one report.
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


def _tokens(text: str) -> list[str]:
    """Words that name a report. Anything carrying a digit -- a policy or
    account number, a print date, a count -- changes between sections and
    runs of one report, and is left out."""
    stripped = _VOLATILE.sub(" ", PAGINATION.sub(" ", text))
    words = re.sub(r"[^\w#&]+", " ", stripped.lower()).split()
    return [word for word in words if not any(ch.isdigit() for ch in word)]


def identity_of(text: str | None, exclude: Iterable[str] = ()) -> str | None:
    """The words of a header band that name the report, or None if none do.

    ``exclude`` is text that is on the band without naming anything -- the
    claims table's own column labels, where the table starts high enough to
    reach the band. A band holding nothing else names no report.
    """
    if not text:
        return None
    dropped = {token for item in exclude for token in _tokens(item)}
    words = [word for word in dict.fromkeys(_tokens(text)) if word not in dropped]
    if not any(any(ch.isalpha() for ch in word) for word in words):
        return None
    return " ".join(words)


#: Words every loss run's heading may print whoever issued it: the kind of
#: report, the insurer's corporate form, the lines of business, the labels
#: around the facts. They say what a page is, not whose. What is left -- the
#: carrier's name, the insured's -- is what names a report.
GENERIC_WORDS = frozenset("""
a about account all am an and as at by carrier claim claims co comp company
companies compensation cont continued corp corporation coverage date dated
detail details effective excluding experience for from general group history
holdings in inc including insurance insured line lines list listing llc loss
losses ltd named of on page period pm policy print printed report reports run
runs section summary term the through thru time to total totals valuation
valued with workers liability auto automobile property umbrella excess
commercial physical damage professional package inland marine casualty
specialty assurance indemnity underwriters mutual fire national business
open closed status addendum schedule
""".split())


def naming_words(identity: str | None) -> set[str]:
    """The words of a heading that name whose report it is."""
    if not identity:
        return set()
    return {word for word in identity.split() if word not in GENERIC_WORDS and len(word) > 1}


def same_heading(one: str | None, other: str | None) -> bool | None:
    """Whether two pages' headings name the same report; None if either names nothing.

    A report's pages share the words that name it -- its carrier, its insured
    -- whatever section title, "ADDENDUM" or "CONTINUED" line one of them
    adds, and a scanned page's heading as the model transcribes it may be
    shorter than the text layer's band. Two carriers' reports share none of
    those words, however alike their generic lines ("LOSS RUN REPORT").
    """
    first, second = naming_words(one), naming_words(other)
    if not first or not second:
        return None
    return bool(first & second)


def confirms(page: str | None, report: str | None) -> bool:
    """Whether a page's heading carries everything the report's heading does.

    It may add to it -- a section title, an "ADDENDUM" line -- but a heading
    that drops words the report prints on its pages (its carrier's name, with
    only a generic "Loss Run Report" left) does not confirm the page is that
    report's: it may be another report whose name is only in its logo.
    """
    wanted, printed = naming_words(report), naming_words(page)
    return bool(wanted) and wanted <= printed


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
    #: The report's own numbering: (last index seen, count). A packet-wide
    #: stamp beside it is not tracked here -- it numbers the packet, not the
    #: report, and continuing it says nothing about where the report ends.
    track: tuple[int, int] | None = None
    ambiguous: bool = False
    ambiguity: str | None = None
    #: The report stopped before its own last page and another began.
    incomplete: str | None = None
    identity: str | None = None
    numbered: bool = False
    tables: bool = False
    #: Pages joined to the segment without a heading confirming it: its
    #: claim-number vote may be pooling two reports.
    blind: set[int] = field(default_factory=set)

    @property
    def exhausted(self) -> bool:
        """Whether the report printed its own last page."""
        return self.track is not None and self.track[0] >= self.track[1]

    def continued_by(self, labels: Sequence[Pagination]) -> Pagination | None:
        if self.track is None:
            return None
        index, count = self.track
        return next((label for label in labels
                     if label.count == count and label.index == index + 1), None)

    def span(self) -> str:
        return _span(self.pages)


def _span(pages: Sequence[int]) -> str:
    pages = sorted(pages)
    return f"{pages[0]}" if len(pages) == 1 else f"{pages[0]}-{pages[-1]}"


def _describe(label: Pagination, page: int) -> str:
    where = {
        MODEL: "the vision model read",
        OCR: f"the recognised text of page {page} reads",
    }.get(label.source, f"the {label.source} of page {page} prints")
    return f"{where} \u201c{label.text.strip()}\u201d"


def _confidence(labels: Iterable[Pagination]) -> RunConfidence:
    labels = list(labels)
    if labels and all(label.source in _READ for label in labels):
        return RunConfidence.MODEL
    return RunConfidence.PRINTED


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
    for group in by_count.values():
        if len({label.index for label in group}) == 1:
            kept.append(group[0])
            continue
        continuing = current.continued_by(group) if current is not None else None
        if continuing is not None:
            kept.append(continuing)
            continue
        shown = " and ".join(f"\u201c{label.text.strip()}\u201d" for label in group)
        return [], f"the page's furniture prints {shown}, and neither follows the page before"
    return kept, None


class _Planner:
    """Walks the pages in order and cuts them into segments by their furniture."""

    def __init__(self, pages: Sequence[int], evidence: Mapping[int, PageEvidence],
                 tables: set[int]) -> None:
        self.pages = list(pages)
        self.evidence = evidence
        self.tables = tables
        self.segments: list[_Segment] = []
        self.current: _Segment | None = None
        #: Packet-wide numbering printed beside a report's own: count -> last index.
        self.stamps: dict[int, int] = {}

    # -- helpers -----------------------------------------------------------

    def page_evidence(self, page: int) -> PageEvidence:
        return self.evidence.get(page) or PageEvidence(page)

    def own_labels(self, page: int, *, record: bool = False) -> list[Pagination]:
        """The page's numbering less any packet-wide stamp it continues."""
        labels = list(self.page_evidence(page).paginations)
        stamped = [label for label in labels
                   if self.stamps.get(label.count) == label.index - 1
                   and (self.current is None or self.current.continued_by([label]) is None)]
        if record:
            for label in stamped:
                self.stamps[label.count] = label.index
        return [label for label in labels if label not in stamped]

    def start(self, page: int, **fields) -> _Segment:
        segment = _Segment(pages=[page], identity=self.page_evidence(page).identity,
                           tables=page in self.tables, **fields)
        self.segments.append(segment)
        self.current = segment
        return segment

    def add(self, segment: _Segment, page: int) -> None:
        segment.pages.append(page)
        segment.tables = segment.tables or page in self.tables
        self.current = segment

    def close_short(self, page: int) -> None:
        """A new report begins while the current one has not printed its last page."""
        current = self.current
        if current is None or not current.numbered or current.exhausted or not current.tables:
            return
        if current.track is not None and current.track[1] in self.stamps:
            return  # it was following the packet's numbering, not a report's
        index, count = current.track  # type: ignore[misc]
        current.incomplete = (
            f"the report on pages {current.span()} stops at page {index} of {count}: "
            f"its remaining page(s) are not in this PDF, or were not read as its own"
        )

    def unnumbered_run_ahead(self, position: int) -> tuple[int, Pagination | None]:
        """How many pages from here print no numbering of their own, and the
        first numbering after them."""
        count = 0
        for page in self.pages[position:]:
            labels = [label for label in self.page_evidence(page).paginations
                      if label.count not in self.stamps]
            if labels:
                return count, labels[0]
            count += 1
        return count, None

    def interrupted(self, labels: Sequence[Pagination]) -> _Segment | None:
        """The report other pages interrupted, if this page continues its own
        numbering -- an ACORD form bound inside a report numbers itself "1 of 1"
        and does not end the report around it, and pages bound between a
        report's page 1 and its page 2 do not take its numbering from it."""
        if self.current is None:
            return None
        for segment in reversed(self.segments):
            if segment is self.current or not segment.numbered or not segment.tables:
                continue
            if segment.exhausted:
                return None
            return segment if segment.continued_by(labels) is not None else None
        return None

    # -- the walk ----------------------------------------------------------

    def run(self) -> list[_Segment]:
        for position, page in enumerate(self.pages):
            self.step(position, page)
        return self.segments

    def step(self, position: int, page: int) -> None:
        page_evidence = self.page_evidence(page)
        labels, conflict = _one_number_per_count(self.own_labels(page, record=True), self.current)
        if conflict is not None:
            why = f"page {page}: {conflict}"
            self.close_short(page)
            self.start(page, confidence=_confidence(page_evidence.paginations),
                       evidence=[RunBoundary(page=page, kind="break", source="none", text=why)],
                       ambiguous=True, ambiguity=why, numbered=True)
            return

        current = self.current
        continuing = current.continued_by(labels) if current is not None else None
        openers = [label for label in labels if label.index == 1]
        if (continuing is not None
                and any(label.count < continuing.count for label in openers)
                and (not current.tables
                     or same_heading(page_evidence.identity, current.identity) is False)):
            # A report's own page 1 inside a larger numbering that carries on
            # -- after pages carrying no claims table, or under a heading
            # naming another report: the numbering followed so far was the
            # packet's, not a report's.
            self.stamps[continuing.count] = continuing.index
            labels = [label for label in labels if label is not continuing]
            continuing = None
        if continuing is not None:
            self.add(current, page)
            current.track = (continuing.index, continuing.count)
            if same_heading(page_evidence.identity, current.identity) is False:
                # The numbering carries on under a heading naming something
                # else -- a packet-wide count, or a report whose summary and
                # detail pages differ. Kept together; not confirmed.
                current.blind.add(page)
            return

        resumed = self.interrupted(labels)
        if resumed is not None:
            label = resumed.continued_by(labels)
            self.add(resumed, page)
            resumed.track = (label.index, label.count)
            resumed.evidence.append(RunBoundary(
                page=page, kind="continued", source=label.source,
                text=f"{_describe(label, page)}: the report resumes after pages that "
                     f"carry no claims table",
            ))
            return

        if openers and current is not None and current.numbered and not current.exhausted:
            # "Page 1 of N" again while the report on the pages before has not
            # printed its own last page of that count, and nothing says this
            # is another report: a back-reference ("continued from Page 1 of
            # 2"), not this page's number. The page is read as unnumbered.
            index, count = current.track  # type: ignore[misc]
            repeated = [label for label in openers if label.count == count]
            if repeated and same_heading(page_evidence.identity, current.identity) is not False:
                labels = [label for label in labels if label not in repeated]
                openers = [label for label in openers if label not in repeated]
            elif repeated:
                # Under another heading it may be another report of the same
                # length -- or the same back-reference. Nothing settles it.
                label = repeated[0]
                why = (f"page {page} prints \u201c{label.text.strip()}\u201d while the "
                       f"report on pages {current.span()} has printed only page {index} "
                       f"of {count}: another report, or a reference to that one")
                self.start(page, confidence=_confidence(repeated),
                           evidence=[RunBoundary(page=page, kind="break",
                                                 source=label.source, text=why)],
                           track=(label.index, label.count), ambiguous=True,
                           ambiguity=why, numbered=True)
                return
        if openers:
            opener = min(openers, key=lambda label: label.count)
            for label in labels:
                if label is not opener:
                    self.stamps[label.count] = label.index
            self.close_short(page)
            self.start(page, confidence=_confidence([opener]),
                       evidence=[RunBoundary(
                           page=page, kind="opened", source=opener.source,
                           text=f"{_describe(opener, page)}: a report begins here")],
                       track=(1, opener.count), numbered=True)
            return

        if labels and current is not None and not current.numbered:
            if self.adopt(page, labels):
                return

        if labels:
            label = min(labels, key=lambda item: item.count)
            before = current.track if current is not None else None
            why = (
                f"page {page} prints \u201c{label.text.strip()}\u201d, which does not "
                + (f"follow page {before[0]} of {before[1]}" if before else "begin a report")
            )
            self.close_short(page)
            self.start(page, confidence=_confidence(labels),
                       evidence=[RunBoundary(page=page, kind="break", source=label.source, text=why)],
                       track=(label.index, label.count), ambiguous=True, ambiguity=why,
                       numbered=True)
            return

        self.unnumbered(position, page, page_evidence)

    def adopt(self, page: int, labels: Sequence[Pagination]) -> bool:
        """"Page k of N" after unnumbered pages: its report began k-1 pages back."""
        current = self.current
        assert current is not None
        label = next((item for item in sorted(labels, key=lambda item: item.count)
                      if 1 <= item.index - 1 <= len(current.pages)), None)
        if label is None:
            return False
        moved_first = current.pages[-(label.index - 1)]
        if same_heading(self.page_evidence(moved_first).identity,
                        self.page_evidence(page).identity) is False:
            return False  # those pages name another report
        back = label.index - 1
        moved = current.pages[-back:]
        current.pages = current.pages[:-back]
        current.tables = bool(self.tables & set(current.pages))
        prior = [] if current.pages else list(current.evidence)
        if not current.pages:
            self.segments.remove(current)
        segment = _Segment(
            pages=[*moved, page], confidence=_confidence([label]),
            evidence=[RunBoundary(
                page=moved[0], kind="opened", source=label.source,
                text=f"{_describe(label, page)}: its report began {back} page(s) earlier, "
                     f"on page {moved[0]}"), *prior],
            track=(label.index, label.count),
            identity=self.page_evidence(moved[0]).identity or self.page_evidence(page).identity,
            numbered=True, tables=bool(self.tables & {*moved, page}),
        )
        self.segments.append(segment)
        self.current = segment
        return True

    def unnumbered(self, position: int, page: int, page_evidence: PageEvidence) -> None:
        current = self.current
        if current is None:
            self.start(page, confidence=RunConfidence.NONE, evidence=[RunBoundary(
                page=page, kind="leading", source="none",
                text=f"page {page} prints no page number")])
            return
        if not current.numbered:
            self.add(current, page)
            return

        index, count = current.track  # type: ignore[misc]
        heading = same_heading(page_evidence.identity, current.identity)
        if current.exhausted:
            ended = (f"the report on pages {current.span()} printed its last page "
                     f"({index} of {count}); page {page} prints no page number")
            if heading is False:
                self.start(page, confidence=RunConfidence.INFERRED, evidence=[RunBoundary(
                    page=page, kind="unnumbered", source="none",
                    text=f"{ended} under a different heading, so it begins another report")])
            else:
                why = (f"{ended} under {'the same heading' if heading else 'no heading'}: "
                       f"it may be an addendum to that report or another report")
                self.start(page, confidence=RunConfidence.NONE, evidence=[RunBoundary(
                    page=page, kind="unnumbered", source="none", text=why)],
                    ambiguous=True, ambiguity=why)
            return

        # The report has not printed its last page. An unnumbered page inside
        # its count is its own page with the number unread -- when the pages
        # after it account for it exactly, or when nothing after it contradicts
        # that and it is not under another report's heading.
        ahead, next_label = self.unnumbered_run_ahead(position)
        exact = (next_label is not None and next_label.count == count
                 and next_label.index == index + ahead + 1)
        fits = (heading is not False and index + ahead <= count
                and (next_label is None or next_label.index == 1))
        if exact or fits:
            self.add(current, page)
            if not exact and not confirms(page_evidence.identity, current.identity):
                current.blind.add(page)
            current.track = (index + 1, count)
            current.evidence.append(RunBoundary(
                page=page, kind="continued", source="none",
                text=f"page {page} prints no page number; it is within the report's own "
                     f"count as page {index + 1} of {count}",
            ))
            return
        why = (f"the report on pages {current.span()} stopped at page {index} of {count}, "
               f"and page {page} prints no page number: it may continue that report or "
               f"begin another")
        self.start(page, confidence=RunConfidence.NONE, evidence=[RunBoundary(
            page=page, kind="unnumbered", source="none", text=why)],
            ambiguous=True, ambiguity=why)


def _merge(into: _Segment, other: _Segment, boundary: RunBoundary | None = None) -> None:
    into.pages.extend(other.pages)
    into.pages.sort()
    into.evidence.extend(other.evidence)
    if boundary is not None:
        into.evidence.append(boundary)
    into.track = other.track or into.track
    into.numbered = into.numbered or other.numbered
    into.tables = into.tables or other.tables
    into.incomplete = other.incomplete
    into.blind |= other.blind
    if other.ambiguous and not into.ambiguous:
        into.ambiguous, into.ambiguity = True, other.ambiguity


def _merge_quietly(into: _Segment, other: _Segment) -> None:
    """Attach pages that carry no claims table; their numbering settles nothing."""
    into.pages.extend(other.pages)
    into.pages.sort()
    if other.pages:
        into.evidence.append(RunBoundary(
            page=min(other.pages), kind="attached", source="none",
            text=f"page(s) {_span(other.pages)} carry no claims table and join this run",
        ))


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
    segments = _Planner(pages, evidence, tables).run()
    for segment in segments:
        segment.pages.sort()
        segment.tables = bool(tables & set(segment.pages))
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
        heading = same_heading(segment.identity, previous.identity) if previous else None
        if (
            previous is not None and opened and previous.numbered
            and previous.exhausted and not segment.ambiguous and heading is not False
        ):
            # A restart alone is not a new report -- sections restart too.
            # Only a heading naming something else makes one.
            _merge(previous, segment, RunBoundary(
                page=segment.pages[0], kind="section", source=segment.evidence[0].source,
                text=(f"numbering restarts on page {segment.pages[0]} under the same "
                      f"heading: a section of the same report") if heading else
                     (f"numbering restarts on page {segment.pages[0]} and nothing on it "
                      f"names another report: read as a section of the same report"),
            ))
            if not confirms(segment.identity, previous.identity):
                previous.blind.update(segment.pages)
            continue
        merged.append(segment)

    # Pages with no claims table are not a run of their own, and nothing
    # unsettled about them puts a claim at risk.
    runs: list[_Segment] = []
    pending: list[_Segment] = []
    for segment in merged:
        if segment.tables:
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


@dataclass
class Plan:
    """What the pages settle about the runs a document binds."""

    #: The logical runs; empty for a single loss run.
    runs: list[LogicalRun]
    #: Whether any page prints numbering in its furniture. Without it, the
    #: claim-number vote was taken over pages nothing bounds.
    bounded: bool
    #: Pages joined to a run with nothing confirming they belong to it.
    blind: set[int]


def plan_packet(
    pages: Sequence[int],
    evidence: Mapping[int, PageEvidence],
    table_pages: Iterable[int],
    methods: Mapping[int, SourceMethod] | None = None,
) -> Plan:
    table_pages = set(table_pages)
    segments = plan_runs(pages, evidence, table_pages)
    return Plan(
        runs=_as_runs(segments, table_pages, methods),
        bounded=any(item.paginations for item in evidence.values()),
        blind={page for segment in segments for page in segment.blind},
    )


def logical_runs(
    pages: Sequence[int],
    evidence: Mapping[int, PageEvidence],
    table_pages: Iterable[int],
    methods: Mapping[int, SourceMethod] | None = None,
) -> list[LogicalRun]:
    """The document's logical runs; empty when it is a single loss run."""
    table_pages = set(table_pages)
    return _as_runs(plan_runs(pages, evidence, table_pages), table_pages, methods)


def _as_runs(
    segments: Sequence[_Segment],
    table_pages: set[int],
    methods: Mapping[int, SourceMethod] | None,
) -> list[LogicalRun]:
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
            incomplete=segment.incomplete,
            evidence=segment.evidence,
            source_methods=used,
            table_pages=sorted(tables & set(segment.pages)),
        ))
    if runs[-1].incomplete is None:
        last = segments[-1]
        if last.numbered and not last.exhausted and last.track is not None:
            index, count = last.track
            runs[-1].incomplete = (
                f"the report on pages {runs[-1].page_range} stops at page {index} of "
                f"{count}: its remaining page(s) are not in this PDF"
            )
    return runs


def vote_plan(runs: Sequence[LogicalRun]) -> tuple[dict[int, int] | None, dict[int, int]]:
    """Page -> run index for the claim-number vote, and which runs borrow a vote.

    Every settled run votes on its own claim numbers, and on nothing else's.
    An unsettled run takes the vote of the settled run before it: nothing
    printed says it is a separate report, and a vote of its own would say
    exactly that -- while pooling its rows into that run's vote would let it
    outvote the run it follows. Returns ``(None, {})`` for a single run.
    """
    if len(runs) < 2:
        return None, {}
    groups = {page: index for index, run in enumerate(runs) for page in run.pages}
    borrowed: dict[int, int] = {}
    settled = None
    for index, run in enumerate(runs):
        if run.ambiguous and settled is not None:
            borrowed[index] = settled
        elif not run.ambiguous:
            settled = index
    return groups, borrowed


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
            identity=identity_of(table.heading, exclude=table.headers),
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
