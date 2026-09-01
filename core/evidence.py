"""Where a value came from, and how to show it on the page it came from.

Every number LossLift reports has to be answerable for: an underwriter looking
at a figure must be able to see the line of the carrier's own document it was
read from. The claim already carries the page, the line and, for digital
extraction, the rectangle those words occupied. This module turns that into
something a reviewer can look at, and — more importantly — refuses to when the
rectangle cannot be trusted.

Three things are deliberately not done here:

* **No rectangle is invented.** A page read by the vision model has none: the
  model is asked what the page says, not where on it the words sit. Saying
  "page 9" and nothing more is the honest answer, and it is the one given.
* **No rectangle is believed without checking.** A page carrying ``/Rotate 90``
  reports its words in one coordinate space and its text in another, so a
  rectangle can be arithmetically fine and point at the wrong part of the page.
  Before a region is shown it is read back, and if the claim is not inside it
  the region is withdrawn rather than drawn somewhere plausible.
* **A typed value is never dressed as a printed one.** Correcting one cell of a
  claim does not make the other nine less genuine, so provenance is answered
  per field: the corrected one says a person typed it, the rest still point at
  the line they were read from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dataclass_field
from enum import Enum
from pathlib import Path

import pymupdf

from core.schema import Claim, Finding, LossRunDocument, SourceMethod

#: How large a rendered page is, and how much room the highlight leaves round
#: the words it marks.
EVIDENCE_DPI = 130
HIGHLIGHT_PADDING = 2.0


class EvidenceKind(str, Enum):
    """How precisely the source of a value can be pointed at."""

    #: A page and a rectangle on it, read back and confirmed to hold the claim.
    REGION = "region"
    #: A page, and no rectangle anyone can vouch for.
    PAGE = "page"
    #: A person entered this. The document does not say it.
    TYPED = "typed"
    #: Nothing is known, which is itself worth saying.
    NONE = "none"


@dataclass(frozen=True)
class Evidence:
    """What can be shown about where one value came from."""

    kind: EvidenceKind
    method: SourceMethod
    note: str
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    lines: list[int] = dataclass_field(default_factory=list)
    #: The text of the cell the value was read from, before it was parsed.
    text: str | None = None

    @property
    def can_highlight(self) -> bool:
        return self.kind is EvidenceKind.REGION and self.bbox is not None

    def describe(self) -> str:
        """One line naming the source, for a table cell or an export column."""
        if self.kind is EvidenceKind.TYPED:
            return "entered by hand"
        if self.page is None:
            return "not recorded"
        where = f"page {self.page}"
        if self.lines:
            printed = ", ".join(str(n + 1) for n in self.lines)
            where += f", line {printed}" if len(self.lines) == 1 else f", lines {printed}"
        if self.kind is EvidenceKind.REGION and self.bbox:
            x0, top, x1, bottom = self.bbox
            where += f" at ({x0:.0f}, {top:.0f})-({x1:.0f}, {bottom:.0f})"
        return where


def _lines_of(claim: Claim) -> list[int]:
    if claim.source_lines:
        return list(claim.source_lines)
    return [claim.source_row] if claim.source_row is not None else []


def claim_evidence(claim: Claim, field: str | None = None) -> Evidence:
    """Where this claim, or one field of it, came from.

    Asking about a field is the more useful question and the more honest one: a
    claim edited in one cell is neither wholly typed nor wholly read.
    """
    if field is not None and field in claim.edited_fields:
        return Evidence(
            kind=EvidenceKind.TYPED,
            method=SourceMethod.MANUAL,
            note=(
                f"{_label(field)} was entered by hand on the review screen. "
                f"The document was not the source of this value."
            ),
            page=claim.source_page,
            lines=_lines_of(claim),
        )

    text = claim.raw_cells.get(field) if field else None
    lines = _lines_of(claim)
    method = claim.provenance_of(field) if field else claim.source_method

    # A claim marked manual is one of two different things: added on the review
    # screen, which has no source at all, or read off the page and then
    # corrected in a cell, which still has the row it was read from. Only the
    # first can honestly say the document is not its source.
    if claim.source_method is SourceMethod.MANUAL and claim.source_bbox is None:
        return Evidence(
            kind=EvidenceKind.TYPED,
            method=SourceMethod.MANUAL,
            note="This claim was added on the review screen, not read from the document.",
            page=claim.source_page,
            lines=lines,
            text=text,
        )

    edited = ""
    if claim.edited_fields:
        named = ", ".join(_label(name) for name in claim.edited_fields)
        edited = f" Since edited on the review screen: {named}."

    if claim.source_method is SourceMethod.VISION:
        return Evidence(
            kind=EvidenceKind.PAGE,
            method=method,
            note=(
                f"Read from a scan of page {claim.source_page} by the vision model, "
                f"which reports what the page says and not where on it the words "
                f"sit. The page can be shown; the row cannot be marked on it."
            ),
            page=claim.source_page,
            lines=lines,
            text=text,
        )

    if claim.source_bbox is None:
        return Evidence(
            kind=EvidenceKind.PAGE,
            method=method,
            note=(
                f"Read from page {claim.source_page}. The extractor did not record "
                f"where on the page, so the page can be shown but not the row."
                + edited
            ),
            page=claim.source_page,
            lines=lines,
            text=text,
        )

    return Evidence(
        kind=EvidenceKind.REGION,
        method=method,
        note=f"Read from page {claim.source_page} of the document." + edited,
        page=claim.source_page,
        bbox=claim.source_bbox,
        lines=lines,
        text=text,
    )


def finding_evidence(document: LossRunDocument, finding: Finding) -> Evidence:
    """Where the values behind a reconciliation finding came from.

    A finding about one claim points at that claim's row. A finding about the
    whole document — a column that does not tie, a claim count that does not
    match, a valuation date nobody printed — is about the document and not
    about any one row, so it points no further than the page, and often not
    even that far. Saying so is better than marking an arbitrary row.
    """
    if finding.claim_number:
        claim = next(
            (c for c in document.claims if c.claim_number == finding.claim_number), None
        )
        if claim is not None:
            return claim_evidence(claim, finding.field)

    if finding.page is not None:
        return Evidence(
            kind=EvidenceKind.PAGE,
            method=SourceMethod.DIGITAL,
            note=f"Raised against page {finding.page}.",
            page=finding.page,
        )

    return Evidence(
        kind=EvidenceKind.NONE,
        method=SourceMethod.DIGITAL,
        note=(
            "This check covers the whole document rather than one row, so there "
            "is no single place on the page to point at."
        ),
    )


def _label(field_name: str) -> str:
    return field_name.replace("_", " ").capitalize()


def _comparable(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", text.lower())


def _to_page_space(page: "pymupdf.Page", bbox: tuple[float, float, float, float]) -> "pymupdf.Rect":
    """Put a recorded rectangle into the space PyMuPDF reads and draws in.

    The two libraries do not describe a page the same way, and both differences
    move a rectangle by about a line:

    * the word extractor measures from the **media** box, PyMuPDF from the
      **crop** box, which on one document in the corpus starts nine points down
      the page -- exactly far enough to mark the row below the right one;
    * a page carrying ``/Rotate`` reports its words turned and its text
      unturned, so the rectangle has to be turned back.

    Where a page has both, which correction comes first is not something this
    corpus can settle, and the answer is not guessed at: the rectangle is read
    back before it is shown, and withdrawn if the claim is not inside it.
    """
    rect = pymupdf.Rect(*bbox) * page.derotation_matrix
    origin = page.cropbox
    return rect + (-origin.x0, -origin.y0, -origin.x0, -origin.y0)


def confirm_region(path: str | Path, evidence: Evidence, expect: str) -> Evidence:
    """Read the region back, and withdraw it if ``expect`` is not inside it.

    A rectangle is recorded in the space the word extractor reports, which for
    a rotated page is not the space the text lives in. The arithmetic gives a
    perfectly well-formed rectangle either way, and one of them is somewhere
    else on the page. Rather than trust the transform, the region is read and
    the claim looked for: found, it is shown; not found, the page is offered
    instead and the reason said out loud.
    """
    if not evidence.can_highlight or evidence.page is None:
        return evidence
    wanted = _comparable(expect)
    if not wanted:
        return evidence
    try:
        with pymupdf.open(str(path)) as document:
            if not 1 <= evidence.page <= document.page_count:
                return _withdrawn(evidence, "that page is not in the file")
            page = document[evidence.page - 1]
            found = page.get_text("text", clip=_to_page_space(page, evidence.bbox))
    except Exception:  # pragma: no cover - unreadable or deleted file
        return _withdrawn(evidence, "the document could not be reopened")

    if wanted in _comparable(found):
        return evidence
    return _withdrawn(
        evidence, "reading the marked area back did not find this claim in it"
    )


def _withdrawn(evidence: Evidence, why: str) -> Evidence:
    return Evidence(
        kind=EvidenceKind.PAGE,
        method=evidence.method,
        note=(
            f"Read from page {evidence.page}. The row is not marked because "
            f"{why}, and marking the wrong one would be worse than marking none."
        ),
        page=evidence.page,
        lines=evidence.lines,
        text=evidence.text,
    )


def render_evidence(
    path: str | Path, evidence: Evidence, *, dpi: int = EVIDENCE_DPI
) -> bytes | None:
    """The page as a PNG, with the region outlined when there is one to outline.

    Returns None when there is no page to show or the file has gone — after
    export the upload is deleted (spec section 9), and the page numbers and
    cell text stay behind without it.
    """
    if evidence.page is None:
        return None
    try:
        with pymupdf.open(str(path)) as document:
            if not 1 <= evidence.page <= document.page_count:
                return None
            page = document[evidence.page - 1]
            if evidence.can_highlight:
                x0, top, x1, bottom = evidence.bbox
                page.draw_rect(
                    _to_page_space(
                        page,
                        (
                            x0 - HIGHLIGHT_PADDING,
                            top - HIGHLIGHT_PADDING,
                            x1 + HIGHLIGHT_PADDING,
                            bottom + HIGHLIGHT_PADDING,
                        ),
                    ),
                    color=(0.85, 0.4, 0.0),
                    width=1.2,
                )
            return page.get_pixmap(dpi=dpi).tobytes("png")
    except Exception:  # pragma: no cover - unreadable or deleted file
        return None
