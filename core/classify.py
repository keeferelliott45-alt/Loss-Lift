"""Stage 1 — classify (spec section 5).

Count extractable characters per page.  Fewer than 50 means the page is a
scan and has to go down the vision path.

Classification is per page, not per document: carriers routinely email a
digital loss run with a scanned continuation sheet stapled on the end.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf

from core.schema import ExtractionMethod

#: Below this many extractable characters, a page is a scan (spec section 5).
SCANNED_CHAR_THRESHOLD = 50

#: Below this share of the page, pictures are decoration -- a logo, a seal, a
#: signature block -- and whatever they hold is not the page.
IMAGE_DOMINANT_FRACTION = 0.5
#: Above this share of a page's words standing inside its pictures, the words
#: are the pictures' own text layer: a scan saved as searchable, whose text is
#: the reading of the image. Below it they sit beside the picture, which makes
#: them a caption and leaves the picture unread.
TEXT_ON_IMAGE_FRACTION = 0.5


@dataclass(frozen=True)
class PageClassification:
    page: int  # 1-based, as printed
    char_count: int
    is_scanned: bool
    has_images: bool = False
    #: Share of the page covered by pictures, overlaps counted once.
    image_fraction: float = 0.0
    #: Share of this page's words standing inside one of those pictures.
    text_on_image: float = 0.0

    @property
    def carries_unread_image(self) -> bool:
        """Pictures cover the page and its text is not their transcription.

        Two pages look alike by area and are opposites in fact. A scan saved
        with a text layer is one full-page picture, and every word extracted
        from it stands *on* that picture because the words are its reading --
        that page has been read. A screenshot pasted under a heading also
        covers the page, and its words sit outside the picture because they
        are a caption -- that page has not.

        Area alone cannot tell them apart, and on the documents this was
        measured against it would have called 67 pages of one searchable scan
        unread. Overlap separates them: those pages carry every word on the
        picture, and a pasted loss run carries none.

        This says only that something on the page went unread. It does not say
        the picture holds claims, and it must not be read as saying it holds
        none.
        """
        return (
            self.image_fraction > IMAGE_DOMINANT_FRACTION
            and self.text_on_image <= TEXT_ON_IMAGE_FRACTION
        )


@dataclass(frozen=True)
class DocumentClassification:
    pages: tuple[PageClassification, ...]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def scanned_pages(self) -> list[int]:
        return [page.page for page in self.pages if page.is_scanned]

    @property
    def digital_pages(self) -> list[int]:
        return [page.page for page in self.pages if not page.is_scanned]

    @property
    def extraction_method(self) -> ExtractionMethod:
        if not self.pages or not self.scanned_pages:
            return ExtractionMethod.DIGITAL
        if not self.digital_pages:
            return ExtractionMethod.VISION
        return ExtractionMethod.MIXED

    def is_scanned(self, page: int) -> bool:
        for classification in self.pages:
            if classification.page == page:
                return classification.is_scanned
        return False


def _image_rects(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """Every picture's placement on the page, clipped to the page itself."""
    rects: list[pymupdf.Rect] = []
    for image in page.get_images(full=True):
        for rect in page.get_image_rects(image[0]):
            clipped = rect & page.rect
            if clipped.width > 0 and clipped.height > 0:
                rects.append(clipped)
    return rects


def _covered_fraction(rects: list[pymupdf.Rect], page: pymupdf.Page) -> float:
    """Share of the page under at least one picture, overlaps counted once."""
    total = page.rect.width * page.rect.height
    if not rects or total <= 0:
        return 0.0
    # A union is never larger than the sum, so a page whose pictures do not
    # add up to the threshold cannot reach it however they overlap. Answering
    # from the sum keeps a page of a hundred small logos off the slow path.
    summed = sum(rect.width * rect.height for rect in rects)
    if summed / total <= IMAGE_DOMINANT_FRACTION:
        return summed / total
    xs = sorted({rect.x0 for rect in rects} | {rect.x1 for rect in rects})
    ys = sorted({rect.y0 for rect in rects} | {rect.y1 for rect in rects})
    covered = 0.0
    for left, right in zip(xs, xs[1:]):
        for top, bottom in zip(ys, ys[1:]):
            x = (left + right) / 2
            y = (top + bottom) / 2
            if any(r.x0 <= x <= r.x1 and r.y0 <= y <= r.y1 for r in rects):
                covered += (right - left) * (bottom - top)
    return covered / total


def _text_on_image(page: pymupdf.Page, rects: list[pymupdf.Rect]) -> float:
    """Share of this page's words standing inside one of its pictures."""
    words = page.get_text("words") or []
    if not words or not rects:
        return 0.0
    inside = 0
    for x0, top, x1, bottom, *_ in words:
        x = (x0 + x1) / 2
        y = (top + bottom) / 2
        if any(r.x0 <= x <= r.x1 and r.y0 <= y <= r.y1 for r in rects):
            inside += 1
    return inside / len(words)


def classify_pdf(
    path: str | Path, threshold: int = SCANNED_CHAR_THRESHOLD
) -> DocumentClassification:
    """Classify every page of a PDF as digital or scanned."""
    pages: list[PageClassification] = []
    with pymupdf.open(path) as document:
        for index, page in enumerate(document, start=1):
            text = page.get_text("text") or ""
            char_count = len(text.strip())
            rects = _image_rects(page)
            pages.append(
                PageClassification(
                    page=index,
                    char_count=char_count,
                    is_scanned=char_count < threshold,
                    has_images=bool(rects),
                    image_fraction=_covered_fraction(rects, page),
                    text_on_image=_text_on_image(page, rects),
                )
            )
    return DocumentClassification(pages=tuple(pages))
