"""LossLift — Streamlit entry point.

Routing and presentation only. Every decision about a document is made in
``core`` so that the whole pipeline stays runnable and testable without
Streamlit (spec section 7).

Two screens, not four: a **queue** (every document in this session, filed and
searchable) and a **workspace** (one open document at a time). Each document
tracks its own place in the pipeline independently — opening, mapping or
exporting one never moves another. That independence is the whole point: the
person running this will have dozens of reports in flight at once.
"""

from __future__ import annotations

import io
import zipfile
from decimal import Decimal
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
    run_pipeline,
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

_FIELD_LABELS = {name: export_module.COLUMN_TITLES.get(name, name) for name in CANONICAL_FIELDS}

REVIEW, EXPORT = "review", "export"

_STYLE = """
<style>
/* A subdued, professional palette instead of Streamlit's default bright
   alert colours — closer to what an underwriting system looks like. */
div[data-testid="stMetricValue"] { font-size: 1.4rem; }
.ll-row {
    padding: 0.35rem 0;
    border-bottom: 1px solid rgba(128, 128, 128, 0.18);
}
.ll-pill {
    display: inline-block;
    padding: 0.1rem 0.55rem;
    border-radius: 999px;
    font-size: 0.82rem;
    font-weight: 600;
    white-space: nowrap;
}
.ll-pill-clean { background: rgba(46, 125, 50, 0.14); color: #2e7d32; }
.ll-pill-review { background: rgba(230, 145, 15, 0.16); color: #a15c00; }
.ll-pill-mapping { background: rgba(2, 119, 189, 0.14); color: #01579b; }
</style>
"""

_STATUS_PILL = {
    "mapping": ('<span class="ll-pill ll-pill-mapping">Needs mapping</span>'),
    "needs_review": ('<span class="ll-pill ll-pill-review">Needs review</span>'),
    "clean": ('<span class="ll-pill ll-pill-clean">Reconciled</span>'),
}


# --------------------------------------------------------------------------
# Session state
#
# One document = one entry in `documents`, independent of every other. The
# only cross-document pointer is `open_document`: which one the workspace is
# currently showing, or None for the queue. There is deliberately no global
# "screen" — that was the bug. Each open document's own map/review/export
# position lives in its own Streamlit widget key (`stage-{id}`), which is
# naturally scoped per document and needs no bookkeeping here.
# --------------------------------------------------------------------------


def _state() -> dict[str, Any]:
    st.session_state.setdefault("documents", {})   # document_id -> ExtractionResult
    st.session_state.setdefault("order", [])       # document_id, upload order
    st.session_state.setdefault("staged", {})      # document_id -> IngestedFile
    st.session_state.setdefault("open_document", None)
    # Only files that could not be read at all. Per-document notes live with
    # their document, not here — a global error list turns one unmapped
    # document into forty red banners that bury the queue.
    st.session_state.setdefault("rejected", [])
    return st.session_state


def _result(document_id: str) -> ExtractionResult | None:
    return _state()["documents"].get(document_id)


def _store(result: ExtractionResult) -> None:
    """Register or refresh one document. Never touches any other document."""
    state = _state()
    document_id = result.document.document_id
    if document_id not in state["documents"]:
        state["order"].append(document_id)
    state["documents"][document_id] = result


def _open(document_id: str) -> None:
    _state()["open_document"] = document_id


def _close() -> None:
    _state()["open_document"] = None


def _status_of(result: ExtractionResult) -> str:
    """"mapping" | "needs_review" | "clean" — purely a fact about the data,
    never about which tab the user happens to have open."""
    if result.needs_mapping:
        return "mapping"
    if result.reconciliation.status is DocumentStatus.CLEAN:
        return "clean"
    return "needs_review"


# --------------------------------------------------------------------------
# Shared pieces
# --------------------------------------------------------------------------


# Financial-identity rules (R-01..R-07) are the only ones the engine ever
# marks ERROR -- they are arithmetic that must tie or a required field that
# must exist, never a judgement call. Everything from R-08 on is the engine's
# opinion that a human should look at something, not a claim that the numbers
# are wrong. Conflating the two -- "Reconciled" while showing 13 exceptions --
# is exactly the confusion a green badge is supposed to prevent.
_DUPLICATE_RULES = {"R-11", "R-12"}
_EXTRACTION_RULES = {"R-15"}

