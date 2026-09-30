"""Canonical data model (spec section 3).

Every carrier format normalises into these models.

Money is always ``Decimal``.  Floats are rejected at the validation boundary
rather than silently coerced: a float round-trip produces reconciliation
failures that are not real, and reconciliation is the product.

Nothing here imports any other LossLift module, so the schema can be used
from any stage without circular imports.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import json
import re
from typing import Annotated, Any, Iterable, Sequence
from uuid import uuid4

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------


class ExtractionMethod(str, Enum):
    """How a *document* was extracted."""

    DIGITAL = "digital"
    VISION = "vision"
    MIXED = "mixed"


class SourceMethod(str, Enum):
    """How a *single claim row* was extracted."""

    DIGITAL = "digital"
    VISION = "vision"
    MANUAL = "manual"


class ClaimStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    #: Closed with payment, which some carriers print separately from a
    #: closed-without-payment claim; the distinction changes frequency counts.
    CLOSED_PAID = "CLOSED_PAID"
    REOPENED = "REOPENED"
    #: Reported but never opened as a claim. Carries no money and must not be
    #: counted as a loss, but is real information about reporting behaviour.
    REPORT_ONLY = "REPORT_ONLY"
    UNKNOWN = "UNKNOWN"


class DeductibleBasis(str, Enum):
    """Whether amounts are stated before or after the deductible.

    Never inferred. A net loss run read as gross understates every claim by the
    deductible, and the sheet looks perfectly reasonable while it does so.
    """

    GROSS = "gross"
    NET = "net"
    UNKNOWN = "unknown"


class AlaeTreatment(str, Enum):
    """Whether ALAE sits inside the indemnity figures or beside them."""

    INCLUDED = "included"
    SEPARATE = "separate"
    UNKNOWN = "unknown"


class LineOfBusiness(str, Enum):
    WC = "WC"
    GL = "GL"
    AUTO = "AUTO"
    PROP = "PROP"
    UMB = "UMB"
    OTHER = "OTHER"


class Severity(str, Enum):
    ERROR = "ERROR"
    WARN = "WARN"
    INFO = "INFO"


class FindingScope(str, Enum):
    """What kind of document object a finding identifies."""

    CLAIM = "claim"
    CLAIM_GROUP = "claim_group"
    DOCUMENT = "document"
    COLUMN = "column"


class FindingCategory(str, Enum):
    FINANCIAL = "financial"
    EXTRACTION = "extraction"
    UNDERWRITING = "underwriting"


class DocumentStatus(str, Enum):
    """The badge at the top of the review screen."""

    CLEAN = "CLEAN"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class NullReason(str, Enum):
    """Why a field is null.

    A null is never a zero.  Every null carries one of these so the review
    screen can tell the user what happened and how to fix it.
    """

    EMPTY = "EMPTY"                               # blank cell: no data
    NOT_APPLICABLE = "NOT_APPLICABLE"             # N/A, NA, n/a
    DASH_PLACEHOLDER = "DASH_PLACEHOLDER"         # "--": zero or null per carrier
    AMBIGUOUS_SEPARATOR = "AMBIGUOUS_SEPARATOR"   # 1.234 with no locale evidence
    AMBIGUOUS_DATE_ORDER = "AMBIGUOUS_DATE_ORDER" # 03/04/2024 with no date evidence
    UNPARSEABLE = "UNPARSEABLE"                   # text where a number belongs
    INVALID_DATE = "INVALID_DATE"                 # 2024-02-30
    OUT_OF_RANGE = "OUT_OF_RANGE"                 # year 0007
    MISSING_COLUMN = "MISSING_COLUMN"             # the source has no such column


#: Reasons that mean "a human should look at this" (drives R-15).
#: EMPTY and NOT_APPLICABLE are unambiguous statements of *no data* rather
#: than parse failures, so they do not raise a finding on their own.
REVIEW_REASONS: frozenset[NullReason] = frozenset(
    {
        NullReason.DASH_PLACEHOLDER,
        NullReason.AMBIGUOUS_SEPARATOR,
        NullReason.AMBIGUOUS_DATE_ORDER,
        NullReason.UNPARSEABLE,
        NullReason.INVALID_DATE,
        NullReason.OUT_OF_RANGE,
    }
)


# --------------------------------------------------------------------------
# Money
# --------------------------------------------------------------------------


def _reject_float(value: Any) -> Any:
    """Refuse floats for money fields.

    ``Decimal(0.1)`` is not ``Decimal("0.1")``; letting a float in here is how
    a reconciliation engine starts reporting one-cent errors that do not exist.
    """
    if isinstance(value, float):
        raise ValueError(
            "money fields must be Decimal, int or str — never float "
            f"(got {value!r}); use Decimal(str(value))"
        )
    return value


Money = Annotated[Decimal, BeforeValidator(_reject_float)]


# --------------------------------------------------------------------------
# Claim
# --------------------------------------------------------------------------


class Claim(BaseModel):
    """One row of a loss run, normalised.

    ``field_issues`` and ``raw_cells`` together are the audit trail: for any
    field an underwriter can see the text that was on the page and, when the
    value is null, why.
    """

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    claim_number: str
    date_of_loss: date | None = None
    date_reported: date | None = None
    claim_status: ClaimStatus = ClaimStatus.UNKNOWN
    claimant_name: str | None = None
    loss_description: str | None = None
    cause_of_loss: str | None = None

    paid_indemnity: Money | None = None
    paid_medical: Money | None = None
    paid_expense: Money | None = None
    paid_total: Money | None = None
    reserve_indemnity: Money | None = None
    reserve_medical: Money | None = None
    reserve_expense: Money | None = None
    reserve_total: Money | None = None
    recovery_total: Money | None = None
    incurred_total: Money | None = None

    litigation_flag: bool | None = None
    #: The carrier's own claimant identifier, when it prints one. Distinct from
    #: claimant_name: a reference is not personal data, a name is.
    claimant_ref: str | None = None
    close_date: date | None = None
    loss_state: str | None = None
    deductible_basis: DeductibleBasis = DeductibleBasis.UNKNOWN
    alae_treatment: AlaeTreatment = AlaeTreatment.UNKNOWN

    # Workers' comp only; null on every other line of business.
    body_part: str | None = None
    nature_of_injury: str | None = None
    ncci_class_code: str | None = None
    medical_only_flag: bool | None = None

    # Provenance — spec section 2, principle 2.
    source_page: int = 1
    source_method: SourceMethod = SourceMethod.DIGITAL
    source_row: int | None = None
    source_bbox: tuple[float, float, float, float] | None = None

    #: Which physical row of the document this claim is, for as long as the
    #: claim exists. Everything else on the row is something a reviewer may
    #: change -- the claim number included, and carriers print the same one
    #: twice -- so nothing else can say *which* claim a decision was about.
    #: Assigned once from the page and line the claim was read from, never
    #: regenerated, and readable as what it means: ``p3r17`` is page 3, line
    #: 18 as printed. See :func:`assign_row_ids`.
    row_id: str = ""

    #: Every printed line this claim was read from, when a carrier spreads one
    #: claim over several. ``source_row`` names the line carrying the claim
    #: number; these name all of them, so a reviewer looking at a seven-line
    #: Liberty record can see which lines it was assembled from.
    source_lines: list[int] = Field(default_factory=list)

    #: Fields a person typed. The rest of the claim still came off the page, so
    #: this is kept per field rather than collapsing the whole row to manual:
    #: editing one amount must not cost the other nine their provenance.
    edited_fields: list[str] = Field(default_factory=list)

    #: What the extractor read, for every field a person has since changed.
    #: The corrected value replaces the old one in the field itself; this keeps
    #: the original so the two can always be told apart, which is the whole
    #: point of the audit trail.
    original_values: dict[str, str] = Field(default_factory=dict)
    field_confidence: dict[str, float] = Field(default_factory=dict)
    field_issues: dict[str, NullReason] = Field(default_factory=dict)

    #: Why a field was null, for every field a person has since filled in.
    #: Typing a number over an unreadable cell answers the question; it does
    #: not un-ask it, and an audit still needs to know the carrier printed
    #: something LossLift could not resolve. Kept beside
    #: :attr:`original_values` for the same reason.
    original_issues: dict[str, NullReason] = Field(default_factory=dict)
    raw_cells: dict[str, str] = Field(default_factory=dict)
    currency: str | None = None

    #: How a claim was read, when a person has since edited part of it. The
    #: claim-level ``source_method`` says "manual" once any cell is corrected,
    #: which is true of the row and false of its other nine fields.
    read_method: SourceMethod | None = None

    def where(self) -> str:
        """Where this claim came from, in words an audit can read.

        The same place :attr:`row_id` names, said rather than encoded. Lines
        are numbered as the page prints them, from one.
        """
        if self.source_method is SourceMethod.MANUAL and self.read_method is None:
            return "added on the review screen"
        if self.source_row is None:
            return f"page {self.source_page}"
        return f"page {self.source_page}, line {self.source_row + 1}"

    def provenance_of(self, field_name: str) -> SourceMethod:
        """How this particular field came to hold its value.

        The authoritative answer, and the only honest one: a claim read off the
        page and then corrected in one cell is manual in that cell and digital
        in the rest. ``source_method`` describes the row -- it says manual as
        soon as any cell is touched -- so it cannot answer for a field.

        A claim a person added outright has no reading behind it, and every
        field of it is manual.
        """
        if field_name in self.edited_fields:
            return SourceMethod.MANUAL
        return self.read_method or self.source_method

    def original_of(self, field_name: str) -> str | None:
        """What the extractor read, for a field a person has since changed."""
        return self.original_values.get(field_name)

    def original_issue_of(self, field_name: str) -> NullReason | None:
        """Why the extractor left a field null, before a person filled it in."""
        return self.original_issues.get(field_name)

    @property
    def confidence(self) -> float:
        """The row's confidence: the least confident field in it.

        A row is only as trustworthy as its worst cell, so this is a minimum
        and not an average — averaging lets one unreadable amount hide behind
        nine clean ones.
        """
        return min(self.field_confidence.values(), default=1.0)

    @property
    def is_medical_only(self) -> bool:
        """Medical paid or reserved, with no indemnity either side.

        Reported rather than inferred where the carrier states it: this is the
        fallback when it does not.
        """
        if self.medical_only_flag is not None:
            return self.medical_only_flag
        medical = (self.paid_medical or Decimal("0")) + (self.reserve_medical or Decimal("0"))
        indemnity = (self.paid_indemnity or Decimal("0")) + (self.reserve_indemnity or Decimal("0"))
        return medical > 0 and indemnity == 0

    @field_validator("claim_number")
    @classmethod
    def _claim_number_not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("claim_number may not be blank")
        return text

    @field_validator("field_confidence")
    @classmethod
    def _confidence_in_unit_interval(cls, value: dict[str, float]) -> dict[str, float]:
        for field, score in value.items():
            if not 0.0 <= float(score) <= 1.0:
                raise ValueError(
                    f"field_confidence[{field!r}] must be between 0 and 1, got {score!r}"
                )
        return value

    def issue(self, field: str) -> NullReason | None:
        """The reason ``field`` is null, if it is null for a recorded reason."""
        return self.field_issues.get(field)

    def confidence_for(self, field: str, default: float = 1.0) -> float:
        """One field's confidence. The row's own is the ``confidence`` property."""
        return float(self.field_confidence.get(field, default))

    def needs_review(self) -> bool:
        return any(reason in REVIEW_REASONS for reason in self.field_issues.values())


def assign_row_ids(claims: Iterable["Claim"]) -> None:
    """Give every claim that lacks one an identity, in place.

    The identity is the claim's own evidence, written down: the page and the
    line it was read from. That is the one thing about a row a reviewer cannot
    edit, which is what makes it the only safe answer to "which claim was this
    decision about". A claim number is not — carriers print the same one twice,
    and a reviewer correcting a duplicate changes it.

    Two properties matter and are both deliberate:

    * **Nothing is ever reassigned.** A claim that already has an identity
      keeps it through every edit, so a decision recorded against it stays
      attached to the row it was taken about.
    * **It is not opaque.** ``p3r17`` is page 3, line 18 as printed, and
      :meth:`Claim.where` says the same thing in words. An audit that cannot
      explain what an identifier means has not really identified anything, so
      no uuid appears here.

    A row typed on the review screen has no page to name and says so. Where a
    page and line genuinely cannot separate two rows the identity is suffixed
    rather than duplicated, because two claims sharing one is the failure this
    exists to prevent.
    """
    claims = list(claims)
    taken = {claim.row_id for claim in claims if claim.row_id}
    added = 0
    for claim in claims:
        if claim.row_id:
            continue
        if claim.source_method is SourceMethod.MANUAL and claim.read_method is None:
            added += 1
            stem = f"added{added}"
        elif claim.source_row is None:
            stem = f"p{claim.source_page}"
        else:
            stem = f"p{claim.source_page}r{claim.source_row}"
        identity, suffix = stem, 1
        while identity in taken:
            suffix += 1
            identity = f"{stem}-{suffix}"
        taken.add(identity)
        claim.row_id = identity


# --------------------------------------------------------------------------
# Document
# --------------------------------------------------------------------------


class MappingState(str, Enum):
    """How a source column came to carry the canonical field it carries.

    A state rather than a number: the vocabulary is either sure or it is not,
    and inventing a 0.87 to sit between those would be a confidence this
    system has never calibrated.
    """

    #: Resolved by the reviewed label vocabulary.
    DETERMINISTIC = "deterministic"
    #: Taken from a saved, human-confirmed carrier profile.
    PROFILE_MATCH = "profile_match"
    #: Proposed by the LLM for a label the vocabulary could not place.
    MODEL_MAPPED = "model_mapped"
    #: Two or more columns claimed one field. The value here is not trusted.
    AMBIGUOUS = "ambiguous"
    #: No canonical field; the column is carried but not interpreted.
    UNMAPPED = "unmapped"


class ColumnMappingRecord(BaseModel):
    """One source column, and what was decided about it.

    Kept so a reviewer can ask "why is this figure in this field?" and get an
    answer that names the printed header rather than a column index.
    """

    model_config = ConfigDict(extra="forbid")

    source_index: int
    source_header_raw: str = ""
    source_header_normalized: str = ""
    canonical_field: str | None = None
    state: MappingState = MappingState.UNMAPPED
    #: The field this column wanted when another column won it.
    contested_field: str | None = None
    #: Structural mapping problem not attributable to one current source cell.
    mapping_issue: str | None = None


class PrintedSection(BaseModel):
    """A subtotal the carrier printed under one policy period.

    Loss runs covering several terms print a total per term as well as one for
    the report. Each of these is a number the carrier committed to, so each is
    something the extracted claims for that term can be checked against.
    """

    model_config = ConfigDict(extra="forbid")

    label: str
    period_start: date | None = None
    period_end: date | None = None
    printed_totals: dict[str, Money | None] = Field(default_factory=dict)
    printed_claim_count: int | None = None
    page: int = 1

    #: The printed line this subtotal was read from. A page can carry the same
    #: subtotal twice -- Liberty prints its report total on page 41 in two
    #: identical rows -- and without the line they are one object with one
    #: identity, so a finding about the second silently answers for the first.
    line_index: int = 0

    #: The physical rows this subtotal totals, where the document establishes
    #: that. A carrier printing "Claim Count = 4" at the foot of a page holding
    #: four extracted claims has said what the figures cover; a count that does
    #: not match the page has not, and this stays empty rather than being
    #: filled with whatever claims are nearby. Empty therefore means one of two
    #: things -- nothing to cover, or nothing established -- which
    #: `scope_known` separates.
    covers_rows: list[str] = Field(default_factory=list)

    #: Whether the scope above was established at all. False is the honest
    #: answer for a subtotal nothing can be checked against, and it must reach
    #: a reviewer rather than leaving a captured figure looking verified.
    scope_known: bool = False

    #: Money columns whose printed cell could not be read, and what the page
    #: shows in them. A refusal is right where the cell is ambiguous, but it
    #: silently stops the column being checked, so the printed text survives
    #: the refusal and can be reported with it.
    unreadable_totals: dict[str, str] = Field(default_factory=dict)


class RunConfidence(str, Enum):
    """How a logical run's extent is known.

    A physical PDF can bind several loss runs. Where one ends and the next
    begins is only ever as good as what the pages themselves printed about it.
    """

    #: The report numbered its own pages, in the page's header or footer band,
    #: as read from the text layer.
    PRINTED = "printed"
    #: The vision model reported the page numbering printed in the page's
    #: header or footer. A reading, not a measurement: lower confidence.
    MODEL = "model"
    #: Nothing numbers these pages; the neighbouring report declared its own
    #: last page ("Page 3 of 3"), so these cannot be part of it.
    INFERRED = "inferred"
    #: Nothing on the pages says where a report begins or ends.
    NONE = "none"


class RunBoundary(BaseModel):
    """One piece of page-local evidence about where a run begins or ends."""

    model_config = ConfigDict(extra="forbid")

    page: int
    #: opened | continued | unnumbered | restarted | section | break | leading
    kind: str
    #: header | footer | model | none -- where on the page the evidence sits.
    source: str
    #: What the page printed, and what was concluded from it, in words.
    text: str


class LogicalRun(BaseModel):
    """One insurance loss run inside a physical packet.

    A board or RFP packet binds several carriers' loss runs into one PDF, each
    numbering its own pages and claims and printing its own totals. A logical
    run is one of them: a set of pages, the evidence that bounds it, and what
    it printed about itself. Claims belong to the run holding their source
    page, so membership needs no field on the claim and survives any edit.

    Present on a document only when it binds more than one run; a single loss
    run is the whole document and carries none.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    pages: list[int]
    confidence: RunConfidence
    #: The evidence did not settle where this run begins: a page-number
    #: sequence broke, or a report stopped before its declared last page and
    #: the next page prints no number. The run is kept whole and apart -- its
    #: claims are neither dropped nor handed to a neighbour -- and reviewed.
    ambiguous: bool = False
    ambiguity: str | None = None
    #: The report stopped before its own last page ("2 of 3") and another
    #: began, or the PDF ended: pages of it -- and their claims -- are missing.
    incomplete: str | None = None
    evidence: list[RunBoundary] = Field(default_factory=list)
    source_methods: list[SourceMethod] = Field(default_factory=list)
    #: Pages carrying a claims table the run's claims were read from.
    table_pages: list[int] = Field(default_factory=list)

    #: What this run printed about itself on its own pages -- never another
    #: run's letterhead. None where the run does not say.
    carrier: str | None = None
    named_insured: str | None = None
    policy_number: str | None = None
    policy_period_start: date | None = None
    policy_period_end: date | None = None
    line_of_business: LineOfBusiness | None = None
    valuation_date_text: str | None = None
    valuation_date: date | None = None
    #: Conventions this run's values were read under that its own pages do not
    #: establish -- a number format or date order proven only by another
    #: report in the packet, or a saved profile made for another carrier.
    #: Conventions are still inferred once per document; this is where that
    #: assumption could be wrong for this run, and it is reported (R-15).
    borrowed_conventions: list[str] = Field(default_factory=list)

    #: What this run printed about itself, read from its own pages only.
    printed_totals: dict[str, Money | None] = Field(default_factory=dict)
    printed_claim_count: int | None = None
    printed_count_evidence: list[dict[str, int]] = Field(default_factory=list)
    unreadable_totals: dict[str, str] = Field(default_factory=dict)
    unreadable_totals_page: int | None = None
    unreadable_totals_row: int | None = None

    def holds(self, page: int | None) -> bool:
        return page is not None and page in self.pages

    @property
    def page_range(self) -> str:
        pages = sorted(self.pages)
        if not pages:
            return "no pages"
        spans: list[str] = []
        start = previous = pages[0]
        for page in pages[1:] + [None]:
            if page is not None and page == previous + 1:
                previous = page
                continue
            spans.append(str(start) if start == previous else f"{start}-{previous}")
            if page is not None:
                start = previous = page
        return ", ".join(spans)


