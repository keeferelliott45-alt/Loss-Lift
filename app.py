"""LossLift — Streamlit entry point.

Routing and presentation only. Every decision about a document is made in
``core`` so that the whole pipeline stays runnable and testable without
Streamlit (spec section 7).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from core import export as export_module
from core.ingest import IngestError, discard, ingest
from core.pipeline import (
    ColumnMapping,
    ExtractionResult,
    PROVENANCE_COLUMNS,
    apply_edits,
    rerun_reconciliation,
    review_columns,
    sample_rows,
    save_confirmed_mapping,
    to_records,
)
from core.profiles import list_profiles, llm_enabled
from core.schema import (
    CANONICAL_FIELDS,
    DATE_FIELDS,
    MONEY_FIELDS,
    DocumentStatus,
    Severity,
)

st.set_page_config(page_title="LossLift", page_icon="📄", layout="wide")

UPLOAD, MAPPING, REVIEW, EXPORT = "Upload", "Map columns", "Review", "Export"

_FIELD_LABELS = {name: export_module.COLUMN_TITLES.get(name, name) for name in CANONICAL_FIELDS}


# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------


def _state() -> dict[str, Any]:
    st.session_state.setdefault("documents", {})   # document_id -> ExtractionResult
    st.session_state.setdefault("order", [])       # document_id, upload order
    st.session_state.setdefault("active", None)
    st.session_state.setdefault("screen", UPLOAD)
    st.session_state.setdefault("errors", [])
    st.session_state.setdefault("staged", {})    # document_id -> IngestedFile
    return st.session_state


def _active_result() -> ExtractionResult | None:
    state = _state()
    return state["documents"].get(state["active"])


def _go(screen: str) -> None:
    _state()["screen"] = screen


def _store(result: ExtractionResult) -> None:
    state = _state()
    document_id = result.document.document_id
    if document_id not in state["documents"]:
        state["order"].append(document_id)
    state["documents"][document_id] = result
    state["active"] = document_id


# --------------------------------------------------------------------------
# Shared pieces
# --------------------------------------------------------------------------


def _badge(result: ExtractionResult) -> None:
    """The green/amber badge — the emotional core of the screen."""
    document, reconciliation = result.document, result.reconciliation
    clean = reconciliation.status is DocumentStatus.CLEAN

    left, middle, right, far = st.columns([2.2, 1, 1, 1.2])
    with left:
        if clean:
            st.success("**Reconciled** — every check passed.")
        else:
            errors = len(reconciliation.errors)
            st.warning(
                f"**Needs review** — {errors} check{'s' if errors != 1 else ''} "
                f"did not tie."
            )
    middle.metric(
        "Valuation date",
        document.valuation_date.isoformat() if document.valuation_date else "missing",
    )
    right.metric("Claims", len(document.claims))
    total = document.column_total("incurred_total")
    far.metric("Total incurred", f"{total:,.2f}")


def _findings_table(result: ExtractionResult) -> None:
    findings = result.reconciliation.findings
    if not findings:
        st.caption("No exceptions. Every check passed.")
        return

    icons = {Severity.ERROR: "🔴", Severity.WARN: "🟠", Severity.INFO: "🔵"}
    rows = [
        {
            "": icons[finding.severity],
            "Rule": finding.rule_id,
            "Claim": finding.claim_number or "—",
            "Field": _FIELD_LABELS.get(finding.field or "", finding.field or "—"),
            "What happened": finding.message,
            "Expected": _money(finding.expected),
            "Actual": _money(finding.actual),
            "Difference": _money(finding.delta),
        }
        for finding in findings
    ]
    st.dataframe(rows, hide_index=True, width="stretch")


def _money(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, Decimal):
        return f"{value:,.2f}"
    return str(value)


def _document_facts(result: ExtractionResult) -> None:
    document = result.document
    left, right = st.columns(2)
    left.markdown(
        f"""
