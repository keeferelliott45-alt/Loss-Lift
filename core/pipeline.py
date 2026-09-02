"""Stage orchestration — ingest through reconcile.

``app.py`` calls this and nothing else, so every stage stays runnable and
testable without Streamlit.  Nothing here talks to the UI, and nothing in the
UI does any of this work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

from core import extract_digital
from core.classify import DocumentClassification, classify_pdf
from core.extract_digital import _COUNT_PATTERNS, _first_match, DocumentMetadata
from core.ingest import IngestedFile, ingest_path
from core.normalize import (
    DateOrderInference,
    LocaleInference,
    RecoverySignInference,
    clean_text,
    infer_date_order,
    infer_locale,
    infer_recovery_sign,
    parse_bool,
    parse_date,
    parse_int,
    parse_money,
    parse_status,
    normalize_label,
    parse_text,
)
from core.profiles import (
    guess_document_field,
    CarrierProfile,
    fingerprint,
    load_profile,
    page_top_text,
    resolve_columns,
)
from core.records import (
    consensus_shapes,
    is_identifier_candidate,
    leading_identifier,
)
from core.reconcile import ReconcileConfig, reconcile
from core.review import (
    LOCAL_REVIEWER,
    ReviewAction,
    resolution_for,
    summarise_review,
)
from core.schema import (
    DATE_FIELDS,
    Finding,
    FindingScope,
    MONEY_FIELDS,
    Claim,
    ClaimStatus,
    LineOfBusiness,
    LossRunDocument,
    ColumnMappingRecord,
    MappingState,
    NullReason,
    PrintedSection,
    RawRow,
    RawTable,
    ReconciliationResult,
    UnplacedRow,
    Resolution,
    SourceMethod,
)


#: Labels that mark the one total covering every claim, not a section subtotal.
GRAND_TOTAL_PATTERN = re.compile(
    r"\b(?:grand|report|overall|final)\s+totals?\b", re.IGNORECASE
)


@dataclass
class ColumnMapping:
    """Which canonical field each printed column carries."""

    headers: list[str]
    fields: dict[int, str | None]
    source: str = "heuristic"  # profile | heuristic | llm | manual
    fingerprint: str = ""
    #: Per column, what was decided and why. Carries the columns that lost a
    #: contest, which `fields` cannot represent: it only has room for a None.
    decisions: list[ColumnMappingRecord] = field(default_factory=list)

    @property
    def mapped_fields(self) -> set[str]:
        return {name for name in self.fields.values() if name}

    @property
    def unmapped_columns(self) -> list[int]:
        return [index for index, name in self.fields.items() if not name]

    def index_of(self, field_name: str) -> int | None:
        for index, name in self.fields.items():
            if name == field_name:
                return index
        return None

    def is_usable(self) -> bool:
        """A mapping is usable once it can identify a claim and an amount."""
        return "claim_number" in self.mapped_fields and bool(
            self.mapped_fields & set(MONEY_FIELDS)
        )


@dataclass
class ExtractionResult:
    """Everything the review screen needs about one document."""

    document: LossRunDocument
    reconciliation: ReconciliationResult
    mapping: ColumnMapping
    classification: DocumentClassification
    locale: LocaleInference
    date_order: DateOrderInference
    recovery_sign: RecoverySignInference = field(
        default_factory=RecoverySignInference
    )
    tables: list[RawTable] = field(default_factory=list)
    profile: CarrierProfile | None = None
    warnings: list[str] = field(default_factory=list)
    #: The staged upload, while it is still on disk. Evidence is shown from the
    #: document itself, so the review screen needs a way back to it. Session
    #: state, not part of the canonical model: the file is deleted after export
    #: (spec section 9) and this becomes None, without taking the page numbers,
    #: regions and cell text with it.
    source_path: Path | None = None

    @property
    def needs_mapping(self) -> bool:
        return not self.mapping.is_usable()


# --------------------------------------------------------------------------
# Column mapping
# --------------------------------------------------------------------------


def build_mapping(
    headers: Sequence[str],
    profile: CarrierProfile | None = None,
    fingerprint_value: str = "",
    *,
    samples: Sequence[Sequence[str]] = (),
    use_llm: bool = False,
    llm_client: Any | None = None,
) -> ColumnMapping:
    """Resolve headers to fields: saved profile, then vocabulary, then the LLM.

    A matching profile means zero LLM calls — the compounding moat in spec
    section 2. The LLM is consulted only for headers the vocabulary could not
    place, and only when the caller asks for it.
    """
    guesses, source = resolve_columns(
        headers,
        samples,
        profile=profile,
        fingerprint_value=fingerprint_value,
        use_llm=use_llm,
        client=llm_client,
    )
    states = {
        "profile": MappingState.PROFILE_MATCH,
        "llm": MappingState.MODEL_MAPPED,
        "contested": MappingState.AMBIGUOUS,
        "unmapped": MappingState.UNMAPPED,
    }
    decisions = [
        ColumnMappingRecord(
            source_index=index,
            source_header_raw=headers[index] if index < len(headers) else "",
            source_header_normalized=normalize_label(
                headers[index] if index < len(headers) else ""
            ),
            canonical_field=guess.field,
            state=states.get(guess.source, MappingState.DETERMINISTIC),
            contested_field=guess.contested_field,
        )
        for index, guess in sorted(guesses.items())
    ]
    if source == "profile_mismatch":
        decisions.append(
            ColumnMappingRecord(
                source_index=-1,
                source_header_raw="Saved profile",
                source_header_normalized="saved profile",
                state=MappingState.AMBIGUOUS,
                mapping_issue=(
                    "The saved profile's source-column structure does not "
                    "uniquely match this document. The profile was not applied; "
                    "normal mapping was used and the result requires review."
                ),
            )
        )
    return ColumnMapping(
        headers=list(headers),
        fields={index: guess.field for index, guess in guesses.items()},
        source=source,
        fingerprint=fingerprint_value,
        decisions=decisions,
    )


# --------------------------------------------------------------------------
# Stage 4 — normalise rows into claims
# --------------------------------------------------------------------------


def _row_values(row: RawRow, mapping: ColumnMapping) -> dict[str, str]:
    """The row's cells keyed by canonical field."""
    values: dict[str, str] = {}
    for index, field_name in mapping.fields.items():
        if field_name:
            values[field_name] = row.cell(index).strip()
    return values


def build_claim(
    row: RawRow,
    mapping: ColumnMapping,
    locale: str | None,
    date_order: str | None,
    *,
    dash_means_zero: bool = False,
    source_method: SourceMethod = SourceMethod.DIGITAL,
    confidence_cap: float = 1.0,
) -> Claim | None:
    """Turn one raw row into a claim, recording why any field is null."""
    values = _row_values(row, mapping)
    claim_number = clean_text(values.get("claim_number", ""))
    if not claim_number:
        return None

    issues: dict[str, NullReason] = {}
    confidence: dict[str, float] = {}
    raw_cells: dict[str, str] = {}
    fields: dict[str, Any] = {}
    currencies: set[str] = set()

    for field_name, raw in values.items():
        if field_name == "claim_number":
            continue
        raw_cells[field_name] = raw

        if field_name in MONEY_FIELDS:
            parsed = parse_money(raw, locale, dash_means_zero=dash_means_zero)
            fields[field_name] = parsed.value
            if parsed.value is None and parsed.reason:
                issues[field_name] = parsed.reason
            if parsed.currency:
                currencies.add(parsed.currency)
            confidence[field_name] = min(
                confidence_cap, 1.0 if parsed.value is not None else 0.0
            )
        elif field_name in DATE_FIELDS:
            parsed_date = parse_date(raw, date_order)
            fields[field_name] = parsed_date.value
            if parsed_date.value is None and parsed_date.reason:
                issues[field_name] = parsed_date.reason
            confidence[field_name] = min(
                confidence_cap, 1.0 if parsed_date.value is not None else 0.0
            )
        elif field_name == "claim_status":
            status = parse_status(raw)
            fields["claim_status"] = status
            confidence["claim_status"] = min(
                confidence_cap, 1.0 if status is not ClaimStatus.UNKNOWN else 0.0
            )
        elif field_name in ("litigation_flag", "medical_only_flag"):
            fields[field_name] = parse_bool(raw)
            confidence[field_name] = confidence_cap
        else:
            fields[field_name] = parse_text(raw)
            confidence[field_name] = confidence_cap

    raw_cells["claim_number"] = values.get("claim_number", "")
    confidence["claim_number"] = confidence_cap

    return Claim(
        claim_number=claim_number,
        source_page=row.page,
        source_row=row.line_index,
        source_bbox=row.bbox,
        source_lines=list(row.source_lines),
        source_method=source_method,
        field_issues=issues,
        field_confidence=confidence,
        raw_cells=raw_cells,
        currency=sorted(currencies)[0] if len(currencies) == 1 else None,
        **fields,
    )