def split_is_settled(runs: Sequence[LogicalRun]) -> bool:
    """Whether every boundary of a packet is printed and unambiguous.

    Only then is it known which run a printed count or total belongs to. A
    boundary resting on a reading (OCR, the vision model) or on nothing, or
    one the planner could not settle, leaves the carrier's printed figures
    with the whole document they were read from.
    """
    return bool(runs) and all(
        run.confidence is RunConfidence.PRINTED and not run.ambiguous for run in runs
    )


class RefusedClaimRow(BaseModel):
    """A row that reads as a claim but whose claim number the vote refused.

    Its identifier cell is well formed and the row states a loss date or a
    status of its own, yet the run's claim-number vote did not accept its
    shape, so no claim was built from it. It is kept here -- page, line and
    the identifier printed -- rather than folded into the claim above or
    reduced to a warning, so a reviewer can be told where it is.
    """

    model_config = ConfigDict(extra="forbid")

    page: int
    row: int | None = None
    identifier: str
    method: SourceMethod = SourceMethod.DIGITAL
    #: Whether the row's run is bounded by what its pages print. Unbounded,
    #: the vote that refused it may have pooled two reports' claim numbers.
    bounded: bool = True
    #: Whether a reviewer must be told (R-29): in a packet, where another
    #: report's numbering is the likeliest reason for a refusal, or on a scan
    #: nothing bounds. Rows in an unsettled run are named by R-28 instead.
    report: bool = False


