"""Stage 1 — classify (spec section 5).

Count extractable characters per page.  Fewer than 50 means the page is a
scan and has to go down the vision path.

Classification is per page, not per document: carriers routinely email a
digital loss run with a scanned continuation sheet stapled on the end.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from core.evidence import _to_page_space
from core.schema import ExtractionMethod

#: A rectangle on a page: left, top, right, bottom.
Box = tuple[float, float, float, float]

#: Below this many extractable characters, a page is a scan (spec section 5).
SCANNED_CHAR_THRESHOLD = 50

#: Below this share of the page, pictures are decoration -- a logo, a seal, a
#: signature block -- and whatever they hold is not the page.
IMAGE_DOMINANT_FRACTION = 0.5


@dataclass(frozen=True)
class PageClassification:
    page: int  # 1-based, as printed
    char_count: int
    is_scanned: bool
    has_images: bool = False
    #: Share of the page covered by pictures, overlaps counted once.
    image_fraction: float = 0.0
    #: Where those pictures sit, so a caller holding the extraction can ask
    #: whether anything it read actually came off one of them.
    image_boxes: tuple[Box, ...] = ()
    #: Which of those pictures this page's own text transcribes -- an OCR
    #: layer written invisibly over a scan, not a label printed on it. Named
    #: one by one, never counted as a share of the page: a sheet can carry a
    #: scan saved as searchable beside a pasted appendix, and reading the
    #: first says nothing whatever about the second.
    transcribed_boxes: tuple[Box, ...] = ()
    #: The page's own area, so a caller can measure what share some of the
    #: pictures cover without knowing how the page is laid out.
    page_area: float = 0.0

    def unread_fraction(self, accounted: Iterable[Box] = ()) -> float:
        """Share of the page under pictures nothing has read.

        A picture counts as read when this page transcribes it, or when a
        caller that has seen the extraction says rows came off it. Everything
        else is unread source content, and this is how much of the sheet it
        covers.

        Asked per picture, not per page. Reading a table off a letterhead
        band is not a reason to call the appendix below it read.
        """
        read = {tuple(box) for box in self.transcribed_boxes}
        read.update(tuple(box) for box in accounted)
        return _covered_fraction(
            [box for box in self.image_boxes if tuple(box) not in read],
            self.page_area,
        )

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
        because the picture already shows it. See :func:`_transcribed_boxes`.

        This says only that something on the page went unread. It does not say
        the picture holds claims, and it must not be read as saying it holds
        none. A caller that knows more -- that the extractor read rows off the
        picture itself -- can say so through :meth:`unread_after`; this, which
        has not seen the extraction, cannot.
        """
        return self.unread_fraction() > IMAGE_DOMINANT_FRACTION

    def read_from(self, boxes: Iterable[Box], page: pymupdf.Page) -> tuple[Box, ...]:
        """Which of this page's pictures something at ``boxes`` came off.

        ``boxes`` are rectangles as the word extractor reports them, which is
        not the space the pictures are placed in; :func:`to_page_space` puts
        them in it first.
        """
        if not self.image_boxes:
            return ()
        placed = [to_page_space(page, box) for box in boxes]
        if not placed:
            return ()
        return tuple(
            image
            for image in self.image_boxes
            if any(_centre_in(image, box) for box in placed)
        )

    def unread_after(self, boxes: Iterable[Box], page: pymupdf.Page) -> bool:
        """Whether pictures nothing read still cover most of this page.

        ``boxes`` is where the extractor found rows. Pictures those rows stand
        on were read off; the rest of the page's pictures were not, and the
        question is whether what is left still dominates the sheet.
        """
        return (
            self.unread_fraction(self.read_from(boxes, page))
            > IMAGE_DOMINANT_FRACTION
        )

    def contains(self, box: Box, page: pymupdf.Page) -> bool:
        """Whether something read at ``box`` came off one of the pictures."""
        return bool(self.read_from([box], page))


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