_CATEGORY_LABELS = {
    "financial": "Financial discrepancy",
    "duplicate": "Duplicate claim",
    "extraction": "Unreadable data",
    "business": "Business-rule review",
    "info": "Informational",
}


def _category_of(finding) -> str:
    if finding.severity is Severity.ERROR:
        return "financial"
    if finding.rule_id in _DUPLICATE_RULES:
        return "duplicate"
    if finding.rule_id in _EXTRACTION_RULES:
        return "extraction"
    if finding.severity is Severity.INFO:
        return "info"
    return "business"


def _badge(result: ExtractionResult) -> None:
    """Two separate facts, never merged into one word.

    Financial reconciliation PASS means the arithmetic ties and every
    required field is present -- nothing more. It says nothing about whether
    a date looks odd or a claim number repeats; those are real findings, just
    not evidence the numbers are wrong.
    """
    document, reconciliation = result.document, result.reconciliation
    passed = reconciliation.status is DocumentStatus.CLEAN
    review_items = [f for f in reconciliation.findings if f.severity is not Severity.ERROR]

    left, middle, right, far = st.columns([2.2, 1, 1, 1.2])
    with left:
        if passed:
            st.success("**Financial reconciliation: PASS**")
        else:
            errors = len(reconciliation.errors)
            st.error(
                f"**Financial reconciliation: FAIL** — {errors} "
                f"discrepanc{'y' if errors == 1 else 'ies'}"
            )
        if review_items:
            st.warning(f"**{len(review_items)} item(s) require review**")
        else:
            st.caption("No data-quality items to review.")
    middle.metric(
        "Valuation date",
        document.valuation_date.isoformat() if document.valuation_date else "missing",
    )
    right.metric("Claims", len(document.claims))
    total = document.column_total("incurred_total")
    far.metric("Total incurred", f"{total:,.2f}")


def _exception_summary(findings: list) -> None:
    """"13 items require review: 1 duplicate claim, 8 business-rule
    warnings, ..." -- the count breakdown up front, so a person can decide
    whether to open the table at all before they read a single row."""
    from collections import Counter

    by_category = Counter(_category_of(f) for f in findings)
    parts = [
        f"{count} {_CATEGORY_LABELS[category].lower()}{'s' if count != 1 else ''}"
        for category, count in sorted(by_category.items(), key=lambda kv: -kv[1])
    ]
    st.caption(f"{len(findings)} total: " + ", ".join(parts))