**Carrier** {document.carrier or "not found"}
**Named insured** {document.named_insured or "not found"}
**Policy number** {document.policy_number or "not found"}
**Policy period** {document.policy_period_start or "?"} to {document.policy_period_end or "?"}
"""
    )
    right.markdown(
        f"""
**Pages** {document.page_count} ({document.extraction_method.value})
**Numbers read as** {"European (1.234,56)" if document.locale_hint == "eu" else "US (1,234.56)"}{"" if document.locale_confident else " — assumed"}
**Dates read as** {document.date_order or "unknown"}{"" if document.date_order_confident else " — assumed"}
**Carrier profile** {document.profile_name or "none saved yet"}
"""
    )


# --------------------------------------------------------------------------
# Screen: upload
# --------------------------------------------------------------------------


def screen_upload() -> None:
    st.subheader("Upload loss runs")
    st.caption(
        "Drop in carrier PDFs. Files are held in memory for this session and "
        "deleted after you export."
    )

    uploads = st.file_uploader(
        "Choose PDF files",
        type=["pdf"],
        accept_multiple_files=True,
        label_visibility="collapsed",
    )

    if uploads and st.button("Extract", type="primary"):
        _extract_uploads(uploads)

    for message in _state()["errors"]:
        st.error(message)

    state = _state()
    if not state["order"]:
        st.info("No documents yet. Upload a loss run to get started.")
        _profile_library()
        return

    st.divider()
    st.markdown("**Documents in this session**")
    for document_id in state["order"]:
        result = state["documents"][document_id]
        document = result.document
        clean = result.reconciliation.status is DocumentStatus.CLEAN
        name, status, claims, action = st.columns([3, 1.6, 1, 1])
        name.write(f"📄 {document.source_filename}")
        if result.needs_mapping:
            status.write("🟡 Needs column mapping")
        elif clean:
            status.write("🟢 Reconciled")
        else:
            status.write("🟠 Needs review")
        claims.write(f"{len(document.claims)} claims")
        if action.button("Open", key=f"open-{document_id}"):
            state["active"] = document_id
            _go(MAPPING if result.needs_mapping else REVIEW)
            st.rerun()

    _profile_library()


def _extract_uploads(uploads: list[Any]) -> None:
    from core.pipeline import run_pipeline

    state = _state()
    state["errors"] = []
    progress = st.progress(0.0, text="Reading documents")

    for index, upload in enumerate(uploads, start=1):
        try:
            staged = ingest(upload.getvalue(), upload.name)
            result = run_pipeline(staged, use_llm=llm_enabled())
            _store(result)
            state["staged"][result.document.document_id] = staged
            for warning in result.warnings:
                state["errors"].append(f"{upload.name}: {warning}")
        except IngestError as error:
            state["errors"].append(str(error))
        except Exception as error:  # noqa: BLE001 - surface, never crash the page
            state["errors"].append(
                f"{upload.name} could not be read ({error}). If it is a scan, "
                f"turn on vision extraction; otherwise send us the file."
            )
        progress.progress(index / len(uploads), text=f"Read {upload.name}")

    progress.empty()
    result = _active_result()
    if result is not None:
        _go(MAPPING if result.needs_mapping else REVIEW)
        st.rerun()


def _profile_library() -> None:
    profiles = list_profiles()
    if not profiles:
        return
    with st.expander(f"Saved carrier formats ({len(profiles)})"):
        st.dataframe(
            [
                {
                    "Carrier": profile.carrier or "Unnamed",
                    "Columns mapped": len(profile.column_map),
                    "Numbers": profile.number_locale or "inferred",
                    "Dates": profile.date_order or "inferred",
                    "Confirmed": "yes" if profile.confirmed_by_human else "no",
                }
                for profile in profiles
            ],
            hide_index=True,
            width="stretch",
        )


# --------------------------------------------------------------------------
# Screen: mapping
# --------------------------------------------------------------------------


def screen_mapping() -> None:
    result = _active_result()
    if result is None:
        st.info("Upload a document first.")
        return

    st.subheader("Map columns")
    st.caption(
        f"LossLift could not place every column in "
        f"{result.document.source_filename}. Tell it what each one is; it will "
        f"remember this format for every future document from this carrier."
    )

    headers = result.mapping.headers
    if not headers:
        st.error(
            "No table was found in this document. If it is a scan, turn on "
            "vision extraction on the upload screen."
        )
        return

    unused = "— not used —"
    labels = {unused: None} | {_FIELD_LABELS[name]: name for name in CANONICAL_FIELDS}
    options = list(labels)
    by_field = {name: label for label, name in labels.items() if name}
    chosen: dict[int, str | None] = {}

    with st.form("mapping"):
        for index, header in enumerate(headers):
            current = result.mapping.fields.get(index)
            current_label = by_field.get(current, unused)
            left, right = st.columns([1, 1])
            left.markdown(f"**{header or f'Column {index + 1}'}**")
            selection = right.selectbox(
                f"Field for {header or index}",
                options,
                index=options.index(current_label),
                key=f"map-{index}",
                label_visibility="collapsed",
            )
            chosen[index] = labels[selection]

        st.markdown("**Sample rows from this document**")
        samples = sample_rows(result.tables, 3)
        if samples:
            st.dataframe(
                [dict(zip(headers, row)) for row in samples],
                hide_index=True,
                width="stretch",
            )

        submitted = st.form_submit_button("Save mapping and continue", type="primary")

    if submitted:
        _apply_mapping(result, chosen)


def _apply_mapping(result: ExtractionResult, chosen: dict[int, str | None]) -> None:
    from core.pipeline import run_pipeline

    mapping = ColumnMapping(
        headers=result.mapping.headers,
        fields=chosen,
        source="manual",
        fingerprint=result.mapping.fingerprint,
    )
    if not mapping.is_usable():
        st.error(
            "Pick which column holds the claim number and at least one amount, "
            "then save again."
        )
        return

    staged = _state()["staged"].get(result.document.document_id)
    if staged is None or not staged.exists:
        st.error(
            "The uploaded file is no longer in this session. Upload it again "
            "and the saved mapping will be applied."
        )
        return

    try:
        updated = run_pipeline(staged, mapping_override=mapping, use_vision=True)
    except Exception as error:  # noqa: BLE001
        st.error(f"The document could not be re-read with that mapping ({error}).")
        return

    save_confirmed_mapping(updated, mapping, confirmed_by_human=True)
    _store(updated)
    st.success(
        f"Saved. Documents from {updated.document.carrier or 'this carrier'} in "
        f"this format will map themselves from now on."
    )
    _go(REVIEW)
    st.rerun()


# --------------------------------------------------------------------------
# Screen: review
# --------------------------------------------------------------------------


def screen_review() -> None:
    result = _active_result()
    if result is None:
        st.info("Upload a document first.")
        return

    st.subheader(result.document.source_filename)
    _badge(result)
    _document_facts(result)

    findings = result.reconciliation.findings
    with st.expander(
        f"Exceptions ({len(findings)})" if findings else "Exceptions (none)",
        expanded=bool(findings),
    ):
        _findings_table(result)

    st.markdown("**Claims**")
    st.caption(
        "Edit any cell. The checks re-run as soon as you do. Cells LossLift "
        "could not read are marked in the exceptions above."
    )

    columns = review_columns(result.document)
    records = to_records(result.document, columns)
    # A DataFrame so an empty amount renders as an empty cell rather than the
    # word "None": a blank and a zero must never look alike on this screen.
    edited = st.data_editor(
        pd.DataFrame(records, columns=list(columns) + list(PROVENANCE_COLUMNS)),
        key=f"editor-{result.document.document_id}",
        num_rows="dynamic",
        width="stretch",
        hide_index=True,
        column_config=_column_config(columns),
        column_order=list(columns) + list(PROVENANCE_COLUMNS),
    )

    edited_records = edited.to_dict("records")
    if edited_records != records:
        updated = apply_edits(result.document, edited_records)
        result.document = updated
        result.reconciliation = rerun_reconciliation(updated)
        st.rerun()

    left, right = st.columns([1, 4])
    if left.button("Export", type="primary"):
        _go(EXPORT)
        st.rerun()


def _column_config(columns: list[str]) -> dict[str, Any]:
    config: dict[str, Any] = {}
    for name in columns:
        label = _FIELD_LABELS.get(name, name)
        if name in MONEY_FIELDS:
            config[name] = st.column_config.NumberColumn(label, format="%.2f")
        elif name in DATE_FIELDS:
            config[name] = st.column_config.DateColumn(label, format="YYYY-MM-DD")
        elif name == "claim_status":
            config[name] = st.column_config.SelectboxColumn(
                label, options=["OPEN", "CLOSED", "REOPENED", "UNKNOWN"]
            )
        elif name == "litigation_flag":
            config[name] = st.column_config.CheckboxColumn(label)
        else:
            config[name] = st.column_config.TextColumn(label)

    config["_page"] = st.column_config.NumberColumn("Page", disabled=True, width="small")
    config["_row"] = st.column_config.NumberColumn("Line", disabled=True, width="small")
    config["_method"] = st.column_config.TextColumn(
        "Read by", disabled=True, width="small"
    )
    return config


# --------------------------------------------------------------------------
# Screen: export
# --------------------------------------------------------------------------


def screen_export() -> None:
    result = _active_result()
    if result is None:
        st.info("Upload a document first.")
        return

    st.subheader("Export")
    _badge(result)

    left, right = st.columns(2)
    template = left.selectbox(
        "Column order", list(export_module.EXPORT_TEMPLATES),
        index=list(export_module.EXPORT_TEMPLATES).index(export_module.DEFAULT_TEMPLATE),
    )
    redact = right.toggle(
        "Remove claimant names and loss descriptions",
        help="Leave personal data out of the spreadsheet you send on.",
    )
    provenance = right.toggle("Include page and extraction columns", value=True)

    columns = export_module.resolve_columns(
        template, redact=redact, include_provenance=provenance
    )
    st.caption("Columns: " + ", ".join(export_module.COLUMN_TITLES.get(c, c) for c in columns))

    if result.reconciliation.errors:
        st.warning(
            f"{len(result.reconciliation.errors)} check(s) still do not tie. The "
            f"workbook exports either way — the Exceptions sheet lists them."
        )

    payload = export_module.to_bytes(
        result.document,
        result.reconciliation,
        template=template,
        redact=redact,
        include_provenance=provenance,
    )
    st.download_button(
        "Download Excel",
        data=payload,
        file_name=export_module.suggested_filename(result.document),
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
    )
    st.caption(
        "Three sheets: Claims, Exceptions, and Source Info with the file hash "
        "and valuation date."
    )

    st.divider()
    if st.button("Delete the uploaded file now"):
        staged = _state()["staged"].pop(result.document.document_id, None)
        if staged is not None:
            discard(staged)
        st.success(
            "Deleted. The extracted table stays in this session until you close "
            "the tab."
        )


# --------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------


SCREENS = {
    UPLOAD: screen_upload,
    MAPPING: screen_mapping,
    REVIEW: screen_review,
    EXPORT: screen_export,
}


def main() -> None:
    state = _state()
    st.title("LossLift")
    st.caption("Carrier loss runs in, reconciled spreadsheets out.")

    with st.sidebar:
        st.markdown("### Steps")
        for name in SCREENS:
            if st.button(
                name,
                key=f"nav-{name}",
                width="stretch",
                type="primary" if state["screen"] == name else "secondary",
            ):
                _go(name)
                st.rerun()
        st.divider()
        st.caption(
            "Column mapping uses an LLM only for headers it does not recognise."
            if llm_enabled()
            else "LLM mapping is off. Unknown columns go to the mapping screen."
        )

    SCREENS[state["screen"]]()


main()