class LossRunDocument(BaseModel):
    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    document_id: str = Field(default_factory=lambda: str(uuid4()))
    source_filename: str
    file_sha256: str

    carrier: str | None = None
    named_insured: str | None = None
    policy_number: str | None = None
    policy_period_start: date | None = None
    policy_period_end: date | None = None
    line_of_business: LineOfBusiness | None = None
    valuation_date: date | None = None

    currency: str = "USD"
    locale_hint: str = "us"
    page_count: int = 0
    extraction_method: ExtractionMethod = ExtractionMethod.DIGITAL

    printed_totals: dict[str, Money | None] = Field(default_factory=dict)
    printed_claim_count: int | None = None

    #: Money columns of the *document's own* total row whose printed cell could
    #: not be read, and what the page shows in them. The same fact
    #: `PrintedSection.unreadable_totals` records, for the row R-04 checks
    #: against. Supplying two readable totals does not earn the row an
    #: exemption for a third: the column simply stops being checked, and a
    #: reviewer has to be told which one and why.
    unreadable_totals: dict[str, str] = Field(default_factory=dict)
    #: Which printed line that row was, when the reading has one. A vision
    #: result can report a figure without a line to point at; that stays None
    #: and is reported as unknown rather than filled with a number nobody
    #: measured. None is the absence of a measurement, not the value zero.
    unreadable_totals_row: int | None = None
    #: The page that row was printed on, so the finding can point at it.
    unreadable_totals_page: int | None = None

    #: Every claim count the document states, with the page it was read from.
    #: Kept whether or not one of them became `printed_claim_count`: where
    #: several are printed and none is the report's, R-05 cannot run, and the
    #: counts are the only evidence of why. Discarding them leaves a document
    #: that looks as though it never mentioned how many claims it holds.
    printed_count_evidence: list[dict[str, int]] = Field(default_factory=list)
    #: Every policy term the document declares, in the order printed. A loss run
    #: covering several renewals declares one per section.
    policy_periods: list[tuple[date, date]] = Field(default_factory=list)
    #: Per-term subtotals the carrier printed, for checking each term's claims.
    printed_sections: list[PrintedSection] = Field(default_factory=list)
    #: The loss runs this packet binds, when it binds more than one. Empty for
    #: a single loss run, which is the whole document. Each run is reconciled
    #: on its own against what it printed; see :class:`LogicalRun`.
    runs: list[LogicalRun] = Field(default_factory=list)
    #: A lone report whose own numbering stops before its declared last page:
    #: pages it holds are absent from the PDF. None for a packet, whose
    #: incompleteness lives on the run, and for a report that prints no
    #: numbering or printed its own last page. R-28 reads it.
    incomplete_report: str | None = None
    #: Rows that read as claims but whose claim number was refused. Recorded,
    #: never silently absorbed; see :class:`RefusedClaimRow`.
    refused_claim_rows: list[RefusedClaimRow] = Field(default_factory=list)
    #: Claim rows found on each page before stitching, so R-19 can tell whether
    #: dropping repeated headers and subtotals also dropped a claim.
    rows_seen_per_page: dict[int, int] = Field(default_factory=dict)
    #: What was decided about every source column, including the ones whose
    #: meaning could not be settled. R-21 reads this.
    column_mapping: list[ColumnMappingRecord] = Field(default_factory=list)

    #: What reviewers decided, kept on the document so it survives an edit and
    #: reaches the export. Never consulted by the rule engine: a decision about
    #: a finding is not evidence about a claim.
    review_log: ReviewLog = Field(default_factory=lambda: ReviewLog())

    claims: list[Claim] = Field(default_factory=list)

    # Parsing context, carried so the UI and the rules can explain themselves.
    locale_confident: bool = True
    date_order: str | None = None
    date_order_confident: bool = True
    currencies_seen: list[str] = Field(default_factory=list)
    document_issues: dict[str, NullReason] = Field(default_factory=dict)
    profile_fingerprint: str | None = None
    profile_name: str | None = None
    #: "credit" when this carrier prints recoveries as negative amounts, which
    #: are normalised to positive so R-01's subtraction means what it says.
    recovery_convention: str | None = None
    scanned_pages: list[int] = Field(default_factory=list)
    #: Source pages successfully inspected, including pages with no claim table.
    processed_pages: list[int] = Field(default_factory=list)
    #: Source pages whose attempted extraction did not complete successfully.
    failed_pages: list[int] = Field(default_factory=list)
    #: Rows carrying printed amounts that could not be attached to any claim.
    #: Kept on the document rather than in the warning list because money the
    #: app read and then could not place has to reach reconciliation.
    unplaced_rows: list["UnplacedRow"] = Field(default_factory=list)
    #: Source pages a vision reader answered for without returning anything.
    #: Kept apart from the three outcomes either side of it because it is a
    #: different fact from each: the request did not fail, the page was not
    #: skipped, and nothing was read. A model handed a poor scan usually
    #: returns well-formed JSON with no rows rather than raising, which looks
    #: exactly like a page that genuinely holds no claim table. Only a
    #: deterministic reader can establish that; a model declining to find
    #: anything is not evidence that there was nothing to find.
    unresolved_pages: list[int] = Field(default_factory=list)
    #: Why each unresolved page is unresolved, where the reader that gave up
    #: was not the vision one. A page reaches this set from more than one
    #: direction now, and a finding that names the wrong reader sends a
    #: reviewer to turn on something that would not have helped. Absent means
    #: the original case: a vision reader answered and returned nothing.
    unresolved_reasons: dict[int, str] = Field(default_factory=dict)
    #: Source pages deliberately not attempted, such as scans with vision off.
    skipped_pages: list[int] = Field(default_factory=list)
    #: Adjacent page pairs whose own printed geometry -- consecutive
    #: spreadsheet column letters, a repeated row-index column at matching
    #: heights on both -- says they are one table sliced by column for
    #: printing. Structural fact only: it says two pages are one table, not
    #: what any cell on either half means, so nothing here is used to fill
    #: in a claim's fields.
    column_split_pages: list[tuple[int, int]] = Field(default_factory=list)
    extracted_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @model_validator(mode="after")
    def _identify_rows(self) -> "LossRunDocument":
        """Every claim on the document can name the row it is.

        Here rather than in each extractor because there is more than one way
        into a document — two extraction paths, the review screen, a test — and
        a claim that reached one of them without an identity would be a claim
        no decision could safely attach to. ``validate_assignment`` means this
        also runs when the review screen replaces the claim list, so a row
        added by hand is identified the moment it exists.
        """
        assign_row_ids(self.claims)
        identities = [claim.row_id for claim in self.claims]
        if len(identities) != len(set(identities)):
            duplicates = sorted(
                {identity for identity in identities if identities.count(identity) > 1}
            )
            raise ValueError(
                "claim row_id values must be unique: " + ", ".join(duplicates)
            )
        return self

    @field_validator("currency")
    @classmethod
    def _iso_4217(cls, value: str) -> str:
        code = value.strip().upper()
        if len(code) != 3 or not code.isalpha():
            raise ValueError(f"currency must be a 3-letter ISO 4217 code, got {value!r}")
        return code

    @field_validator("locale_hint")
    @classmethod
    def _known_locale(cls, value: str) -> str:
        locale = value.strip().lower()
        if locale not in {"us", "eu"}:
            raise ValueError(f"locale_hint must be 'us' or 'eu', got {value!r}")
        return locale

    @field_validator("page_count")
    @classmethod
    def _non_negative_pages(cls, value: int) -> int:
        if value < 0:
            raise ValueError("page_count may not be negative")
        return value

    @property
    def recovery_convention_label(self) -> str:
        if self.recovery_convention == "credit":
            return "credits, normalised to positive amounts"
        return "positive amounts"

    @property
    def is_packet(self) -> bool:
        """Whether this PDF binds more than one loss run."""
        return len(self.runs) > 1

    def run_of(self, page: int | None) -> LogicalRun | None:
        """The logical run holding a page, or None for a single loss run."""
        return next((run for run in self.runs if run.holds(page)), None)

    def run_claims(self, run: LogicalRun) -> list[Claim]:
        """The claims read from this run's pages, in document order."""
        return [claim for claim in self.claims if run.holds(claim.source_page)]

    def column_total(self, field: str) -> Decimal:
        """Sum of a money column across claims, ignoring nulls.

        Nulls are skipped rather than treated as zero; R-04 reports the sum
        alongside the printed total so a skipped null shows up as a mismatch
        instead of quietly changing the answer.
        """
        total = Decimal("0")
        for claim in self.claims:
            value = getattr(claim, field, None)
            if value is not None:
                total += value
        return total

    def claims_by_number(self) -> dict[str, list[Claim]]:
        grouped: dict[str, list[Claim]] = {}
        for claim in self.claims:
            grouped.setdefault(claim.claim_number, []).append(claim)
        return grouped


# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------


class Finding(BaseModel):
    """One reconciliation result (spec section 6)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str
    severity: Severity
    message: str
    scope: FindingScope
    category: FindingCategory
    claim_number: str | None = None
    #: What on the document this finding is about, in terms that survive being
    #: edited: a claim's :attr:`~Claim.row_id`, or the printed column a mapping
    #: rule contests. The claim number cannot do this job — two rows can share
    #: one, and correcting a duplicate changes it — and neither can the
    #: finding's position, since the engine rebuilds the list on every edit.
    subject: str
    #: Distinguishes independent checks by one rule on the same subject/field.
    #: For example, R-10 checks both report-before-loss and after-valuation.
    condition: str = "primary"
    related_rows: tuple[str, ...] = ()
    field: str | None = None
    expected: Decimal | int | str | None = None
    actual: Decimal | int | str | None = None
    delta: Decimal | None = None
    page: int | None = None
    #: The logical run this finding was raised in, for a packet binding
    #: several. None on a single loss run, where the document is the run.
    run_id: str | None = None

    @model_validator(mode="after")
    def _identity_is_complete(self) -> "Finding":
        """Refuse findings whose identity cannot safely survive a re-run."""
        if not self.subject.strip():
            raise ValueError("finding subject must be non-empty and explainable")
        if not self.condition.strip():
            raise ValueError("finding condition must be non-empty")
        if self.category is FindingCategory.FINANCIAL and self.severity is not Severity.ERROR:
            raise ValueError("financial reconciliation failures must have ERROR severity")
        if self.category is FindingCategory.UNDERWRITING and self.severity is Severity.ERROR:
            raise ValueError("underwriting observations cannot have ERROR severity")
        claim_scopes = {FindingScope.CLAIM, FindingScope.CLAIM_GROUP}
        if self.scope in claim_scopes and not self.claim_number:
            raise ValueError(f"{self.scope.value} findings require a claim number")
        if self.scope not in claim_scopes and self.claim_number is not None:
            raise ValueError(
                f"{self.scope.value} findings cannot carry a claim number"
            )
        if self.scope is not FindingScope.CLAIM_GROUP and self.related_rows:
            raise ValueError("only claim-group findings may identify related rows")
        if self.scope is FindingScope.DOCUMENT and self.subject != "document":
            raise ValueError("document findings must use subject='document'")
        if self.scope is FindingScope.CLAIM_GROUP:
            if (len(self.related_rows) < 2 or any(not row for row in self.related_rows)
                    or len(set(self.related_rows)) != len(self.related_rows)):
                raise ValueError("claim-group findings require distinct physical row identities")
            expected = f"claim-number:{self.claim_number}"
            if self.subject != expected:
                raise ValueError(
                    f"claim-group subject must be {expected!r}, got {self.subject!r}"
                )
        if self.scope is FindingScope.COLUMN:
            if not re.fullmatch(r"column [1-9][0-9]*", self.subject) or not self.field:
                raise ValueError(
                    "column findings require subject='column N' and a canonical field"
                )
        return self

    def __str__(self) -> str:  # pragma: no cover - convenience only
        where = f" [{self.claim_number}]" if self.claim_number else ""
        return f"{self.rule_id} {self.severity.value}{where}: {self.message}"


# --------------------------------------------------------------------------
# Review — what a person decided, kept apart from what the document said
# --------------------------------------------------------------------------

#: Used where no reviewer identity is available. There are no accounts yet, and
#: inventing a name would put someone's word behind a decision they never made.
LOCAL_REVIEWER = "local reviewer"


class ReviewAction(str, Enum):
    """What a reviewer did about a finding.

    ``OPEN`` is the absence of the others rather than a stored value: a finding
    nobody has touched has no resolution at all.
    """

    OPEN = "open"
    #: Looked at, and the document is right — a genuine large loss, a real
    #: reopened claim. Nothing about the data changes.
    CONFIRMED = "confirmed"
    #: Looked at, and the extraction was wrong. A value was corrected and every
    #: rule was run again over the corrected value.
    CORRECTED = "corrected"
    #: Looked at, and the finding does not apply. The claim stays, the data
    #: stays, and the finding stays on the record as raised.
    DISMISSED = "dismissed"
    #: A cell changed in the claims table rather than against a finding. Not a
    #: decision about anything the engine raised, but it is still somebody
    #: replacing a carrier's figure, so it belongs in the same record.
    EDITED = "edited"
    #: A whole row removed on the review screen. Recorded with what it held,
    #: because a claim that leaves without a trace is the one kind of change
    #: nothing downstream can notice.
    DELETED = "deleted"


def _asserted(finding: "Finding") -> tuple[str | None, str | None, str | None]:
    """What a finding materially claims, as text an audit can read back."""
    return (
        None if finding.expected is None else str(finding.expected),
        None if finding.actual is None else str(finding.actual),
        None if finding.delta is None else str(finding.delta),
    )


def _same_amount(one: str | None, other: str | None) -> bool:
    """Whether two recorded figures say the same thing.

    Numerically where both are numbers, because a document that round-trips
    through the review table comes back with the same money at a different
    scale — ``8400.00`` and ``8400.0`` are one figure, and a decision about it
    has not been overtaken by a re-run that wrote it differently.
    """
    if one == other:
        return True
    if one is None or other is None:
        return False
    try:
        return Decimal(one) == Decimal(other)
    except (InvalidOperation, ValueError):
        return one.strip() == other.strip()


def finding_key(finding: "Finding") -> str:
    """What identifies a finding across a re-run.

    Not its position: the engine rebuilds the list whenever a cell changes, and
    a resolution attached to index 4 would slide onto a different finding. What
    survives re-running is the rule, the thing on the document it is about, and
    the field — so those are the key.

    ``subject`` is what makes the middle term trustworthy. Keyed on the claim
    number instead, a rule that raises one finding per contested column keys
    them all alike: Liberty prints "Incurred Medical", "Incurred Expense" and
    "Total Incurred", R-21 says so three times, and one dismissal would answer
    for all three including the real incurred column.
    """
    # A JSON tuple is readable and cannot collide when a label contains a
    # delimiter. Concatenating with '|' made boundary placement ambiguous.
    identity = [finding.rule_id, finding.category.value, finding.scope.value,
                finding.subject, finding.field or "", finding.condition,
                sorted(finding.related_rows)]
    # Two runs in one packet each print a total, and the same rule checking
    # each is two findings. The run joins the key only where there is one, so
    # every decision already recorded against a single loss run still matches.
    if finding.run_id is not None:
        identity.append(finding.run_id)
    return json.dumps(identity, ensure_ascii=False, separators=(",", ":"))


class Resolution(BaseModel):
    """One reviewer decision, and everything needed to reconstruct it."""

    model_config = ConfigDict(extra="forbid")

    key: str
    action: ReviewAction
    reviewer: str = LOCAL_REVIEWER
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    note: str = ""

    #: The finding as it was raised, kept verbatim. The engine will raise it
    #: again on the next run if it still holds and will not if it does not;
    #: either way this is what the reviewer was looking at when they decided.
    rule_id: str = ""
    severity: str = ""
    message: str = ""
    claim_number: str | None = None
    field: str | None = None

    #: Which physical row this was about, and the same thing in words. The
    #: claim number alone cannot answer it: two rows can carry one number, and
    #: correcting a duplicate changes the number itself, so an audit reading
    #: back "claim FM-0003" would not know which of them moved.
    row_id: str = ""
    where: str = ""

    #: What the finding asserted when the decision was taken. A reviewer
    #: confirming a $10,000 discrepancy has not confirmed a $2,223 one, so
    #: these are compared before the decision is allowed to still apply.
    expected: str | None = None
    actual: str | None = None
    delta: str | None = None

    #: Set only for a correction.
    before: str | None = None
    after: str | None = None

    #: The reconciliation status either side of the decision, so a reader can
    #: see whether the document's position actually moved.
    status_before: str = ""
    status_after: str = ""

    @property
    def changed_a_value(self) -> bool:
        return (
            self.action
            in (ReviewAction.CORRECTED, ReviewAction.EDITED, ReviewAction.DELETED)
            and self.before != self.after
        )

    def still_applies_to(self, finding: "Finding") -> bool:
        """Whether this decision was taken about what the finding now says.

        A resolution is attached to a finding by identity, which survives an
        edit on purpose — that is how "somebody looked at R-01 on this row"
        outlives a change elsewhere on the page. But identity is not the whole
        of what a reviewer agreed to. Correct one figure and R-01 can go on
        failing on the same row for a different amount, and the decision taken
        about the old amount would silently cover the new one.

        So the material claim is compared too: what the rule expected, what it
        found, and by how much. Where they differ the finding is open again and
        the old decision stays in the log as what it was, a decision about a
        different number.
        """
        recorded = (self.expected, self.actual, self.delta)
        if self.severity != finding.severity.value:
            return False
        if not any(value is not None for value in recorded):
            # Recorded before the finding carried a material claim, or a rule
            # that makes none. Identity is all there is to go on.
            return self.message == finding.message
        return all(
            _same_amount(was, now) for was, now in zip(recorded, _asserted(finding))
        )


class ReviewLog(BaseModel):
    """Every decision taken on one document, oldest first.

    Append-only. A reviewer changing their mind adds an entry; nothing is
    rewritten, because the question an audit answers is what was decided and
    when, not what the last word was.
    """

    model_config = ConfigDict(extra="forbid")

    entries: list[Resolution] = Field(default_factory=list)

    def record(self, resolution: Resolution) -> None:
        self.entries.append(resolution)

    def latest_for(self, key: str) -> Resolution | None:
        for entry in reversed(self.entries):
            if entry.key == key:
                return entry
        return None

    def action_for(self, finding: "Finding") -> ReviewAction:
        latest = self.latest_for(finding_key(finding))
        if latest is None or not latest.still_applies_to(finding):
            return ReviewAction.OPEN
        return latest.action

    def is_resolved(self, finding: "Finding") -> bool:
        return self.action_for(finding) is not ReviewAction.OPEN

    def unresolved(self, findings: "Iterable[Finding]") -> list["Finding"]:
        return [finding for finding in findings if not self.is_resolved(finding)]

    @property
    def corrections(self) -> list[Resolution]:
        return [entry for entry in self.entries if entry.changed_a_value]


class ReconciliationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: DocumentStatus
    findings: list[Finding] = Field(default_factory=list)
    #: Each logical run's own status, for a packet binding several. The
    #: document is NEEDS_REVIEW whenever any run is, or any boundary is not
    #: settled; empty for a single loss run.
    run_status: dict[str, DocumentStatus] = Field(default_factory=dict)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.WARN]

    @property
    def infos(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.INFO]

    def by_claim(self, claim_number: str) -> list[Finding]:
        return [f for f in self.findings if f.claim_number == claim_number]

    def rule_ids(self) -> list[str]:
        return [f.rule_id for f in self.findings]


# --------------------------------------------------------------------------
# Field groups — used by mapping, reconciliation, and export
# --------------------------------------------------------------------------

PAID_COMPONENT_FIELDS: tuple[str, ...] = (
    "paid_indemnity",
    "paid_medical",
    "paid_expense",
)

RESERVE_COMPONENT_FIELDS: tuple[str, ...] = (
    "reserve_indemnity",
    "reserve_medical",
    "reserve_expense",
)

MONEY_FIELDS: tuple[str, ...] = (
    *PAID_COMPONENT_FIELDS,
    "paid_total",
    *RESERVE_COMPONENT_FIELDS,
    "reserve_total",
    "recovery_total",
    "incurred_total",
)

DATE_FIELDS: tuple[str, ...] = ("date_of_loss", "date_reported", "close_date")

TEXT_FIELDS: tuple[str, ...] = (
    "claim_number",
    "claimant_name",
    "claimant_ref",
    "loss_state",
    "body_part",
    "nature_of_injury",
    "ncci_class_code",
    "loss_description",
    "cause_of_loss",
)

#: Everything a source column may be mapped onto.
CANONICAL_FIELDS: tuple[str, ...] = (
    "claim_number",
    "date_of_loss",
    "date_reported",
    "claim_status",
    "claimant_name",
    "loss_description",
    "cause_of_loss",
    *MONEY_FIELDS,
    "litigation_flag",
)

#: Personal data dropped by the export redaction toggle (spec section 9).
REDACTED_FIELDS: tuple[str, ...] = ("claimant_name", "loss_description")


def sum_present(values: Iterable[Decimal | None]) -> Decimal | None:
    """Sum only the values that exist; ``None`` if none of them do.

    Returning ``None`` rather than ``0`` keeps "no components" distinct from
    "components that add to zero".
    """
    present = [v for v in values if v is not None]
    if not present:
        return None
    total = Decimal("0")
    for value in present:
        total += value
    return total


# --------------------------------------------------------------------------
# Extraction artefacts — what stage 2 hands to stage 3
# --------------------------------------------------------------------------


class RawRow(BaseModel):
    """One physical line of a printed table, split into columns.

    Cells are still *text*: normalisation happens in stage 4, after the column
    mapping is known, so nothing is parsed twice or parsed the wrong way.
    """

    model_config = ConfigDict(extra="forbid")

    cells: list[str] = Field(default_factory=list)
    page: int = 1
    line_index: int = 0
    bbox: tuple[float, float, float, float] | None = None
    #: What the extractor made of this printed line. ``meta`` is a line it
    #: recognised as belonging to the document rather than to any claim -- a
    #: contract or policy block, a section label. It is kept as a row so the
    #: page's contents are not silently dropped, but it is not claim data and
    #: must never be folded into a claim's text: the line above a continuation
    #: page's first claim is the *previous page's* last claim, and a contract
    #: number appended there becomes part of an accident description.
    kind: str = "data"  # data | total | header | meta

    #: Every printed line this row was read from, when a carrier spreads one
    #: claim over several. Empty means the row is one line, at ``line_index``.
    #: Reconstruction has to leave a trail: a reviewer looking at a merged
    #: record needs to know which lines on the page it came from, and a
    #: reader of this row needs to be able to tell that it was merged at all.
    source_lines: list[int] = Field(default_factory=list)

    def cell(self, index: int) -> str:
        return self.cells[index] if 0 <= index < len(self.cells) else ""

    def text(self) -> str:
        return " ".join(cell for cell in self.cells if cell.strip())

    def is_blank(self) -> bool:
        return not any(cell.strip() for cell in self.cells)


class UnplacedRow(BaseModel):
    """A printed row carrying amounts that no claim could take.

    The extractor already tells a wrapped description from a line of figures:
    prose is folded into the claim above it, and a row whose cells parse under
    mapped money columns is data whose claim number could not be identified.
    Having drawn that distinction it used to drop the second kind to a warning,
    and a warning is not a finding -- it never reached the badge, the exceptions
    or the workbook. An amount the app had itself parsed could therefore sit
    nowhere at all while the document read as reconciled.

    Only the location and the printed amounts are kept. Whatever else was on
    the line is left on the page: a reviewer needs to know that money went
    unplaced and where to look, not to have the row's prose copied into the
    record.
    """

    model_config = ConfigDict(extra="forbid")

    page: int
    #: The printed line, in the same coordinates a claim's ``source_row`` uses,
    #: so "page 3, line 18" means the same thing for both.
    row: int | None = None
    #: Canonical money field -> the text printed under it, verbatim.
    amounts: dict[str, str] = Field(default_factory=dict)

    #: Numeric text read under a mapped money column where the row and its
    #: table establish neither that it is money nor that it is not. Kept apart
    #: from `amounts` because the two are different facts and only one of them
    #: may be called an amount: a bare "9400" on an otherwise empty row may be
    #: a whole-unit payment or a page number a column boundary caught, and the
    #: reading does not know. Reported in those words rather than resolved.
    ambiguous_values: dict[str, str] = Field(default_factory=dict)
    #: The same amounts, already parsed. Kept beside the text rather than
    #: re-derived later: reconciliation compares this row's figures against a
    #: claim's own Decimal fields, and re-parsing at that layer would mean
    #: carrying locale into a module that should never need it.
    parsed_amounts: dict[str, Money] = Field(default_factory=dict)

    def where(self) -> str:
        if self.row is None:
            return f"page {self.page}"
        return f"page {self.page}, line {self.row + 1}"


class RawTable(BaseModel):
    """The table found on one page, before any value is interpreted."""

    model_config = ConfigDict(extra="forbid")

    page: int
    headers: list[str] = Field(default_factory=list)
    rows: list[RawRow] = Field(default_factory=list)
    total_rows: list[RawRow] = Field(default_factory=list)
    strategy: str = "words"  # words | ruled | records | vision
    header_line_index: int | None = None
    column_bounds: list[tuple[float, float]] = Field(default_factory=list)

    #: Facts printed on the page itself. A fully scanned document has no text
    #: layer to read these from, so the vision pass reports them here.
    printed_claim_count: int | None = None
    valuation_date_text: str | None = None
    #: The page numbering and the heading the vision model read in the page's
    #: furniture -- what a scanned page says about which report it belongs
    #: to. Readings, not measurements; the digital path measures its own.
    page_label: str | None = None
    page_label_index: int | None = None
    page_label_count: int | None = None
    #: header | footer. A number the model found anywhere else is not kept.
    page_label_position: str | None = None
    heading: str | None = None

    @property
    def column_count(self) -> int:
        return len(self.headers)