def _findings_table(result: ExtractionResult) -> None:
    findings = result.reconciliation.findings
    if not findings:
        st.caption("No exceptions. Every check passed.")
        return

    _exception_summary(findings)

    icons = {Severity.ERROR: "🔴", Severity.WARN: "🟠", Severity.INFO: "🔵"}
    rows = [
        {
            "": icons[finding.severity],
            "Category": _CATEGORY_LABELS[_category_of(finding)],
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


def _document_notes(result: ExtractionResult) -> None:
    """Per-document extraction notes, shown with their document.

    A document awaiting mapping reports every row as unidentifiable, which is
    true but not news: the status already says so, and repeating it once per
    row drowns the queue. Those notes are held back until the mapping is in
    place and they would mean something.
    """
    if result.needs_mapping or not result.warnings:
        return
    with st.expander(f"Extraction notes ({len(result.warnings)})"):
        for warning in result.warnings:
            st.caption(warning)


def _document_facts(result: ExtractionResult) -> None:
    document = result.document
    left, right = st.columns(2)
    left.markdown(
        f"""
**Carrier:** {document.carrier or "not found"}  
**Named insured:** {document.named_insured or "not found"}  
**Policy number:** {document.policy_number or "not found"}  
**Policy period:** {document.policy_period_start or "?"} to {document.policy_period_end or "?"}
"""
    )
    right.markdown(
        f"""
**Pages:** {document.page_count} ({document.extraction_method.value})  
**Numbers read as:** {"European (1.234,56)" if document.locale_hint == "eu" else "US (1,234.56)"}{"" if document.locale_confident else " — assumed"}  
**Dates read as:** {document.date_order or "unknown"}{"" if document.date_order_confident else " — assumed"}  
**Carrier profile:** {document.profile_name or "none saved yet"}  
**Recoveries printed as:** {document.recovery_convention_label}
"""
    )


def _profile_library() -> None:
    profiles = list_profiles()
    if not profiles:
        return
    with st.expander(f"Saved carrier formats ({len(profiles)})"):
        st.caption(
            "Once you map a carrier's columns, LossLift remembers the "
            "format. The next document with a matching layout skips the "
            "mapping screen and goes straight to review -- this list is "
            "what it has learned so far. Nothing here is claim data, only "
            "column labels and formatting rules."
        )
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
# Queue — the filing system
# --------------------------------------------------------------------------


def screen_queue() -> None:
    state = _state()
    st.subheader("Loss run queue")
    st.caption(
        "Drop in carrier PDFs — one or a hundred at once. Files are held in "
        "memory for this session and deleted after you export."
    )

    state.setdefault("uploader_generation", 0)
    uploads = st.file_uploader(
        "Choose PDF files",
        type=["pdf"],
        accept_multiple_files=True,
        label_visibility="collapsed",
        key=f"uploader-{state['uploader_generation']}",
    )
    if uploads and st.button("Add to queue", type="primary"):
        # _extract_uploads ends in st.rerun(), which halts this function via
        # an exception -- any code after that call here would never run.
        # The generation bump has to happen inside it, before the rerun.
        _extract_uploads(uploads)

    for message in state["rejected"]:
        st.error(message)

    if not state["order"]:
        st.info("No documents yet. Upload loss runs to get started.")
        _profile_library()
        return

    st.divider()
    _queue_summary()
    st.divider()
    _queue_toolbar_and_list()
    st.divider()
    _profile_library()


def _extract_uploads(uploads: list[Any]) -> None:
    """Process every upload and land back on the queue with all of them
    visible. Never guess which one the user wants to see next — that guess is
    exactly what made multi-file uploads look broken before."""
    state = _state()
    state["rejected"] = []
    progress = st.progress(0.0, text="Reading documents")
    added = 0

    for index, upload in enumerate(uploads, start=1):
        try:
            staged = ingest(upload.getvalue(), upload.name)
            result = run_pipeline(staged, use_llm=llm_enabled())
            _store(result)
            state["staged"][result.document.document_id] = staged
            added += 1
        except IngestError as error:
            state["rejected"].append(f"{upload.name}: {error}")
        except Exception as error:  # noqa: BLE001 - surface, never crash the page
            state["rejected"].append(
                f"{upload.name} could not be read ({error}). If it is a scan, "
                f"turn on vision extraction; otherwise send us the file."
            )
        progress.progress(index / len(uploads), text=f"Read {upload.name}")

    progress.empty()
    if added:
        state["last_added"] = added
        # A fresh uploader key so already-processed files disappear from the
        # tray instead of sitting there ready to be re-added by accident.
        state["uploader_generation"] = state.get("uploader_generation", 0) + 1
    st.rerun()


def _queue_summary() -> None:
    state = _state()
    results = [state["documents"][did] for did in state["order"]]
    statuses = [_status_of(result) for result in results]

    if state.pop("last_added", None):
        st.success(f"Added {len(results)} total document(s) to the queue.")

    cols = st.columns(4)
    cols[0].metric("Documents", len(results))
    cols[1].metric("Needs mapping", statuses.count("mapping"))
    cols[2].metric("Needs review", statuses.count("needs_review"))
    cols[3].metric("Reconciled", statuses.count("clean"))


_SORT_OPTIONS = {
    "Upload order": lambda r: 0,
    "Filename": lambda r: r.document.source_filename.lower(),
    "Status": lambda r: {"mapping": 0, "needs_review": 1, "clean": 2}[_status_of(r)],
    "Total incurred": lambda r: -r.document.column_total("incurred_total"),
    "Claims": lambda r: -len(r.document.claims),
}

_STATUS_FILTERS = {
    "All": None,
    "Needs mapping": "mapping",
    "Needs review": "needs_review",
    "Reconciled": "clean",
}


def _queue_toolbar_and_list() -> None:
    state = _state()

    search, status_filter, sort_by = st.columns([2, 1.3, 1.3])
    query = search.text_input(
        "Search", placeholder="Search filename, carrier, or insured…",
        label_visibility="collapsed",
    ).strip().lower()
    status_choice = status_filter.selectbox(
        "Status", list(_STATUS_FILTERS), label_visibility="collapsed"
    )
    sort_choice = sort_by.selectbox(
        "Sort by", list(_SORT_OPTIONS), label_visibility="collapsed"
    )

    visible_ids = []
    for document_id in state["order"]:
        result = _result(document_id)
        if result is None:
            continue
        status = _status_of(result)
        if _STATUS_FILTERS[status_choice] not in (None, status):
            continue
        if query:
            haystack = " ".join(
                filter(None, [
                    result.document.source_filename,
                    result.document.carrier,
                    result.document.named_insured,
                ])
            ).lower()
            if query not in haystack:
                continue
        visible_ids.append(document_id)

    visible_ids.sort(key=lambda did: _SORT_OPTIONS[sort_choice](_result(did)))

    if not visible_ids:
        st.caption("No documents match this filter.")
        return

    _batch_export_bar(visible_ids)
    st.divider()

    header = st.columns([0.4, 3, 1.5, 0.9, 1.3, 1.2, 0.8])
    for col, title in zip(
        header, ["", "Document", "Status", "Claims", "Total incurred", "Valuation", ""]
    ):
        col.markdown(f"**{title}**")

    for document_id in visible_ids:
        _queue_row(document_id)


def _queue_row(document_id: str) -> None:
    result = _result(document_id)
    document = result.document
    status = _status_of(result)

    with st.container():
        check, name, status_col, claims, incurred, valuation, action = st.columns(
            [0.4, 3, 1.5, 0.9, 1.3, 1.2, 0.8]
        )
        check.checkbox("Select", key=f"select-{document_id}", label_visibility="collapsed")
        name.markdown(
            f"📄 **{document.source_filename}**  \n"
            f"<span style='color: gray; font-size: 0.85rem;'>"
            f"{document.carrier or 'Carrier unknown'}</span>",
            unsafe_allow_html=True,
        )
        status_col.markdown(_STATUS_PILL[status], unsafe_allow_html=True)
        claims.write(str(len(document.claims)))
        incurred.write(f"{document.column_total('incurred_total'):,.2f}")
        valuation.write(
            document.valuation_date.isoformat() if document.valuation_date else "—"
        )
        if action.button("Open", key=f"open-{document_id}"):
            _open(document_id)
            st.rerun(scope="app")
        st.markdown('<div class="ll-row"></div>', unsafe_allow_html=True)


def _batch_export_bar(visible_ids: list[str]) -> None:
    state = _state()
    selected_ids = [
        did for did in visible_ids if st.session_state.get(f"select-{did}")
    ]

    left, right = st.columns([1, 1])
    with left:
        select_all, clear_all = st.columns(2)
        if select_all.button(f"Select all {len(visible_ids)} shown"):
            for did in visible_ids:
                st.session_state[f"select-{did}"] = True
            st.rerun(scope="app")
        if clear_all.button("Clear selection"):
            for did in visible_ids:
                st.session_state[f"select-{did}"] = False
            st.rerun(scope="app")

    with right:
        with st.expander(
            f"Export {len(selected_ids)} selected report(s)",
            expanded=bool(selected_ids),
        ):
            if not selected_ids:
                st.caption("Select one or more documents above to export them together.")
                return
            template = st.selectbox(
                "Column order", list(export_module.EXPORT_TEMPLATES),
                index=list(export_module.EXPORT_TEMPLATES).index(export_module.DEFAULT_TEMPLATE),
                key="batch-template",
            )
            redact = st.toggle(
                "Remove claimant names and loss descriptions", key="batch-redact"
            )
            st.caption("Applied the same way to every selected report.")

            if st.button(f"Prepare {len(selected_ids)} report(s)", type="primary"):
                state["batch_zip"] = _build_batch_zip(selected_ids, template, redact)
                state["batch_zip_count"] = len(selected_ids)

            if state.get("batch_zip"):
                st.download_button(
                    f"Download {state['batch_zip_count']} report(s) (.zip)",
                    data=state["batch_zip"],
                    file_name="losslift-export.zip",
                    mime="application/zip",
                    type="primary",
                )


def _build_batch_zip(document_ids: list[str], template: str, redact: bool) -> bytes:
    buffer = io.BytesIO()
    used_names: set[str] = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for document_id in document_ids:
            result = _result(document_id)
            if result is None:
                continue
            payload = export_module.to_bytes(
                result.document, result.reconciliation,
                template=template, redact=redact,
            )
            name = export_module.suggested_filename(result.document)
            if name in used_names:
                stem, _, ext = name.rpartition(".")
                name = f"{stem} ({document_id[:8]}).{ext}"
            used_names.add(name)
            archive.writestr(name, payload)
    return buffer.getvalue()


# --------------------------------------------------------------------------
# Workspace — one open document
# --------------------------------------------------------------------------


def screen_workspace(document_id: str) -> None:
    result = _result(document_id)
    if result is None:
        st.warning("This document is no longer in the session.")
        _close()
        st.rerun()
        return

    top_left, top_right = st.columns([1, 5])
    if top_left.button("← Back to queue"):
        _close()
        st.rerun()
    with top_right:
        st.markdown(
            f"## 📄 {result.document.source_filename}"
        )
        st.caption(
            f"{result.document.carrier or 'Carrier unknown'}"
            + (f" · {result.document.named_insured}" if result.document.named_insured else "")
        )
    st.divider()

    if result.needs_mapping:
        screen_mapping(document_id, result)
        return

    stage = st.radio(
        "Stage", [REVIEW, EXPORT],
        format_func=lambda s: {"review": "Review", "export": "Export"}[s],
        horizontal=True, label_visibility="collapsed",
        key=f"stage-{document_id}",
    )
    if stage == REVIEW:
        screen_review(document_id, result)
    else:
        screen_export(document_id, result)


def screen_mapping(document_id: str, result: ExtractionResult) -> None:
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

    with st.form(f"mapping-{document_id}"):
        for index, header in enumerate(headers):
            current = result.mapping.fields.get(index)
            current_label = by_field.get(current, unused)
            left, right = st.columns([1, 1])
            left.markdown(f"**{header or f'Column {index + 1}'}**")
            selection = right.selectbox(
                f"Field for {header or index}",
                options,
                index=options.index(current_label),
                key=f"map-{document_id}-{index}",
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
        _apply_mapping(document_id, result, chosen)


def _apply_mapping(
    document_id: str, result: ExtractionResult, chosen: dict[int, str | None]
) -> None:
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

    staged = _state()["staged"].get(document_id)
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
    st.rerun()


def screen_review(document_id: str, result: ExtractionResult) -> None:
    if result.mapping.source == "profile":
        st.info(
            f"Mapped automatically from a saved **{result.document.carrier or 'carrier'}** "
            f"format — no mapping step was needed for this document. Saved "
            f"formats are shown at the bottom of the queue."
        )
    _badge(result)
    _document_facts(result)
    _document_notes(result)

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
        key=f"editor-{document_id}",
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


def screen_export(document_id: str, result: ExtractionResult) -> None:
    _badge(result)

    left, right = st.columns(2)
    template = left.selectbox(
        "Column order", list(export_module.EXPORT_TEMPLATES),
        index=list(export_module.EXPORT_TEMPLATES).index(export_module.DEFAULT_TEMPLATE),
        key=f"template-{document_id}",
    )
    redact = right.toggle(
        "Remove claimant names and loss descriptions",
        help="Leave personal data out of the spreadsheet you send on.",
        key=f"redact-{document_id}",
    )
    provenance = right.toggle(
        "Include page and extraction columns", value=True, key=f"provenance-{document_id}"
    )

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
        key=f"download-{document_id}",
    )
    st.caption(
        "Three sheets: Claims, Exceptions, and Source Info with the file hash "
        "and valuation date. For many reports at once, use Export from the queue."
    )

    st.divider()
    if st.button("Delete the uploaded file now", key=f"delete-{document_id}"):
        staged = _state()["staged"].pop(document_id, None)
        if staged is not None:
            discard(staged)
        st.success(
            "Deleted. The extracted table stays in this session until you close "
            "the tab."
        )


# --------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------


def main() -> None:
    st.markdown(_STYLE, unsafe_allow_html=True)
    state = _state()

    with st.sidebar:
        st.markdown("### LossLift")
        st.caption("Carrier loss runs in, reconciled spreadsheets out.")
        if state["order"]:
            results = [state["documents"][did] for did in state["order"]]
            statuses = [_status_of(r) for r in results]
            st.metric("In this session", len(results))
            st.caption(
                f"{statuses.count('mapping')} need mapping · "
                f"{statuses.count('needs_review')} need review · "
                f"{statuses.count('clean')} reconciled"
            )
        if state["open_document"]:
            if st.button("← Back to queue", width="stretch"):
                _close()
                st.rerun()
        st.divider()
        st.caption(
            "Column mapping uses an LLM only for headers it does not recognise."
            if llm_enabled()
            else "LLM mapping is off. Unknown columns go to the mapping screen."
        )

    st.title("LossLift")

    if state["open_document"]:
        screen_workspace(state["open_document"])
    else:
        screen_queue()


main()
