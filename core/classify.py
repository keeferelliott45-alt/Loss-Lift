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
#: Above this share of a page's text transcribing its pictures, the page is a
#: scan saved as searchable and its text is the reading of the image. Below
#: it, nothing on the page claims to have read the picture.
TEXT_ON_IMAGE_FRACTION = 0.5


@dataclass(frozen=True)
class PageClassification:
    page: int  # 1-based, as printed
    char_count: int
    is_scanned: bool
    has_images: bool = False
    #: Share of the page covered by pictures, overlaps counted once.
    image_fraction: float = 0.0
    #: Share of this page's text that transcribes one of those pictures --
    #: an OCR layer written invisibly over a scan, not a label printed on it.
    text_on_image: float = 0.0
    #: Where those pictures sit, so a caller holding the extraction can ask
    #: whether anything it read actually came off one of them.
    image_boxes: tuple[tuple[float, float, float, float], ...] = ()

    @property
    def carries_unread_image(self) -> bool:
        """Pictures cover the page and nothing on it transcribes them.

        Two pages look alike by area and are opposites in fact. A scan saved
        as searchable is one full-page picture carrying the characters a
        scanner recognised in it; a raster appendix under a stamped label is
        one full-page picture carrying words about it. Both put text inside
        the picture, so where the words sit settles nothing -- measured on
        real documents, the sparsest genuine scan carries less text over its
        image than a page of labels does, by every geometric measure tried.

        What separates them is that an OCR layer is written invisibly,
        because the picture already shows it. See :func:`_transcribed_fraction`.

        This says only that something on the page went unread. It does not say
        the picture holds claims, and it must not be read as saying it holds
        none. A caller that knows more -- that the extractor read rows off the
        picture itself -- can say so; this cannot.
        """
        return (
            self.image_fraction > IMAGE_DOMINANT_FRACTION
            and self.text_on_image <= TEXT_ON_IMAGE_FRACTION
        )

    def contains(self, box: tuple[float, float, float, float]) -> bool:
        """Whether something read at ``box`` came off one of the pictures."""
        x = (box[0] + box[2]) / 2
        y = (box[1] + box[3]) / 2
        return any(
            left <= x <= right and top <= y <= bottom
            for left, top, right, bottom in self.image_boxes
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
    """Every picture's placement on the page, clipped to the page itself.

    Two sources, because neither sees everything. The resources name the
    pictures stored as objects and reused; the page's own blocks report what
    was actually drawn, including a picture written straight into the content
    stream, which is in no resource dictionary at all and which asking the
    resources therefore answers "none" to.

    Placements are kept apart and repeats are dropped. A picture printed
    twice covers two parts of the page and both count; the same placement
    reached twice is still one piece of page.
    """
    rects: list[pymupdf.Rect] = []
    seen: set[tuple[float, ...]] = set()

    def keep(rect: pymupdf.Rect) -> None:
        clipped = rect & page.rect
        if clipped.width <= 0 or clipped.height <= 0:
            return
        # Rounded, because the two sources describe the same placement to
        # different precision and a hair's difference is not a second picture.
        key = tuple(round(value, 1) for value in tuple(clipped))
        if key in seen:
            return
        seen.add(key)
        rects.append(clipped)

    # get_image_rects answers for an xref, not for the name it was reached
    # by, so asking once per name repeats every placement: two names over two
    # placements gave four rectangles, and two hundred would give forty
    # thousand -- a list both passes below walk again for every word.
    for xref in dict.fromkeys(image[0] for image in page.get_images(full=True)):
        for rect in page.get_image_rects(xref):
            keep(rect)

    for block in page.get_text("dict").get("blocks", ()):
        if block.get("type") == 1:
            keep(pymupdf.Rect(block["bbox"]))
    return rects


def _covered_fraction(rects: list[pymupdf.Rect], page: pymupdf.Page) -> float:
    """Share of the page under at least one picture, overlaps counted once.

    Swept column by column rather than gridded. Compressing both axes and
    asking every cell which pictures cover it is cubic: two hundred
    placements give four hundred columns and four hundred rows, and each of
    those hundred and sixty thousand cells is then asked about all two
    hundred of them. Here each column asks only which pictures span it and
    merges their vertical runs, which an image-heavy page can afford.
    """
    total = page.rect.width * page.rect.height
    if not rects or total <= 0:
        return 0.0
    xs = sorted({rect.x0 for rect in rects} | {rect.x1 for rect in rects})
    covered = 0.0
    for left, right in zip(xs, xs[1:]):
        width = right - left
        if width <= 0:
            continue
        spans = sorted(
            (rect.y0, rect.y1)
            for rect in rects
            if rect.x0 <= left and rect.x1 >= right
        )
        height = 0.0
        top = bottom = None
        for start, end in spans:
            if bottom is None:
                top, bottom = start, end
            elif start > bottom:
                height += bottom - top
                top, bottom = start, end
            elif end > bottom:
                bottom = end
        if bottom is not None:
            height += bottom - top
        covered += width * height
    return covered / total


#: PDF text render mode 3 draws nothing. It is what a scanner writes when it
#: saves a page as searchable: the characters it recognised, placed over the
#: picture and left invisible because the picture already shows them.
_INVISIBLE_RENDER_MODE = 3


def _transcribed_fraction(page: pymupdf.Page, rects: list[pymupdf.Rect]) -> float:
    """Share of this page's text that transcribes one of its pictures.

    Where the words sit cannot answer whether a picture was read. A scan
    saved with an OCR layer and a raster appendix under a stamped label both
    put words inside the picture, and measured on real documents the sparsest
    genuine scan carries less text over its image -- by share of words, by
    area covered, by height spanned -- than a page of overlaid labels does.
    Every geometric line that could be drawn puts genuine scans on both sides
    of it.

    The file says which it is. An OCR layer is drawn in render mode 3,
    invisible because the picture underneath already shows it; a label meant
    for a reader is drawn to be seen. That is the producer's own statement
    about what the text is for, and it is what this counts.

    A page with no transcription over its pictures scores zero, which is the
    honest answer: nothing on it claims to have read them.
    """
    spans = page.get_texttrace() or []
    if not spans or not rects:
        return 0.0
    transcribed = 0
    for span in spans:
        if span.get("type") != _INVISIBLE_RENDER_MODE:
            continue
        x0, top, x1, bottom = span.get("bbox", (0.0, 0.0, 0.0, 0.0))
        x = (x0 + x1) / 2
        y = (top + bottom) / 2
        if any(r.x0 <= x <= r.x1 and r.y0 <= y <= r.y1 for r in rects):
            transcribed += 1
    return transcribed / len(spans)


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
                    text_on_image=_transcribed_fraction(page, rects),
                    image_boxes=tuple(tuple(rect) for rect in rects),
                )
            )
    return DocumentClassification(pages=tuple(pages))
