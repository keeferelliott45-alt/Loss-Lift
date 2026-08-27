"""Stage orchestration — ingest through reconcile.

``app.py`` calls this and nothing else, so every stage stays runnable and
testable without Streamlit.  Nothing here talks to the UI, and nothing in the
UI does any of this work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Sequence

from core import extract_digital
from core.classify import DocumentClassification, classify_pdf
from core.extract_digital import DigitalExtraction, DocumentMetadata
from core.ingest import IngestedFile, ingest, ingest_path
from core.normalize import (
    DateOrderInference,
    LocaleInference,
    clean_text,
    infer_date_order,
    infer_locale,
    parse_bool,
    parse_date,
    parse_money,
    parse_status,
    parse_text,
)
from core.profiles import (
    CarrierProfile,
    FieldGuess,
    fingerprint,
    load_profile,
    map_headers,
    page_top_text,
    resolve_columns,
)
from core.reconcile import ReconcileConfig, reconcile
from core.schema import (
    DATE_FIELDS,
    MONEY_FIELDS,
    Claim,
    ClaimStatus,
    ExtractionMethod,
    LineOfBusiness,
    LossRunDocument,
    NullReason,
    RawRow,
    RawTable,
    ReconciliationResult,
    SourceMethod,
)


@dataclass
class ColumnMapping:
    """Which canonical field each printed column carries."""

    headers: list[str]
    fields: dict[int, str | None]
    source: str = "heuristic"  # profile | heuristic | llm | manual
    fingerprint: str = ""

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
    tables: list[RawTable] = field(default_factory=list)
    profile: CarrierProfile | None = None
    warnings: list[str] = field(default_factory=list)

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
        use_llm=use_llm,
        client=llm_client,
    )
    return ColumnMapping(
        headers=list(headers),
        fields={index: guess.field for index, guess in guesses.items()},
        source=source,
        fingerprint=fingerprint_value,
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
        elif field_name == "litigation_flag":
            fields["litigation_flag"] = parse_bool(raw)
            confidence["litigation_flag"] = confidence_cap
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
        source_method=source_method,
        field_issues=issues,
        field_confidence=confidence,
        raw_cells=raw_cells,
        currency=sorted(currencies)[0] if len(currencies) == 1 else None,
        **fields,
    )


def _continuation_text(row: RawRow, mapping: ColumnMapping) -> str | None:
    """Text from a wrapped row that belongs to the claim above it."""
    values = _row_values(row, mapping)
    if clean_text(values.get("claim_number", "")):
        return None
    if any(values.get(name, "").strip() for name in MONEY_FIELDS):
        return None
    text = clean_text(
        " ".join(
            values.get(name, "")
            for name in ("loss_description", "claimant_name", "cause_of_loss")
        )
    )
    return text or None


def build_claims(
    tables: Sequence[RawTable],
    mapping: ColumnMapping,
    locale: str | None,
    date_order: str | None,
    *,
    dash_means_zero: bool = False,
    source_method: SourceMethod = SourceMethod.DIGITAL,
    confidence_cap: float = 1.0,
) -> tuple[list[Claim], list[str]]:
    """Normalise every row of every page, folding wrapped lines into their claim."""
    claims: list[Claim] = []
    warnings: list[str] = []

    for table in tables:
        table_mapping = mapping
        if table.headers and table.headers != mapping.headers:
            table_mapping = build_mapping(table.headers, None, mapping.fingerprint)

        for row in table.rows:
            claim = build_claim(
                row, table_mapping, locale, date_order,
                dash_means_zero=dash_means_zero,
                source_method=source_method,
                confidence_cap=confidence_cap,
            )
            if claim is not None:
                claims.append(claim)
                continue

            extra = _continuation_text(row, table_mapping)
            if extra and claims:
                previous = claims[-1]
                previous.loss_description = clean_text(
                    f"{previous.loss_description or ''} {extra}"
                )
            elif not row.is_blank():
                preview = " ".join(cell for cell in row.cells if cell)[:60]
                warnings.append(
                    f"Page {row.page}: skipped a row with no claim number "
                    f"({preview})."
                )
    return claims, warnings


def collect_printed_totals(
    tables: Sequence[RawTable], mapping: ColumnMapping, locale: str | None
) -> dict[str, Decimal | None]:
    """Read the footer totals row — the numbers R-04 checks against."""
    best: dict[str, Decimal | None] = {}
    best_count = 0
    for table in tables:
        for row in table.total_rows:
            totals: dict[str, Decimal | None] = {}
            for index, field_name in mapping.fields.items():
                if field_name not in MONEY_FIELDS:
                    continue
                cell = row.cell(index).strip()
                if not cell:
                    continue
                parsed = parse_money(cell, locale)
                if parsed.value is not None:
                    totals[field_name] = parsed.value
            if len(totals) > best_count:
                best, best_count = totals, len(totals)
    return best


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


def _date_tokens(
    tables: Sequence[RawTable], mapping: ColumnMapping, metadata: DocumentMetadata
) -> list[str]:
    indexes = [i for i, name in mapping.fields.items() if name in DATE_FIELDS]
    tokens: list[str] = []
    for table in tables:
        for row in table.rows:
            tokens.extend(row.cell(index) for index in indexes)
    tokens.extend(
        text
        for text in (
            metadata.valuation_date_text,
            metadata.policy_period_start_text,
            metadata.policy_period_end_text,
        )
        if text
    )
    return [token for token in tokens if token.strip()]


def _line_of_business(text: str | None) -> LineOfBusiness | None:
    if not text:
        return None
    token = clean_text(text).upper().replace(" ", "")
    for member in LineOfBusiness:
        if token.startswith(member.value):
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
    extraction = extract_digital.extract_pdf(ingested.path, pages=digital_pages or None)
    tables = list(extraction.tables)
    metadata = extraction.metadata
    warnings: list[str] = []

    scanned_pages = classification.scanned_pages
    vision_tables: list[RawTable] = []
    if scanned_pages and use_vision:
        from core.extract_vision import VisionUnavailable, extract_scanned_pages

        extractor = vision_extractor or extract_scanned_pages
        try:
            vision_tables = list(extractor(ingested.path, scanned_pages))
            tables.extend(vision_tables)
        except VisionUnavailable as error:
            warnings.append(str(error))
    elif scanned_pages:
        warnings.append(
            f"{len(scanned_pages)} page(s) are scans and were not read. "
            f"Turn on vision extraction to include them."
        )

    # Fingerprint the letterhead plus the header labels.
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
    date_inference = infer_date_order(
        _date_tokens(tables, mapping, metadata), locale_inference.for_parsing
    )
    if profile and profile.date_order:
        date_inference = DateOrderInference(profile.date_order, True, "carrier profile")

    locale = locale_inference.for_parsing
    date_order = date_inference.for_parsing
    dash_means_zero = bool(profile and profile.dash_means_zero)

    digital_tables = [table for table in tables if table.strategy != "vision"]
    claims, row_warnings = build_claims(
        digital_tables, mapping, locale, date_order, dash_means_zero=dash_means_zero
    )
    warnings.extend(row_warnings)

    if vision_tables:
        vision_claims, vision_warnings = build_claims(
            vision_tables, mapping, locale, date_order,
            dash_means_zero=dash_means_zero,
            source_method=SourceMethod.VISION,
            confidence_cap=0.85,
        )
        claims.extend(vision_claims)
        warnings.extend(vision_warnings)
        claims.sort(key=lambda claim: (claim.source_page, claim.source_row or 0))

    # Document-level facts.
    document_issues: dict[str, NullReason] = {}
    valuation = parse_date(metadata.valuation_date_text or "", date_order)
    if valuation.value is None and metadata.valuation_date_text:
        document_issues["valuation_date"] = valuation.reason or NullReason.UNPARSEABLE
    period_start = parse_date(metadata.policy_period_start_text or "", date_order)
    period_end = parse_date(metadata.policy_period_end_text or "", date_order)

    currency = (metadata.currency or "USD").strip().upper()[:3] or "USD"
    currencies_seen = sorted(
        {claim.currency for claim in claims if claim.currency} | {currency}
    )

    document = LossRunDocument(
        document_id=ingested.document_id,
        source_filename=ingested.source_filename,
        file_sha256=ingested.sha256,
        carrier=metadata.carrier,
        named_insured=metadata.named_insured,
        policy_number=metadata.policy_number,
        policy_period_start=period_start.value,
        policy_period_end=period_end.value,
        line_of_business=_line_of_business(metadata.line_of_business),
        valuation_date=valuation.value,
        currency=currency if len(currency) == 3 and currency.isalpha() else "USD",
        locale_hint=locale_inference.locale,
        locale_confident=locale_inference.confident,
        date_order=date_inference.order,
        date_order_confident=date_inference.confident,
        page_count=classification.page_count,
        extraction_method=classification.extraction_method,
        scanned_pages=scanned_pages,
        printed_totals=collect_printed_totals(tables, mapping, locale),
        printed_claim_count=metadata.printed_claim_count,
        claims=claims,
        currencies_seen=currencies_seen,
        document_issues=document_issues,
        profile_fingerprint=fingerprint_value,
        profile_name=profile.profile_name if profile else None,
    )

    config = reconcile_config or _config_for(profile)
    return ExtractionResult(
        document=document,
        reconciliation=reconcile(document, config),
        mapping=mapping,
        classification=classification,
        locale=locale_inference,
        date_order=date_inference,
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