#: "Page 3 of 8" and friends. A footer often shares its line with a strapline,
#: and the page number is the one part that changes, so it has to come out
#: before two pages' footers can be compared.
_PAGE_MARKER = re.compile(r"\bpage\s+\d+\s*(?:of\s*\d+)?\b", re.IGNORECASE)


def _normalised(row: RawRow) -> str:
    text = " ".join(row.text().lower().split())
    return " ".join(_PAGE_MARKER.sub(" ", text).split())


def page_furniture(
    tables: Sequence[RawTable],
    mapping: ColumnMapping | None = None,
    locale: str | None = None,
) -> set[str]:
    """Row text that repeats across pages: a running header, footer or strapline.

    A claim appears once. A line printed on most pages of the document is the
    carrier's letterhead, its tagline or a column caption, and treating those as
    skipped claim data buries the warnings that matter under one entry per page.
    Needs at least two pages: on a single page a repeated line cannot be told
    apart from data.
    """
    pages = {table.page for table in tables}
    if len(pages) < 2:
        return set()

    seen: dict[str, set[int]] = {}
    for table in tables:
        table_mapping = mapping_for(table, mapping) if mapping is not None else None
        for row in list(table.rows) + list(table.total_rows):
            # Repetition alone does not make a row furniture. Spreadsheet
            # continuations can repeat the same amounts on several pages; if
            # those amounts have no claim, reconciliation must see each row.
            if table_mapping is not None and _parsed_financial_cells(
                row, table_mapping, locale
            ):
                continue
            text = _normalised(row)
            if text:
                seen.setdefault(text, set()).add(table.page)
    threshold = max(2, len(pages) // 2)
    return {text for text, on in seen.items() if len(on) >= threshold}


def _continuation_text(row: RawRow, mapping: ColumnMapping) -> str | None:
    """Text from a wrapped row that belongs to the claim above it."""
    values = _row_values(row, mapping)

    # A wrapped description runs the width of the row, so its words land in
    # whichever columns they cross, money and date columns included. What marks
    # a row as data is whether those cells parse: "WHEEL" under a date column
    # is prose, and rejecting the row for it drops the description entirely.
    for name in MONEY_FIELDS:
        if parse_money(values.get(name, ""), None).value is not None:
            return None
    for name in DATE_FIELDS:
        if parse_date(values.get(name, ""), None).value is not None:
            return None

    # Reading only the text-typed columns truncated the description at the
    # first column boundary, so take the whole line.
    return clean_text(" ".join(row.cells)) or None


_NON_MONEY_ROW = re.compile(
    r"\b(?:software\s+)?version\b|"
    r"\b(?:policy|calendar|fiscal)\s+year\b|"
    r"\bexcel(?:\s+serial)?\s+date\b|"
    r"\b(?:policy|claim)\s+(?:identifier|id|number|no)\b|"
    r"\b(?:page|row|column)\s+(?:index|number|no|count)\b",
    re.IGNORECASE,
)
_FINANCIAL_NOTATION = re.compile(
    r"[.,]|[$€£¥₤₹₽]|^[+(\-]|[)\-]$|\b(?:CR|DR)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _UnplacedEvidence:
    amounts: dict[str, str] = field(default_factory=dict)
    ambiguous_values: dict[str, str] = field(default_factory=dict)


def _parsed_financial_cells(
    row: RawRow, mapping: ColumnMapping, locale: str | None
) -> dict[str, str]:
    """Numeric text physically read under columns mapped as financial."""
    values = _row_values(row, mapping)
    return {
        name: text
        for name in MONEY_FIELDS
        if (text := (values.get(name) or "").strip())
        and parse_money(text, locale).value is not None
    }


def _claim_like_without_identifier(values: dict[str, str]) -> bool:
    """Independent row evidence that weak numeric cells belong to claim data."""
    if parse_status(values.get("claim_status", "")) is not ClaimStatus.UNKNOWN:
        return True
    return any(
        parse_date(values.get(field, ""), None).value is not None
        for field in DATE_FIELDS
    )


def _unplaced_evidence(
    row: RawRow,
    mapping: ColumnMapping,
    locale: str | None,
    *,
    table_context: str = "unknown",
) -> _UnplacedEvidence:
    """Classify parsed numeric cells using the row and its table, not their shape."""
    values = _row_values(row, mapping)
    parsed = _parsed_financial_cells(row, mapping, locale)
    if not parsed:
        return _UnplacedEvidence()

    claim_like = _claim_like_without_identifier(values)
    context_text = " ".join(
        row.cell(index)
        for index in range(len(row.cells))
        if mapping.fields.get(index) not in MONEY_FIELDS
    )
    if not claim_like and _NON_MONEY_ROW.search(clean_text(context_text)):
        return _UnplacedEvidence()
    if claim_like or table_context == "monetary-only":
        return _UnplacedEvidence(amounts=parsed)
    if table_context == "claims" and any(
        _FINANCIAL_NOTATION.search(text) for text in parsed.values()
    ):
        return _UnplacedEvidence(amounts=parsed)
    return _UnplacedEvidence(ambiguous_values=parsed)


def unplaced_money(
    row: RawRow, mapping: ColumnMapping, locale: str | None
) -> dict[str, str]:
    """Amounts printed under mapped money columns on a row nothing claimed.

    This compatibility helper returns only values whose own row establishes
    claim context. :func:`build_claims` additionally uses table context and
    preserves unresolved numeric cells separately rather than guessing.
    """
    return _unplaced_evidence(row, mapping, locale).amounts


def _table_context(
    table: RawTable,
    mapping: ColumnMapping,
    shapes: set[str],
) -> str:
    """Whether this table supplies context that its numeric cells are financial."""
    mapped = mapping.mapped_fields
    if mapped and mapped <= set(MONEY_FIELDS):
        return "monetary-only"
    if any(
        not is_structural_row(row, mapping)
        and claim_identifier(row, mapping, shapes) is not None
        for row in table.rows
    ):
        return "claims"
    return "unknown"


def accepted_identifier_shapes(
    tables: Sequence[RawTable], mapping: ColumnMapping
) -> set[str]:
    """Which shapes this document uses for claim numbers.

    Detail layouts print each claim as a stack of lines, and the lines under
    the first one land in the claim-number column carrying a cause code, a
    class code or an injury description. Those are not claims, and no rule
    downstream can tell: they arrive with a plausible-looking identifier and
    inflate the count, the sums and the duplicate checks alike.

    A claim number is normally one token. Where a document has such cells they
    define what an identifier looks like here, and anything shaped differently
    is a continuation line. Where it has none — some carriers print the
    insured's name into the same cell — the shape used most often stands in,
    since a layout repeats itself even when it is not tidy.
    """
    candidates: list[str] = []
    for table in tables:
        index = mapping_for(table, mapping).index_of("claim_number")
        if index is None:
            continue
        for row in table.rows:
            cell = row.cell(index).strip()
            if cell and is_identifier_candidate(cell):
                candidates.append(cell)
    return consensus_shapes(candidates)


def claim_identifier(
    row: RawRow, mapping: ColumnMapping, shapes: set[str]
) -> str | None:
    """The claim number this row opens, or None if it continues the one above.

    A neighbouring column bleeds into the identifier cell on some pages and
    not others, so the same claim series arrives clean here and as
    "502-124958-001/8459543132US Acc/Ben: FL/" there. An identifier leads its
    cell and the contamination trails it, which is what separates this from a
    continuation line: the junk that detail layouts put in this column does
    not begin with an identifier either. Where only the leading token matches,
    the trailing text belongs to another column and is dropped from the
    number rather than carried into it.
    """
    index = mapping.index_of("claim_number")
    if index is None:
        return None
    # No shape consensus means nothing to compare against; leading_identifier
    # falls back to the basic tests rather than rejecting every row and
    # reporting an empty document, which R-20 would then have to catch.
    return leading_identifier(row.cell(index), shapes)


def has_claim_identifier(
    row: RawRow, mapping: ColumnMapping, shapes: set[str]
) -> bool:
    """Whether this row opens a claim, rather than continuing the one above."""
    return claim_identifier(row, mapping, shapes) is not None


def is_structural_row(row: RawRow, mapping: ColumnMapping) -> bool:
    """True for section headings and page furniture, not claims.

    Documents grouped by policy period interleave "Policy Period: …" headings
    between claims, and page footers add lines like "Report: LossRunSummary".
    Both land in the claim-number column carrying a label and its colon, and
    how much of the value follows the colon depends on where the column
    boundary happens to fall. A claim number contains no colon at all, so a
    worded label ahead of one marks the row as printed furniture.
    """
    index = mapping.index_of("claim_number")
    if index is None:
        return False
    label, separator, _ = row.cell(index).strip().partition(":")
    return bool(separator) and bool(label) and label.replace(" ", "").isalpha()


def mapping_for(table: RawTable, mapping: ColumnMapping) -> ColumnMapping:
    """The column mapping that fits this page.

    Column detection is per page, and pages of one document do not always agree
    — a label that fits on one line here wraps into two columns there. Reading a
    page through another page's mapping puts every value after the extra column
    one field to the left.
    """
    if table.headers and table.headers != mapping.headers:
        return build_mapping(table.headers, None, mapping.fingerprint)
    return mapping


def build_claims(
    tables: Sequence[RawTable],
    mapping: ColumnMapping,
    locale: str | None,
    date_order: str | None,
    *,
    dash_means_zero: bool = False,
    source_method: SourceMethod = SourceMethod.DIGITAL,
    confidence_cap: float = 1.0,
) -> tuple[list[Claim], list[str], list[UnplacedRow]]:
    """Normalise every row of every page, folding wrapped lines into their claim.

    Rows that carry money and cannot be attached to a claim come back
    separately from the warnings. A warning is a note to the reader; an amount
    the app parsed and could not place is a hole in the reading, and only the
    rules can say so.
    """
    claims: list[Claim] = []
    warnings: list[str] = []
    unplaced: list[UnplacedRow] = []
    furniture = page_furniture(tables, mapping, locale)
    shapes = accepted_identifier_shapes(tables, mapping)

    for table in tables:
        table_mapping = mapping_for(table, mapping)
        table_context = _table_context(table, table_mapping, shapes)

        for row in table.rows:
            if is_structural_row(row, table_mapping):
                continue
            text = _normalised(row)
            if not text or text in furniture:
                continue  # a running header, footer or page marker

            # A row whose claim-number cell holds something that is not an
            # identifier is the continuation of the claim above it, not a new
            # one. Folding it in keeps its text; treating it as a claim
            # multiplies the count by however many lines each claim occupies.
            identifier = claim_identifier(row, table_mapping, shapes)
            if identifier is None:
                extra = clean_text(" ".join(row.cells))
                # A continuation line carries prose. A row carrying money or a
                # date is a claim whose number could not be identified, and
                # folding that into the description above would bury real
                # figures inside someone else's narrative.
                if extra and claims and _continuation_text(row, table_mapping):
                    previous = claims[-1]
                    previous.loss_description = clean_text(
                        f"{previous.loss_description or ''} {extra}"
                    )
                elif extra:
                    _record_discard(
                        row, table_mapping, locale, warnings, unplaced, extra,
                        table_context=table_context,
                    )
                continue

            claim = build_claim(
                row, table_mapping, locale, date_order,
                dash_means_zero=dash_means_zero,
                source_method=source_method,
                confidence_cap=confidence_cap,
            )
            if claim is not None:
                # The cell may have carried a neighbouring column's text; the
                # claim is filed under the identifier, not under the smear.
                if claim.claim_number != identifier:
                    claim.claim_number = identifier
                claims.append(claim)
                continue

            extra = _continuation_text(row, table_mapping)
            if extra and claims:
                previous = claims[-1]
                previous.loss_description = clean_text(
                    f"{previous.loss_description or ''} {extra}"
                )
            elif not row.is_blank():
                preview = " ".join(cell for cell in row.cells if cell)
                _record_discard(
                    row, table_mapping, locale, warnings, unplaced, preview,
                    table_context=table_context,
                )
    return claims, warnings, unplaced


def _record_discard(
    row: RawRow,
    mapping: ColumnMapping,
    locale: str | None,
    warnings: list[str],
    unplaced: list[UnplacedRow],
    preview: str,
    *,
    table_context: str = "unknown",
) -> None:
    """Note a row nothing could take and retain any numeric evidence on it.

    A text-only row stays a warning: nothing measurable was lost, and raising
    a finding for every stray line would bury the ones that matter. A row with
    figures under financial columns are recorded on the document instead,
    where reconciliation can distinguish confirmed from ambiguous evidence.
    """
    evidence = _unplaced_evidence(
        row, mapping, locale, table_context=table_context
    )
    if evidence.amounts or evidence.ambiguous_values:
        unplaced.append(
            UnplacedRow(
                page=row.page,
                row=row.line_index,
                amounts=evidence.amounts,
                ambiguous_values=evidence.ambiguous_values,
            )
        )
        return
    warnings.append(
        f"Page {row.page}: skipped a row with no claim number ({preview[:60]})."
    )


def stated_periods(
    stated: dict[str, list[str]], date_order: str | None
) -> list[tuple[date, date]]:
    """Every policy term the document names in its own columns.

    XL prints an inception and an expiry beside each claim and three different
    policies across the page. One document-level term cannot describe that, and
    picking the commonest would put two thirds of the claims outside the term
    they are reported under. Each pairing that actually appears is kept, which
    is what R-09 needs to judge a date of loss against the right one.
    """
    starts = stated.get("policy_period_start", [])
    ends = stated.get("policy_period_end", [])
    periods: list[tuple[date, date]] = []
    for start_text, end_text in dict.fromkeys(zip(starts, ends)):
        start = parse_date(start_text, date_order).value
        end = parse_date(end_text, date_order).value
        if start and end and start <= end:
            periods.append((start, end))
    return sorted(set(periods))


def _reads_as_column_labels(candidate: str, headers: Sequence[str]) -> bool:
    """Whether this "carrier name" is really the table's own column headings.

    A loss run exported from a spreadsheet begins with its header row, so the
    scan of the top of page one reads column labels and returns them as the
    company. No carrier is called "Status Currency Indemnity", and every word
    of it appears in the headings above the table.
    """
    words = {word for word in candidate.lower().split() if word.isalpha()}
    printed = {
        word
        for label in headers
        for word in label.lower().split()
        if word.isalpha()
    }
    return bool(words) and words <= printed


def read_document_columns(
    tables: Sequence[RawTable],
) -> dict[str, list[str]]:
    """Document-level facts a carrier printed as columns instead of a heading.

    A spreadsheet export repeats the company, the insured, the policy and its
    term on every claim. Those are document facts, and the canonical schema
    holds them once -- so they are read once here, and only where the rows
    agree. A column that says three different things across the page is
    describing three policies, not one document, and the disagreement is
    reported rather than resolved by picking the first.
    """
    furniture = page_furniture(tables)
    values: dict[str, list[str]] = {}
    for table in tables:
        columns = {
            index: field
            for index, label in enumerate(table.headers)
            if (field := guess_document_field(label))
        }
        if not columns:
            continue
        for row in table.rows:
            # A footer printed on every page occupies the same columns as the
            # claims and says nothing about the policy. Two of them are enough
            # to make eighty-four rows look as though they disagree.
            if _normalised(row) in furniture:
                continue
            for index, field in columns.items():
                text = clean_text(row.cell(index))
                if text:
                    values.setdefault(field, []).append(text)
    return values


def agreed(values: Sequence[str]) -> str | None:
    """The one thing these rows say, or None if they do not agree."""
    distinct = {value for value in values if value}
    return next(iter(distinct)) if len(distinct) == 1 else None


def _covers(row_text: str, claim_count: int | None) -> int:
    """Whether a totals row says it covers the claims that were extracted.

    A total is only worth checking against claims it actually totals. Loss runs
    arrive inside board packets holding several of them, and a row reading
    "Totals: 10 ... 226,605.00" belongs to whichever run has ten claims in it,
    not to the twenty-eight that were read. Comparing the two reports a
    quarter-million-pound discrepancy that nobody made.

    Returns 1 when the row names the same count, -1 when it names a different
    one, and 0 when it names none and so says nothing either way.
    """
    if claim_count is None:
        return 0
    printed = _first_match(row_text, _COUNT_PATTERNS)
    stated = parse_int(printed) if printed else None
    if stated is None:
        return 0
    return 1 if stated == claim_count else -1


def collect_printed_totals(
    tables: Sequence[RawTable],
    mapping: ColumnMapping,
    locale: str | None,
    claim_count: int | None = None,
) -> dict[str, Decimal | None]:
    """Read the footer totals row — the numbers R-04 checks against."""
    best: dict[str, Decimal | None] = {}
    best_rank = (0, 0, 0)
    for table in tables:
        table_mapping = mapping_for(table, mapping)
        for row in table.total_rows:
            totals: dict[str, Decimal | None] = {}
            for index, field_name in table_mapping.fields.items():
                if field_name not in MONEY_FIELDS:
                    continue
                cell = row.cell(index).strip()
                if not cell:
                    continue
                parsed = parse_money(cell, locale)
                if parsed.value is not None:
                    totals[field_name] = parsed.value
            # Documents grouped by policy period print a subtotal per section
            # and one grand total. Only the grand total covers every claim, so
            # it outranks a subtotal no matter which was seen first. A row that
            # names a different claim count outranks nothing: whatever it
            # totals, it is not these claims.
            rank = (
                _covers(row.text(), claim_count),
                1 if GRAND_TOTAL_PATTERN.search(row.text()) else 0,
                len(totals),
            )
            if totals and rank > best_rank:
                best, best_rank = totals, rank
    return best


#: The claim count printed inside a subtotal row, e.g. "# Claims: 6".
_SECTION_COUNT = re.compile(r"#?\s*claims?\s*:?\s*(\d[\d,]*)", re.IGNORECASE)
#: The date a subtotal row leads with, naming the term it totals.
_SECTION_DATE = re.compile(r"\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4}")


def collect_printed_sections(
    tables: Sequence[RawTable],
    mapping: ColumnMapping,
    locale: str | None,
    date_order: str | None,
) -> list[PrintedSection]:
    """Read each per-term subtotal the document prints.

    These are the only other numbers besides the grand total that the carrier
    committed to, so they let each policy term be checked on its own rather
    than only the document as a whole.
    """
    sections: list[PrintedSection] = []
    for table in tables:
        table_mapping = mapping_for(table, mapping)
        for row in table.total_rows:
            if GRAND_TOTAL_PATTERN.search(row.text()):
                continue  # the report total, already held on the document

            totals: dict[str, Decimal | None] = {}
            for index, field_name in table_mapping.fields.items():
                if field_name not in MONEY_FIELDS:
                    continue
                cell = row.cell(index).strip()
                if cell:
                    parsed = parse_money(cell, locale)
                    if parsed.value is not None:
                        totals[field_name] = parsed.value
            if not totals:
                continue

            text = row.text()
            count = _SECTION_COUNT.search(text)
            start = _SECTION_DATE.search(row.cell(0))
            sections.append(
                PrintedSection(
                    label=clean_text(row.cell(0)) or clean_text(text)[:40],
                    period_start=(
                        parse_date(start.group(0), date_order).value if start else None
                    ),
                    printed_totals=totals,
                    printed_claim_count=parse_int(count.group(1)) if count else None,
                    page=row.page,
                )
            )
    return sections


# --------------------------------------------------------------------------
# Inference over the whole document
# --------------------------------------------------------------------------


def _money_tokens(tables: Sequence[RawTable], mapping: ColumnMapping) -> list[str]:
    indexes = [i for i, name in mapping.fields.items() if name in MONEY_FIELDS]
    tokens: list[str] = []
    for table in tables:
        for row in list(table.rows) + list(table.total_rows):
            tokens.extend(row.cell(index) for index in indexes)
    return [token for token in tokens if token.strip()]


def _table_date_tokens(
    tables: Sequence[RawTable], mapping: ColumnMapping
) -> list[str]:
    """Date cells from the claims table itself."""
    indexes = [i for i, name in mapping.fields.items() if name in DATE_FIELDS]
    tokens: list[str] = []
    for table in tables:
        for row in table.rows:
            tokens.extend(row.cell(index) for index in indexes)
    return [token for token in tokens if token.strip()]


def _metadata_date_tokens(metadata: DocumentMetadata) -> list[str]:
    """Dates from the header block, which is a separate format context."""
    return [
        text
        for text in (
            metadata.valuation_date_text,
            metadata.policy_period_start_text,
            metadata.policy_period_end_text,
        )
        if text and text.strip()
    ]


def resolve_date_order(
    tables: Sequence[RawTable],
    mapping: ColumnMapping,
    metadata: DocumentMetadata,
    locale: str | None,
) -> tuple[DateOrderInference, DateOrderInference]:
    """Settle the day/month order for the table, and for the header block.

    The table and the header are resolved **independently** and never fall
    back on each other. A European report can print
    ``Valuation Date: 06/30/2024`` above dates written the other way round, so
    letting the header settle the table would move every loss date — that is
    the whole reason this function exists rather than one document-wide
    inference. The same reasoning runs in the other direction, and matters
    more than it looks: if a claims table is genuinely ambiguous throughout
    (no row's day exceeds 12, and the numeric locale gives no evidence
    either), the header's own resolved order must **not** be borrowed to
    parse the table just because it happens to be available — a policy period
    ending on the 31st proves the header's convention for the header, not for
    a table that offers no evidence of its own. Borrowing it would parse
    every ambiguous claim date silently, exactly the guess spec section 4
    forbids. A table with no evidence of its own stays ``source="default"``,
    so :attr:`DateOrderInference.for_parsing` returns ``None`` and every date
    in it comes back null with ``AMBIGUOUS_DATE_ORDER`` — correct, if less
    convenient.
    """
    table_order = infer_date_order(_table_date_tokens(tables, mapping), locale)
    header_order = infer_date_order(_metadata_date_tokens(metadata), locale)
    return table_order, header_order


#: What carriers call a line of business when they do not use the abbreviation.
_LOB_WORDS: tuple[tuple[str, LineOfBusiness], ...] = (
    ("WORKERSCOMP", LineOfBusiness.WC),
    ("WORKERSCOMPENSATION", LineOfBusiness.WC),
    ("WORKMENSCOMP", LineOfBusiness.WC),
    ("GENERALLIABILITY", LineOfBusiness.GL),
    ("LIABILITY", LineOfBusiness.GL),
    ("PUBLICLIABILITY", LineOfBusiness.GL),
    ("AUTOMOBILE", LineOfBusiness.AUTO),
    ("MOTOR", LineOfBusiness.AUTO),
    ("COMMERCIALAUTO", LineOfBusiness.AUTO),
    ("PROPERTY", LineOfBusiness.PROP),
    ("UMBRELLA", LineOfBusiness.UMB),
    ("EXCESS", LineOfBusiness.UMB),
)


def _line_of_business(text: str | None) -> LineOfBusiness | None:
    if not text:
        return None
    token = clean_text(text).upper().replace(" ", "")
    for member in LineOfBusiness:
        if token.startswith(member.value):
            return member
    for word, member in _LOB_WORDS:
        if word in token:
            return member
    return LineOfBusiness.OTHER


# --------------------------------------------------------------------------
# The pipeline
# --------------------------------------------------------------------------


def run_pipeline(
    source: str | Path | IngestedFile,
    *,
    profile: CarrierProfile | None = None,
    profiles_dir: Path | None = None,
    mapping_override: ColumnMapping | None = None,
    reconcile_config: ReconcileConfig | None = None,
    use_vision: bool = True,
    vision_extractor: Any | None = None,
    use_llm: bool = False,
    llm_client: Any | None = None,
) -> ExtractionResult:
    """Run stages 0 through 5 and return everything the UI needs."""
    ingested = source if isinstance(source, IngestedFile) else ingest_path(source)
    classification = classify_pdf(ingested.path)

    digital_pages = classification.digital_pages
    # `pages=None` means "extract every page" (spec: no filter). Passing the
    # empty list through `or None` when a document is fully scanned would
    # silently mean the opposite of what was intended -- pdfplumber would
    # eagerly parse every heavy scanned page for a document that has nothing
    # digital on it at all. An explicit empty list correctly extracts none.
    extraction = extract_digital.extract_pdf(ingested.path, pages=digital_pages)
    tables = list(extraction.tables)
    metadata = extraction.metadata
    warnings: list[str] = []
    processed_pages = set(extraction.page_texts)
    failed_pages: set[int] = set()
    skipped_pages: set[int] = set()
    unresolved_pages: set[int] = set()

    scanned_pages = classification.scanned_pages
    vision_tables: list[RawTable] = []
    if scanned_pages and use_vision:
        from core.extract_vision import (
            VisionExtraction,
            VisionUnavailable,
            extract_scanned_pages,
        )

        extractor = vision_extractor or extract_scanned_pages
        try:
            vision_output = extractor(ingested.path, scanned_pages)
            if isinstance(vision_output, VisionExtraction):
                vision_tables = list(vision_output.tables)
                for page, message in vision_output.failures.items():
                    failed_pages.add(page)
                    warnings.append(message)
            else:
                # Preserve compatibility with injected extractors that return
                # tables directly. A requested page they do not return is not
                # evidence of successful processing.
                vision_tables = list(vision_output)

            # A page counts as read only where the reader actually returned
            # something off it. A well-formed answer carrying no rows is a
            # model declining to find a table, which is not the same fact as a
            # page having none -- and on a scan there is no second reader to
            # settle which it was. Headers and page metadata do not decide it
            # either: a shape is not a reading.
            read_vision_pages = {
                table.page for table in vision_tables
                if table.rows or table.total_rows
            }
            empty_vision_pages = {
                table.page for table in vision_tables
            } - read_vision_pages
            processed_pages.update(read_vision_pages)
            unresolved_pages.update(empty_vision_pages - failed_pages)
            if empty_vision_pages:
                joined = ", ".join(str(page) for page in sorted(empty_vision_pages))
                warnings.append(
                    f"Vision extraction returned no rows for page(s) {joined}. "
                    f"Whether they hold claims is unknown."
                )
            unreturned = (
                set(scanned_pages)
                - read_vision_pages
                - empty_vision_pages
                - failed_pages
            )
            if unreturned:
                failed_pages.update(unreturned)
                joined = ", ".join(str(page) for page in sorted(unreturned))
                warnings.append(
                    f"Vision extraction returned no result for page(s) {joined}."
                )
            tables.extend(vision_tables)
        except VisionUnavailable as error:
            failed_pages.update(scanned_pages)
            warnings.append(str(error))
    elif scanned_pages:
        skipped_pages.update(scanned_pages)
        warnings.append(
            f"{len(scanned_pages)} page(s) are scans and were not read. "
            f"Turn on vision extraction to include them."
        )

    # Fingerprint the letterhead plus the header labels.
    # A scanned page prints its valuation date and claim count like any other,
    # but there is no text layer to read them from, so the vision pass reports
    # them and they are used only where the digital pass found nothing.
    for table in vision_tables:
        if metadata.valuation_date_text is None and table.valuation_date_text:
            metadata.valuation_date_text = table.valuation_date_text
        if metadata.printed_claim_count is None and table.printed_claim_count:
            metadata.printed_claim_count = table.printed_claim_count

    first_page_text = extraction.page_texts.get(
        min(extraction.page_texts), ""
    ) if extraction.page_texts else ""
    headers = tables[0].headers if tables else []
    fingerprint_value = fingerprint(
        page_top_text(first_page_text), headers, metadata.carrier
    )

    if profile is None:
        profile = load_profile(fingerprint_value, profiles_dir)

    mapping = mapping_override or build_mapping(
        headers,
        profile,
        fingerprint_value,
        samples=sample_rows(tables),
        use_llm=use_llm,
        llm_client=llm_client,
    )

    # Number and date conventions, derived from the document itself.
    locale_inference = infer_locale(_money_tokens(tables, mapping))
    if profile and profile.number_locale:
        locale_inference = LocaleInference(
            profile.number_locale, True, "carrier profile"
        )
    date_inference, header_date_inference = resolve_date_order(
        tables, mapping, metadata, locale_inference.for_parsing
    )
    if profile and profile.date_order:
        date_inference = DateOrderInference(
            profile.date_order, True, "carrier profile", source="evidence"
        )
        header_date_inference = date_inference

    locale = locale_inference.for_parsing
    date_order = date_inference.for_parsing
    dash_means_zero = bool(profile and profile.dash_means_zero)

    digital_tables = [table for table in tables if table.strategy != "vision"]

    # Where the document states a fact in a column rather than a heading, that
    # column is the better source: it is the document saying so directly, not
    # a guess about which line of the letterhead names the carrier.
    stated = read_document_columns(digital_tables)
    claims, row_warnings, unplaced_rows = build_claims(
        digital_tables, mapping, locale, date_order, dash_means_zero=dash_means_zero
    )
    warnings.extend(row_warnings)

    if vision_tables:
        vision_claims, vision_warnings, vision_unplaced = build_claims(
            vision_tables, mapping, locale, date_order,
            dash_means_zero=dash_means_zero,
            source_method=SourceMethod.VISION,
            confidence_cap=0.85,
        )
        claims.extend(vision_claims)
        warnings.extend(vision_warnings)
        unplaced_rows.extend(vision_unplaced)
        claims.sort(key=lambda claim: (claim.source_page, claim.source_row or 0))

    # Document-level facts.
    document_issues: dict[str, NullReason] = {}
    header_order = header_date_inference.for_parsing or date_order
    valuation = parse_date(metadata.valuation_date_text or "", header_order)
    if valuation.value is None and metadata.valuation_date_text:
        document_issues["valuation_date"] = valuation.reason or NullReason.UNPARSEABLE
    period_start = parse_date(metadata.policy_period_start_text or "", header_order)
    period_end = parse_date(metadata.policy_period_end_text or "", header_order)

    # A loss run grouped by policy period lists several terms, and page 1 only
    # declares the first. Judging every claim against that one term reports
    # each later year as out of period, burying the real findings. The document
    # covers the whole span, so widen the window to what it actually lists.
    period_start_value, period_end_value = period_start.value, period_end.value
    declared_periods = [
        (start_value, end_value)
        for start_text, end_text in metadata.policy_periods
        if (start_value := parse_date(start_text, header_order).value) is not None
        and (end_value := parse_date(end_text, header_order).value) is not None
    ]
    if len(declared_periods) > 1:
        period_start_value = min(start for start, _ in declared_periods)
        period_end_value = max(end for _, end in declared_periods)

    rows_seen_per_page: dict[int, int] = {}
    furniture = page_furniture(tables)
    for table in tables:
        table_mapping = mapping_for(table, mapping)
        # Count rows that carry a claim number: those are the rows that ought
        # to survive stitching. A row skipped for having no claim number is
        # already reported on its own, and counting it here would make R-19
        # repeat that warning as a phantom stitching loss.
        index = table_mapping.index_of("claim_number")
        rows_seen_per_page[table.page] = sum(
            1
            for row in table.rows
            if index is not None
            and row.cell(index).strip()
            and not is_structural_row(row, table_mapping)
            and (text := _normalised(row))
            and text not in furniture
        )

    printed_totals = collect_printed_totals(tables, mapping, locale, len(claims))

    # Recoveries: settle the carrier's sign convention before anything is
    # reconciled, and apply it to the printed totals too — otherwise R-04
    # would compare a corrected column against an uncorrected footer.
    recovery_sign = infer_recovery_sign(
        (
            claim.claim_number,
            claim.paid_total,
            claim.reserve_total,
            claim.recovery_total,
            claim.incurred_total,
        )
        for claim in claims
    )
    if profile and profile.recovery_convention:
        recovery_sign = RecoverySignInference(
            credit_convention=profile.recovery_convention == "credit",
            confident=True,
            evidence="carrier profile",
        )
    if recovery_sign.should_negate:
        for claim in claims:
            if claim.recovery_total is not None:
                claim.recovery_total = -claim.recovery_total
        printed_totals = {
            name: (-value if name == "recovery_total" and value is not None else value)
            for name, value in printed_totals.items()
        }

    # Currency: what the rows actually show, not what the default assumes.
    # Defaulting to USD and then comparing that default against a euro symbol
    # is how R-16 accuses every European document of mixing currencies.
    declared = (metadata.currency or "").strip().upper()[:3]
    declared = declared if len(declared) == 3 and declared.isalpha() else ""
    symbols = {claim.currency for claim in claims if claim.currency}
    stated_currency = agreed(stated.get("currency", []))
    if stated_currency and len(stated_currency) == 3 and stated_currency.isalpha():
        # A currency column is the document naming its own currency, which
        # beats a symbol scraped off the amounts.
        currency = stated_currency.upper()
    elif declared:
        currency = declared
    elif len(symbols) == 1:
        currency = next(iter(symbols))
    else:
        currency = "USD"
    currencies_seen = sorted(symbols | ({declared} if declared else set()))

    column_carrier = agreed(stated.get("carrier", []))
    column_insured = agreed(stated.get("named_insured", []))
    column_policy = agreed(stated.get("policy_number", []))
    column_lob = agreed(stated.get("line_of_business", []))
    column_start = parse_date(agreed(stated.get("policy_period_start", [])) or "",
                              date_order).value
    column_end = parse_date(agreed(stated.get("policy_period_end", [])) or "",
                            date_order).value
    if not declared_periods:
        declared_periods = stated_periods(stated, date_order)
    letterhead_carrier = metadata.carrier
    if letterhead_carrier and _reads_as_column_labels(letterhead_carrier, headers):
        # The top of page one *is* the table's own heading here, so the scan
        # returned three column labels as the company's name.
        letterhead_carrier = None
    if "carrier" in stated:
        # A column naming the company settles it, including when it names two.
        # The letterhead scan reads the top of page one, which on a sheet like
        # this is the first claim -- so it would report one of the two carriers
        # as the document's, and file a third of the book under the wrong one.
        letterhead_carrier = None

    document = LossRunDocument(
        document_id=ingested.document_id,
        source_filename=ingested.source_filename,
        file_sha256=ingested.sha256,
        carrier=column_carrier or letterhead_carrier,
        named_insured=column_insured or metadata.named_insured,
        policy_number=column_policy or metadata.policy_number,
        policy_period_start=period_start_value or column_start,
        policy_period_end=period_end_value or column_end,
        line_of_business=_line_of_business(metadata.line_of_business)
        or _line_of_business(column_lob),
        valuation_date=valuation.value,
        currency=currency,
        locale_hint=locale_inference.locale,
        locale_confident=locale_inference.confident,
        date_order=date_inference.order,
        date_order_confident=date_inference.confident,
        page_count=classification.page_count,
        extraction_method=classification.extraction_method,
        scanned_pages=scanned_pages,
        processed_pages=sorted(processed_pages),
        failed_pages=sorted(failed_pages),
        skipped_pages=sorted(skipped_pages),
        unresolved_pages=sorted(unresolved_pages),
        unplaced_rows=unplaced_rows,
        printed_totals=printed_totals,
        printed_claim_count=metadata.printed_claim_count,
        policy_periods=declared_periods,
        rows_seen_per_page=rows_seen_per_page,
        column_mapping=mapping.decisions,
        printed_sections=collect_printed_sections(
            tables, mapping, locale_inference.locale, date_inference.order
        ),
        claims=claims,
        currencies_seen=currencies_seen,
        document_issues=document_issues,
        profile_fingerprint=fingerprint_value,
        profile_name=profile.profile_name if profile else None,
        recovery_convention="credit" if recovery_sign.should_negate else None,
    )

    config = reconcile_config or _config_for(profile)
    return ExtractionResult(
        document=document,
        reconciliation=reconcile(document, config),
        mapping=mapping,
        classification=classification,
        source_path=ingested.path,
        locale=locale_inference,
        date_order=date_inference,
        recovery_sign=recovery_sign,
        tables=tables,
        profile=profile,
        warnings=warnings,
    )


def _config_for(profile: CarrierProfile | None) -> ReconcileConfig:
    if profile is None:
        return ReconcileConfig()
    try:
        tolerance = Decimal(str(profile.money_tolerance))
    except Exception:  # pragma: no cover - a hand-edited profile
        tolerance = Decimal("0.01")
    return ReconcileConfig(money_tolerance=tolerance)


def rerun_reconciliation(
    document: LossRunDocument, config: ReconcileConfig | None = None
) -> ReconciliationResult:
    """Re-run the rules after a human edits a cell (spec section 11)."""
    return reconcile(document, config or ReconcileConfig())


def edit_claims(
    result: ExtractionResult, records: Sequence[dict[str, Any]]
) -> ExtractionResult:
    """Commit a grid edit, reconciliation and audit chronology together."""
    before = summarise_review(
        result.reconciliation.findings, result.document.review_log
    ).headline()
    old_entries = len(result.document.review_log.entries)
    document = apply_edits(result.document, records)
    if len(document.review_log.entries) == old_entries:
        # pandas/Arrow can change null/date representations without a reviewer
        # changing a value. Do not publish or re-run for that round trip.
        return result
    reconciliation = rerun_reconciliation(document, _config_for(result.profile))
    after = summarise_review(reconciliation.findings, document.review_log).headline()
    for entry in document.review_log.entries[old_entries:]:
        entry.status_before = before
        entry.status_after = after
    result.document = document
    result.reconciliation = reconciliation
    return result


def resolve_finding(
    result: ExtractionResult,
    finding: Finding,
    action: ReviewAction,
    *,
    note: str = "",
    corrected_value: Any = None,
    reviewer: str = LOCAL_REVIEWER,
) -> ExtractionResult:
    """Record what a reviewer decided about one finding, and act on it.

    Confirming and dismissing change no data at all: they add a line to the
    log saying somebody looked, and the rule engine is never told. Correcting
    changes one cell and then runs *every* rule again over the result — not the
    rules that look related, because working out which rules a value can reach
    is exactly the kind of cleverness that quietly stops re-checking something.
    The engine is cheap and correctness is not.

    Either way the finding survives. If it still holds after a correction the
    engine raises it again, and the log shows a reviewer tried; if it no longer
    holds, the log shows what the value was before.
    """
    original_document = result.document
    document = original_document.model_copy(deep=True)
    reconciliation = result.reconciliation
    before_headline = summarise_review(
        result.reconciliation.findings, original_document.review_log
    ).headline()
    was = after = None
    claim = claim_for(original_document, finding)

    if action is ReviewAction.CORRECTED:
        # Refused rather than recorded. A log line reading "corrected" against
        # a finding that has no cell to correct describes a change nobody made,
        # and an audit trail that says so about one decision cannot be trusted
        # about the others.
        if finding.scope is FindingScope.CLAIM_GROUP:
            raise ValueError(
                f"{finding.rule_id} concerns multiple physical rows. Select the "
                "specific row in the claims table before correcting its number."
            )
        if not finding.claim_number:
            raise ValueError(
                f"{finding.rule_id} is about the whole document rather than one "
                f"row, so there is no cell here to correct. Confirm or dismiss "
                f"it, or change the value in the claims table."
            )
        if claim is None:
            raise ValueError(
                f"No claim {finding.claim_number!r} to correct — it may have been "
                f"renamed or deleted since this finding was raised."
            )
        if not finding.field:
            raise ValueError(
                f"{finding.rule_id} is about claim {finding.claim_number} as a "
                f"whole rather than one of its fields, so there is no single "
                f"cell here to correct."
            )
        if finding.field not in Claim.model_fields:
            # A rule may flag something the claim does not store -- a printed
            # claim count, a column that was never mapped. Writing it into the
            # row would be dropped on the way in and logged as a correction
            # that happened, which is the one thing an audit trail may not do.
            raise ValueError(
                f"{finding.rule_id} is about {finding.field!r}, which is not a "
                f"field of a claim, so there is nothing here to correct."
            )
        if finding.field not in REVIEW_COLUMNS:
            raise ValueError(f"{finding.field!r} is not an editable claim value")
        was = _audit_value(claim, finding.field)
        records = to_records(original_document, review_columns(original_document))
        # By identity, not by claim number: two rows can share a number, and
        # the row this finding is about is not necessarily the first of them.
        row = next(
            record for record in records
            if record.get(ROW_ID_COLUMN) == claim.row_id
        )
        row[finding.field] = corrected_value
        # The edit and the re-run both complete before either is visible. A
        # document whose claims have moved and whose findings have not is worse
        # than an edit that failed: it reads as reconciled against figures that
        # are no longer there, and nothing says so.
        edited = apply_edits(original_document, records, audit=False)
        corrected = next(c for c in edited.claims if c.row_id == claim.row_id)
        after = _audit_value(corrected, finding.field)
        if was == after:
            raise ValueError("No value changed. Confirm or dismiss the finding instead.")
        reconciliation = rerun_reconciliation(edited, _config_for(result.profile))
        document = edited
        claim = corrected
    elif claim is not None:
        claim = next(c for c in document.claims if c.row_id == claim.row_id)

    entry = resolution_for(
        finding,
        action,
        reviewer=reviewer,
        note=note,
        before=was,
        after=after,
        row_id=claim.row_id if claim else "",
        where=claim.where() if claim else "",
        status_before=before_headline,
        status_after="",
    )
    document.review_log.record(entry)
    # Filled in after the entry is in the log, because this decision is part of
    # what the document now reads as: dismissing the last open flag is what
    # moves "flags to review" to "flags reviewed", and a status computed a
    # moment earlier would record every decision as having changed nothing.
    # Only this entry is touched, and only before anyone has seen it.
    entry.status_after = summarise_review(
        reconciliation.findings, document.review_log
    ).headline()
    # Publish only after editing, reconciliation and audit writing all finish.
    # Any failure above leaves the caller's previous valid pair untouched.
    result.document = document
    result.reconciliation = reconciliation
    return result


def claim_for(document: LossRunDocument, finding: Finding) -> Claim | None:
    """The claim a finding is about, or None where it is about the document.

    Findings about one physical row carry that row's identity. Group and
    document findings deliberately return no claim: choosing the first row
    sharing a number recreates the duplicate-number bug.
    """
    if finding.scope is FindingScope.CLAIM:
        for claim in document.claims:
            if claim.row_id == finding.subject:
                return claim
    return None


def save_confirmed_mapping(
    result: ExtractionResult,
    mapping: ColumnMapping | None = None,
    *,
    profiles_dir: Path | None = None,
    confirmed_by_human: bool = True,
) -> CarrierProfile:
    """Save the mapping screen's choices as a reusable carrier profile.

    Everything saved is format structure. The whitelist in
    :func:`core.profiles.sanitise_profile` refuses anything else, so a claimant
    name cannot reach disk through this path.
    """
    from core.profiles import profile_from_mapping, save_profile

    mapping = mapping or result.mapping
    document = result.document
    profile = profile_from_mapping(
        document.profile_fingerprint or mapping.fingerprint,
        mapping.headers,
        dict(mapping.fields),
        carrier=document.carrier,
        date_order=result.date_order.order if result.date_order.confident else None,
        number_locale=result.locale.locale if result.locale.confident else None,
        recovery_convention=(
            "credit" if result.recovery_sign.should_negate else None
        ),
        currency=document.currency,
        line_of_business=(
            document.line_of_business.value if document.line_of_business else None
        ),
        confirmed_by_human=confirmed_by_human,
    )
    profile.times_used = (result.profile.times_used + 1) if result.profile else 1
    save_profile(profile, profiles_dir)
    return profile


def sample_rows(tables: Sequence[RawTable], count: int = 3) -> list[list[str]]:
    """The first few data rows, for the mapping screen and the LLM prompt."""
    rows: list[list[str]] = []
    for table in tables:
        for row in table.rows:
            rows.append(list(row.cells))
            if len(rows) >= count:
                return rows
    return rows


# --------------------------------------------------------------------------
# Stage 6 — review adapters
#
# The review table is a presentation of the document, and turning one into the
# other is logic, so it lives here rather than in the Streamlit layer.
# --------------------------------------------------------------------------

#: Provenance columns carried through the editor so an edited row can still be
#: traced back to the page it came from. Shown read-only.
PROVENANCE_COLUMNS = ("_page", "_row", "_method")

#: The claim's durable identity, carried through the editable table and never
#: shown in it. See :attr:`core.schema.Claim.row_id`.
ROW_ID_COLUMN = "_id"

REVIEW_COLUMNS: tuple[str, ...] = (
    "claim_number",
    "date_of_loss",
    "date_reported",
    "claim_status",
    "claimant_name",
    "loss_description",
    "cause_of_loss",
    "paid_indemnity",
    "paid_medical",
    "paid_expense",
    "paid_total",
    "reserve_indemnity",
    "reserve_medical",
    "reserve_expense",
    "reserve_total",
    "recovery_total",
    "incurred_total",
    "litigation_flag",
)


def review_columns(document: LossRunDocument) -> list[str]:
    """Show the columns this document actually has, plus the required ones."""
    always = {"claim_number", "date_of_loss", "incurred_total"}
    present = []
    for name in REVIEW_COLUMNS:
        if name in always:
            present.append(name)
            continue
        if any(getattr(claim, name, None) is not None for claim in document.claims):
            present.append(name)
    return present


def to_records(
    document: LossRunDocument, columns: Sequence[str] | None = None
) -> list[dict[str, Any]]:
    """The claims as plain rows for the editable table."""
    names = list(columns or review_columns(document))
    records = []
    for claim in document.claims:
        row: dict[str, Any] = {}
        for name in names:
            value = getattr(claim, name, None)
            if isinstance(value, Decimal):
                # Keep the exact decimal spelling through the UI boundary.
                # Streamlit numbers are binary floats and cannot preserve
                # either arbitrary precision or the carrier's written scale.
                row[name] = format(value, "f")
            elif hasattr(value, "value"):
                row[name] = value.value
            else:
                row[name] = value
        row["_page"] = claim.source_page
        row["_row"] = claim.source_row
        row["_method"] = claim.source_method.value
        # Which row this is, carried through the table so an edit can be put
        # back on the claim it came from. Not displayed: it answers a question
        # the reviewer is not asking, and every other column on the row is one
        # they may change, the claim number included.
        row[ROW_ID_COLUMN] = claim.row_id
        records.append(row)
    return records


def _is_missing(value: Any) -> bool:
    """True for every flavour of "no value" a table round trip can produce.

    pandas, numpy and pyarrow each have their own null, and none of them is
    ``None``. Any of them reaching ``Decimal`` becomes ``Decimal("NaN")``,
    which the schema rejects — so they are all caught in one place.
    """
    if value is None:
        return True
    if isinstance(value, Decimal):
        return not value.is_finite()
    if isinstance(value, float):
        return value != value or value in (float("inf"), float("-inf"))
    try:
        # numpy.nan, pandas.NA and pyarrow nulls all answer this.
        import pandas as pd

        result = pd.isna(value)
    except (ImportError, ValueError, TypeError):
        return False
    return bool(result) if isinstance(result, bool) or getattr(result, "ndim", 0) == 0 else False


def _coerce(
    field_name: str, value: Any, locale: str | None, date_order: str | None
) -> tuple[Any, NullReason | None]:
    """Turn one edited cell back into a canonical value."""
    # Status has no null: an unset status is UNKNOWN, not a missing value.
    if field_name == "claim_status":
        return parse_status(value), None

    # An empty numeric cell arrives as NaN once the table has been through
    # pandas. NaN is a null, not a zero, and it must never reach a Decimal.
    if _is_missing(value):
        return None, NullReason.EMPTY
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, NullReason.EMPTY

    if field_name in MONEY_FIELDS:
        if isinstance(value, Decimal):
            return (value, None) if value.is_finite() else (None, NullReason.EMPTY)
        if isinstance(value, bool):
            return None, NullReason.UNPARSEABLE
        if isinstance(value, (int, float)):
            # str() first: Decimal(0.1) is not Decimal("0.1").
            amount = Decimal(str(value))
            return (amount, None) if amount.is_finite() else (None, NullReason.EMPTY)
        parsed = parse_money(str(value), locale)
        return parsed.value, parsed.reason
    if field_name in DATE_FIELDS:
        if isinstance(value, date):
            return value, None
        parsed_date = parse_date(str(value), date_order)
        return parsed_date.value, parsed_date.reason
    if field_name == "litigation_flag":
        return parse_bool(value), None
    return parse_text(value), None


def _as_text(value: Any) -> str:
    """A value as the audit trail should keep it: readable, and never a float."""
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return f"{value:f}"
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _audit_value(claim: Claim, field_name: str) -> str:
    """A value and, when null, the reason needed to reconstruct its meaning."""
    value = getattr(claim, field_name, None)
    if value is not None:
        return _as_text(value)
    reason = claim.field_issues.get(field_name, NullReason.EMPTY)
    return f"null ({reason.value})"


def apply_edits(
    document: LossRunDocument,
    records: Sequence[dict[str, Any]],
    *,
    locale: str | None = None,
    date_order: str | None = None,
    audit: bool = True,
) -> LossRunDocument:
    """Rebuild the document from the edited table.

    A cell the human changed becomes ``source_method="manual"`` with full
    confidence and no outstanding issue — they are the authority, and the audit
    trail records that they were.

    Every change made here is written to the review log, because this table is
    the main editing surface and not a side door: a carrier's figure replaced
    in the grid is the same act as one corrected against a finding, and an
    export that shows the second and not the first is not an audit trail. A
    removed row is refused: a short audit summary cannot replace its complete
    carrier evidence, and R-05 only notices when a printed count exists.

    ``audit=False`` is for :func:`resolve_finding`, which records the same
    change against the finding it was made about and would otherwise log it
    twice.
    """
    locale = locale or (document.locale_hint if document.locale_confident else None)
    date_order = date_order or (
        document.date_order if document.date_order_confident else None
    )
    # The row a record came from, found by the one thing about it a reviewer
    # cannot edit. Matching on the claim number instead loses the original
    # whenever the number is what changed -- which is the ordinary remedy for
    # a duplicate -- and with it the printed cell text, the region on the page
    # and the reasons its nulls are null.
    by_identity = {claim.row_id: claim for claim in document.claims if claim.row_id}
    if any(ROW_ID_COLUMN in record for record in records):
        returned_ids = {
            str(record.get(ROW_ID_COLUMN) or "") for record in records
            if record.get(ROW_ID_COLUMN)
        }
        removed = [claim for row_id, claim in by_identity.items() if row_id not in returned_ids]
        if removed:
            named = ", ".join(f"{claim.claim_number} ({claim.where()})" for claim in removed)
            raise ValueError(
                "Claim rows cannot be deleted because that would discard carrier "
                f"evidence. Restore the row before continuing: {named}."
            )
    originals = {
        (claim.source_page, claim.source_row, claim.claim_number): claim
        for claim in document.claims
    }
    by_position = list(document.claims)

    claims: list[Claim] = []
    for index, record in enumerate(records):
        claim_number = clean_text(record.get("claim_number", ""))
        if not claim_number:
            continue  # a row emptied out is a row deleted

        original = by_identity.get(str(record.get(ROW_ID_COLUMN) or ""))
        # Records built by hand -- a test, a caller predating the identity --
        # still match the way they always did.
        if original is None:
            key = (record.get("_page"), record.get("_row"), claim_number)
            original = originals.get(key)
        if original is None and index < len(by_position):
            candidate = by_position[index]
            if candidate.claim_number == claim_number:
                original = candidate

        issues = dict(original.field_issues) if original else {}
        confidence = dict(original.field_confidence) if original else {}
        raw_cells = dict(original.raw_cells) if original else {}
        edited = False
        touched: list[str] = []
        originals_read = dict(original.original_values) if original else {}
        issues_read = dict(original.original_issues) if original else {}
        fields: dict[str, Any] = original.model_dump(exclude={
            "claim_number", "row_id", "source_page", "source_row", "source_bbox",
            "source_lines", "edited_fields", "original_values", "original_issues",
            "read_method", "source_method", "field_issues", "field_confidence",
            "raw_cells", "currency",
        }) if original else {}

        for name, value in record.items():
            if name in PROVENANCE_COLUMNS or name == "claim_number":
                continue
            if name not in REVIEW_COLUMNS:
                continue
            previous = getattr(original, name, None) if original else None
            if (isinstance(previous, Decimal) and isinstance(value, str)
                    and value == format(previous, "f")):
                # Canonical text already on the screen is not fresh locale
                # input. In an EU document, 1.234 may otherwise become 1234.
                coerced, reason = previous, None
            else:
                coerced, reason = _coerce(name, value, locale, date_order)
            # A figure the reviewer did not touch keeps the scale it was read
            # at. The table round-trips money through float, so 8,400.00 comes
            # back as 8400.0 -- the same amount, spelled with less of what the
            # carrier printed, on a row nobody edited.
            fields[name] = previous if coerced == previous else coerced
            explicit_null_change = (
                coerced is None and previous is None and reason is not None
                and isinstance(value, str) and bool(value.strip())
                and reason != issues.get(name, NullReason.EMPTY)
            )
            if coerced != previous or explicit_null_change:
                edited = True
                touched.append(name)
                # What the extractor read, kept before the correction lands on
                # top of it. Only the first correction records one: a second
                # edit of the same cell is a person changing their own answer,
                # not the document's.
                if original is not None and name not in originals_read:
                    originals_read[name] = _as_text(previous)
                    # And why it was null, when it was. The reason is the
                    # carrier's evidence about that cell just as much as the
                    # text is, and popping it below would be the only copy.
                    was_unreadable = original.field_issues.get(name)
                    if was_unreadable is not None:
                        issues_read[name] = was_unreadable
                confidence[name] = 1.0
                issues.pop(name, None)
                if coerced is None and reason and reason is not NullReason.EMPTY:
                    issues[name] = reason
            elif original is not None and name in original.field_issues:
                issues[name] = original.field_issues[name]

        if original is not None and claim_number != original.claim_number:
            # A renumbered claim is an edited field like any other, and the
            # loop above skips it: the claim number is the row's label, so it
            # is read before the fields rather than with them. Left out, the
            # one change that alters what a row calls itself would be the one
            # change nothing recorded.
            edited = True
            touched.append("claim_number")
            originals_read.setdefault("claim_number", original.claim_number)

        claims.append(
            Claim(
                claim_number=claim_number,
                row_id=original.row_id if original else "",
                source_page=int(record.get("_page") or (original.source_page if original else 1)),
                source_row=record.get("_row") if record.get("_row") is not None else (original.source_row if original else None),
                source_bbox=original.source_bbox if original else None,
                source_lines=list(original.source_lines) if original else [],
                edited_fields=sorted(
                    set(touched) | set(original.edited_fields if original else [])
                ),
                original_values=originals_read,
                original_issues=issues_read,
                # How the row was read, kept beside how it now reads. Without
                # it, correcting one cell would leave the other nine unable to
                # say whether they came off a text layer or out of a scan.
                read_method=(
                    original.read_method or original.source_method
                    if original is not None
                    else None
                ),
                source_method=(
                    SourceMethod.MANUAL
                    if edited or original is None
                    else original.source_method
                ),
                field_issues=issues,
                field_confidence=confidence,
                raw_cells=raw_cells,
                currency=original.currency if original else None,
                **fields,
            )
        )

    retained_ids = {claim.row_id for claim in claims if claim.row_id}
    if any(claim.row_id not in retained_ids for claim in document.claims):
        raise ValueError(
            "Claim rows cannot be deleted or have their claim number cleared; "
            "restore the row so its carrier evidence remains available."
        )
    updated = document.model_copy(deep=True)
    updated.claims = claims
    if audit:
        _record_edits(updated, document.claims, claims)
    return updated


def _record_edits(
    document: LossRunDocument,
    before: Sequence[Claim],
    after: Sequence[Claim],
) -> None:
    """Write what the table changed into the review log.

    Keyed on the row rather than on any finding: nobody raised these, so there
    is nothing for a later run to re-raise or retire. They are here to answer
    "who replaced this figure, and what was it before".
    """
    was = {claim.row_id: claim for claim in before if claim.row_id}
    now = {claim.row_id: claim for claim in after if claim.row_id}

    for row_id, gone in was.items():
        if row_id in now:
            continue
        document.review_log.record(
            Resolution(
                key=f"deleted|{row_id}",
                action=ReviewAction.DELETED,
                row_id=row_id,
                where=gone.where(),
                claim_number=gone.claim_number,
                message=(
                    f"Claim {gone.claim_number} was removed on the review screen, "
                    f"read from {gone.where()}."
                ),
                before=_deleted_summary(gone),
                after=None,
            )
        )

    for row_id, current in now.items():
        previous = was.get(row_id)
        if previous is None:
            document.review_log.record(
                Resolution(
                    key=f"added|{row_id}",
                    action=ReviewAction.EDITED,
                    row_id=row_id,
                    where=current.where(),
                    claim_number=current.claim_number,
                    message=(
                        f"Claim {current.claim_number} was added on the review "
                        f"screen. The document is not its source."
                    ),
                    before=None,
                    after=current.claim_number,
                )
            )
            continue
        for field_name in current.edited_fields:
            was_value = _audit_value(previous, field_name)
            now_value = _audit_value(current, field_name)
            if was_value == now_value:
                continue
            document.review_log.record(
                Resolution(
                    key=f"edited|{row_id}|{field_name}",
                    action=ReviewAction.EDITED,
                    row_id=row_id,
                    where=current.where(),
                    claim_number=current.claim_number,
                    field=field_name,
                    message=(
                        f"{field_name.replace('_', ' ').capitalize()} was changed "
                        f"in the claims table, on the row read from "
                        f"{current.where()}."
                    ),
                    before=was_value,
                    after=now_value,
                )
            )


def _deleted_summary(claim: Claim) -> str:
    """What a removed row held, in one line, so the loss is visible."""
    parts = [f"claim {claim.claim_number}"]
    if claim.date_of_loss:
        parts.append(f"loss {claim.date_of_loss.isoformat()}")
    for field_name in ("paid_total", "reserve_total", "incurred_total"):
        value = getattr(claim, field_name, None)
        if value is not None:
            parts.append(f"{field_name.replace('_', ' ')} {value}")
    return ", ".join(parts)