def _page_box(page: pymupdf.Page) -> pymupdf.Rect:
    """The page as the pictures are placed on it and the rows are mapped onto it.

    ``page.rect`` is the page as *displayed*: on a sheet carrying ``/Rotate``
    its width and height are swapped. Picture placements are reported
    unturned and measured from the crop box, and :func:`to_page_space` puts
    rows in that same space, so this is the box the two are compared inside.
    Clipping against the displayed rect instead cuts a hundred and eighty
    points off a picture that covers the whole sheet, and a page fully under
    one picture then measures four fifths covered.
    """
    crop = page.cropbox
    return pymupdf.Rect(0.0, 0.0, crop.width, crop.height)


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
    box = _page_box(page)

    def keep(rect: pymupdf.Rect) -> None:
        clipped = rect & box
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


def _covered_fraction(rects: Iterable[Box | pymupdf.Rect], total: float) -> float:
    """Share of ``total`` under at least one picture, overlaps counted once.

    Measured against an area rather than a page, so the same sweep answers
    for all of a page's pictures and for the ones nothing has read.

    Swept column by column rather than gridded. Compressing both axes and
    asking every cell which pictures cover it is cubic: two hundred
    placements give four hundred columns and four hundred rows, and each of
    those hundred and sixty thousand cells is then asked about all two
    hundred of them. Here each column asks only which pictures span it and
    merges their vertical runs, which an image-heavy page can afford.
    """
    if not rects or total <= 0:
        return 0.0
    rects = [pymupdf.Rect(*rect) for rect in rects]
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


def _centre_in(box, other) -> bool:
    """Whether ``other``'s centre stands inside ``box``."""
    left, top, right, bottom = tuple(box)
    x0, y0, x1, y1 = tuple(other)
    return left <= (x0 + x1) / 2 <= right and top <= (y0 + y1) / 2 <= bottom


def to_page_space(page: pymupdf.Page, box) -> pymupdf.Rect:
    """A word-extractor rectangle in the space the pictures are placed in.

    The two libraries do not describe a page the same way: rows are measured
    from the media box and pictures placed from the crop box, and a page
    carrying /Rotate reports its words turned and its pictures not. Compared
    raw, a row printed squarely on a picture lands outside it. This is the
    same correction core/evidence.py makes before it draws a box around a
    claim, and it is that function, not a second answer to the same question.
    """
    return _to_page_space(page, tuple(box))


#: PDF text render mode 3 draws nothing. It is what a scanner writes when it
#: saves a page as searchable: the characters it recognised, placed over the
#: picture and left invisible because the picture already shows them.
_INVISIBLE_RENDER_MODE = 3


def _transcribed_boxes(
    page: pymupdf.Page, rects: list[pymupdf.Rect]
) -> list[pymupdf.Rect]:
    """Which of this page's pictures its own text transcribes.

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
    if not rects:
        return []
    transcribed: list[pymupdf.Rect] = []
    for rect in rects:
        on_box = [
            span
            for span in spans
            if _centre_in(rect, span.get("bbox", (0.0, 0.0, 0.0, 0.0)))
        ]
        if not on_box:
            continue
        # Asked of each picture in turn. One page can carry a scan saved as
        # searchable beside a pasted appendix, and reading the first says
        # nothing whatever about the second.
        layer = sum(
            1 for span in on_box if span.get("type") == _INVISIBLE_RENDER_MODE
        )
        if layer * 2 > len(on_box):
            transcribed.append(rect)
    return transcribed


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
            area = _page_box(page).get_area()
            pages.append(
                PageClassification(
                    page=index,
                    char_count=char_count,
                    is_scanned=char_count < threshold,
                    has_images=bool(rects),
                    image_fraction=_covered_fraction(rects, area),
                    image_boxes=tuple(tuple(rect) for rect in rects),
                    transcribed_boxes=tuple(
                        tuple(rect) for rect in _transcribed_boxes(page, rects)
                    ),
                    page_area=area,
                )
            )
    return DocumentClassification(pages=tuple(pages))
