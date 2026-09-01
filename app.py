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
from pathlib import Path
from decimal import Decimal
from typing import Any

import pandas as pd
import streamlit as st

from core import export as export_module
from core.review import (
    EXTRACTION_RULES,
    finding_key,
    FINANCIAL_RULES,
    ReviewAction,
    summarise_review,
)
from core.evidence import (
    claim_evidence,
    confirm_region,
    finding_evidence,
    render_evidence,
)
from core.ingest import IngestError, discard, ingest
from core.pipeline import (
    resolve_finding,
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
from core.account import UNNAMED_ACCOUNT, build_accounts
from core.summary import summarise_by_period
from core.schema import (
    CANONICAL_FIELDS,
    DATE_FIELDS,
    MONEY_FIELDS,
    Claim,
    ClaimStatus,
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

/* A quiet left-accent bar reads as "flagged" without turning the page into
   a wall of solid colour -- native st.error/st.warning boxes are a full
   saturated fill, which is what read as blocky. This is one thin bar of
   colour, a near-white tint behind it, and body-colour text. */
.ll-status {
    padding: 0.55rem 0.9rem;
    border-left: 3px solid;
    font-size: 0.95rem;
    margin-bottom: 0.6rem;
}
.ll-status-pass { border-color: #2e7d32; background: rgba(46, 125, 50, 0.05); }
.ll-status-fail { border-color: #b3261e; background: rgba(179, 38, 30, 0.05); }

.ll-finding {
    padding: 0.3rem 0.7rem;
    border-left: 3px solid;
    font-size: 0.88rem;
    line-height: 1.5;
    margin-bottom: 0.2rem;
}
.ll-finding-issue { border-color: #b3261e; background: rgba(179, 38, 30, 0.045); }
.ll-finding-flag { border-color: #a15c00; background: rgba(161, 92, 0, 0.045); }
.ll-finding-claim { color: #6b6b6b; font-size: 0.82rem; }
</style>
"""

def _status_pill(result: ExtractionResult) -> str:
    """User-facing status: Ready / Review N issue(s) / Needs mapping --
    never the internal rule/status vocabulary."""
    if result.needs_mapping:
        return '<span class="ll-pill ll-pill-mapping">Needs mapping</span>'
    data_issues, _flags = _split_findings(result.reconciliation.findings)
    if not data_issues:
        return '<span class="ll-pill ll-pill-clean">✓ Ready</span>'
    label = f"Review {len(data_issues)} issue{'s' if len(data_issues) != 1 else ''}"
    return f'<span class="ll-pill ll-pill-review">{label}</span>'


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


def _is_reviewed(document_id: str) -> bool:
    return bool(st.session_state.get(f"reviewed-{document_id}"))


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
    never about which tab the user happens to have open. Uses the same
    Data Quality Issue gate as the reconciliation card so the queue pill and
    the open document never disagree about whether it is reconciled."""
    if result.needs_mapping:
        return "mapping"
    data_issues, _flags = _split_findings(result.reconciliation.findings)
    return "needs_review" if data_issues else "clean"


# --------------------------------------------------------------------------
# Shared pieces
# --------------------------------------------------------------------------


# Two buckets, not a severity ladder. Data Quality Issues determine whether
# the document can be called reconciled; Underwriting Flags are observations
# about otherwise-valid data and never block that call. A few rules the
# engine marks WARN move into the blocking bucket anyway: an unreadable or
# ambiguous value, or a duplicate the extractor produced, is evidence the
# document could not be read cleanly, not a business condition worth an
# underwriter's judgement -- which is what "flag" means here.
# The split lives in core/review.py so the queue pill, the reconciliation card
# and the export cannot drift apart. Financial rules ask whether the numbers
# add up; extraction rules ask whether the document could be read at all.
# Together they are what blocks the reconciled badge -- exactly as before.
_DATA_QUALITY_RULES = FINANCIAL_RULES | EXTRACTION_RULES


def _is_data_issue(finding) -> bool:
    return finding.rule_id in _DATA_QUALITY_RULES


def _split_findings(findings: list) -> tuple[list, list]:
    data_issues = [f for f in findings if _is_data_issue(f)]
    flags = [f for f in findings if not _is_data_issue(f)]
    return data_issues, flags


def _reconciliation_card(result: ExtractionResult) -> None:
    """The account/document header and the reconciliation card.

    "Reconciled to carrier" means the document's own printed totals and row
    count match what was extracted, and every value needed to compute that
    could be read. It says nothing about whether a claim looks unusual --
    that is what the flags below are for, and they never affect this line.
    """
    document = result.document
    data_issues, flags = _split_findings(result.reconciliation.findings)
    passed = not data_issues

    st.markdown(
        f"#### {document.carrier or 'Carrier unknown'}"
        + (f" — {document.named_insured}" if document.named_insured else "")
    )
    meta = []
    if document.line_of_business:
        meta.append(document.line_of_business.value)
    if document.policy_number:
        meta.append(f"Policy: {document.policy_number}")
    meta.append(
        f"Valuation: {document.valuation_date.isoformat() if document.valuation_date else 'missing'}"
    )
    st.caption(" · ".join(meta))

    if passed:
        st.markdown(
            '<div class="ll-status ll-status-pass">✓ <b>Reconciled to carrier</b></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div class="ll-status ll-status-fail">✗ <b>Not reconciled</b> — '
            f'{len(data_issues)} data issue{"s" if len(data_issues) != 1 else ""}</div>',
            unsafe_allow_html=True,
        )

    with st.container():
        extracted_count = len(document.claims)
        printed_count = document.printed_claim_count
        count_line = (
            f"{extracted_count} / {printed_count} claims captured"
            if printed_count is not None
            else f"{extracted_count} claims captured"
        )
        st.write(count_line)

        printed_incurred = document.printed_totals.get("incurred_total")
        extracted_incurred = document.column_total("incurred_total")
        cols = st.columns(3)
        cols[0].metric(
            "Carrier total incurred",
            f"{printed_incurred:,.2f}" if printed_incurred is not None else "not printed",
        )
        cols[1].metric("LossLift total incurred", f"{extracted_incurred:,.2f}")
        if printed_incurred is not None:
            cols[2].metric("Difference", f"{extracted_incurred - printed_incurred:,.2f}")
        else:
            cols[2].metric("Difference", "—")

    counts = st.columns(2)
    counts[0].metric("Data Issues", len(data_issues))
    counts[1].metric("Underwriting Flags", len(flags))


def _finding_list_html(findings: list, css_class: str) -> str:
    """One block of markup for a whole bucket of findings, not one Streamlit
    widget per row. A 44-claim document can easily carry a dozen findings,
    and a dozen native st.error/st.warning components is both the "wall of
    solid colour" look and a dozen extra widgets Streamlit has to mount on
    every rerun. A single markdown block is one element either way."""
    import html as _html

    lines = []
    for finding in findings:
        message = _html.escape(finding.message)
        claim = (
            f' <span class="ll-finding-claim">· Claim {_html.escape(finding.claim_number)}</span>'
            if finding.claim_number
            else ""
        )
        lines.append(f'<div class="ll-finding {css_class}">{message}{claim}</div>')
    return "\n".join(lines)


def _findings_table(result: ExtractionResult) -> None:
    """Plain language first, for the person deciding what to do next. The
    rule id, expected/actual and delta that made the finding are real and
    kept, just moved under one shared "Technical details" expander rather
    than sitting in the primary view of every row."""
    findings = result.reconciliation.findings
    if not findings:
        st.caption("No exceptions. Every check passed.")
        return

    data_issues, flags = _split_findings(findings)

    if data_issues:
        st.markdown(f"**Data Quality Issues ({len(data_issues)})**")
        st.markdown(_finding_list_html(data_issues, "ll-finding-issue"), unsafe_allow_html=True)

    if flags:
        st.markdown(f"**Underwriting Flags ({len(flags)})**")
        st.markdown(_finding_list_html(flags, "ll-finding-flag"), unsafe_allow_html=True)

    with st.expander("Technical details"):
        icons = {Severity.ERROR: "🔴", Severity.WARN: "🟠", Severity.INFO: "🔵"}
        rows = [
            {
                "": icons[finding.severity],
                "Bucket": "Data issue" if _is_data_issue(finding) else "Flag",
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


def _evidence_panel(result: ExtractionResult, document_id: str) -> None:
    """Show the page a claim or a finding was read from, with its row marked.

    The question a reviewer asks of any figure is where it came from, and the
    only satisfying answer is the carrier's own page with the line marked on
    it. Where the row cannot be marked -- a scanned page the vision model read,
    or a rectangle that does not survive being read back -- the page is still
    shown and the reason is said, rather than marking a plausible-looking row.
    """
    document = result.document
    findings = result.reconciliation.findings
    if not document.claims and not findings:
        return

    choices: list[tuple[str, Any]] = [
        (f"Claim {claim.claim_number}", ("claim", index))
        for index, claim in enumerate(document.claims)
    ] + [
        (f"{finding.rule_id} · {finding.claim_number or 'document'}", ("finding", index))
        for index, finding in enumerate(findings)
    ]
    labels = [label for label, _ in choices]
    picked = st.selectbox(
        "Show me where this came from",
        labels,
        key=f"evidence-pick-{document_id}",
        index=0,
    )
    kind, index = dict(choices)[picked]

    if kind == "claim":
        claim = document.claims[index]
        fields = [""] + sorted(claim.raw_cells)
        field = st.selectbox(
            "Field (optional)",
            fields,
            key=f"evidence-field-{document_id}",
            format_func=lambda name: _FIELD_LABELS.get(name, name) if name else "the whole row",
        )
        evidence = claim_evidence(claim, field or None)
        expect = claim.claim_number
    else:
        finding = findings[index]
        evidence = finding_evidence(document, finding)
        expect = finding.claim_number or ""

    if result.source_path and expect:
        evidence = confirm_region(result.source_path, evidence, expect)

    left, right = st.columns([2, 3])
    with left:
        st.markdown(f"**Source** — {evidence.describe()}")
        st.caption(evidence.note)
        st.markdown(f"Extraction: `{evidence.method.value}`")
        if evidence.text:
            st.markdown("Text on the page:")
            st.code(evidence.text, language=None)
    with right:
        if not result.source_path or not Path(result.source_path).exists():
            st.info(
                "The uploaded file has been deleted, so the page cannot be shown. "
                "The page number, line and cell text above are kept."
            )
            return
        image = render_evidence(result.source_path, evidence)
        if image is None:
            st.info("There is no page to show for this one.")
        else:
            st.image(image, width="stretch")


_ACTION_LABELS = {
    ReviewAction.CONFIRMED: "Confirm — the document is right",
    ReviewAction.CORRECTED: "Correct the value",
    ReviewAction.DISMISSED: "Dismiss — does not apply",
}


def _review_status_bar(result: ExtractionResult) -> None:
    """Four readings, side by side and never merged into one.

    A document that does not reconcile has not become healthy because somebody
    read every warning, and one whose warnings are all outstanding has not
    stopped balancing. Each column answers its own question.
    """
    summary = summarise_review(
        result.reconciliation.findings, result.document.review_log
    )
    columns = st.columns(4)
    columns[0].metric(
        "Extraction",
        "clean" if summary.extraction.passes else f"{summary.extraction.total} issue(s)",
    )
    columns[1].metric(
        "Financial reconciliation",
        "ties" if summary.financial.passes else f"{summary.financial.total} failing",
    )
    columns[2].metric("Underwriting flags", summary.underwriting.total or "none")
    columns[3].metric(
        "Reviewed", f"{summary.reviewed} of {summary.total}" if summary.total else "—"
    )
    if summary.total and summary.fully_reviewed and not summary.financial.passes:
        st.caption(
            "Every finding has been reviewed. The document still does not "
            "reconcile — reviewing a finding records that somebody looked at "
            "it, not that the figures now agree."
        )


def _review_workspace(result: ExtractionResult, document_id: str) -> None:
    """One finding at a time: what, why, where from, and what to do about it."""
    document = result.document
    findings = result.reconciliation.findings
    if not findings:
        st.success("No findings to review.")
        return

    log = document.review_log
    outstanding = [f for f in findings if not log.is_resolved(f)]
    show_all = st.toggle(
        "Include findings already reviewed",
        key=f"review-all-{document_id}",
        value=not outstanding,
    )
    queue = findings if show_all else outstanding
    if not queue:
        st.success("Every finding has been reviewed.")
        return

    def _label(finding) -> str:
        mark = "" if log.is_resolved(finding) else "• "
        return (
            f"{mark}{finding.rule_id} · {finding.claim_number or 'document'}"
            f" — {finding.message[:60]}"
        )

    picked = st.selectbox(
        f"{len(outstanding)} finding(s) awaiting review",
        list(range(len(queue))),
        format_func=lambda index: _label(queue[index]),
        key=f"review-pick-{document_id}",
    )
    finding = queue[picked]
    claim = next(
        (c for c in document.claims if c.claim_number == finding.claim_number), None
    )

    st.markdown(f"**{finding.rule_id}** — {finding.message}")
    if finding.expected is not None or finding.actual is not None:
        cols = st.columns(3)
        cols[0].metric("Expected", _money(finding.expected))
        cols[1].metric("Found", _money(finding.actual))
        cols[2].metric("Difference", _money(finding.delta))

    evidence = finding_evidence(document, finding)
    if result.source_path and finding.claim_number:
        evidence = confirm_region(result.source_path, evidence, finding.claim_number)

    left, right = st.columns([2, 3])
    with left:
        if claim is not None and finding.field:
            st.markdown(f"**Claim {claim.claim_number}** · {_FIELD_LABELS.get(finding.field, finding.field)}")
            raw = claim.raw_cells.get(finding.field)
            st.markdown("Text on the page:")
            st.code(raw if raw else "(nothing in this column)", language=None)
            st.markdown(f"Read as: `{getattr(claim, finding.field, None)}`")
            was = claim.original_of(finding.field)
            if was is not None:
                st.markdown(f"Originally extracted: `{was}` — since corrected")
            issue = claim.field_issues.get(finding.field)
            if issue is not None:
                st.markdown(f"Why it is null: `{issue.value}`")
        st.caption(evidence.note)
        st.markdown(f"Source: {evidence.describe()} · `{evidence.method.value}`")
    with right:
        image = (
            render_evidence(result.source_path, evidence)
            if result.source_path and Path(result.source_path).exists()
            else None
        )
        if image is not None:
            st.image(image, width="stretch")
        else:
            st.info("The page cannot be shown; the record above is kept.")

    previous = log.latest_for(finding_key(finding))
    if previous is not None:
        st.info(
            f"Reviewed {previous.at.strftime('%Y-%m-%d %H:%M UTC')} by "
            f"{previous.reviewer}: **{previous.action.value}**"
            + (f" — {previous.note}" if previous.note else "")
        )

    correctable = claim is not None and finding.field in Claim.model_fields
    choices = [ReviewAction.CONFIRMED, ReviewAction.DISMISSED]
    if correctable:
        choices.insert(1, ReviewAction.CORRECTED)

    with st.form(key=f"review-form-{document_id}-{finding_key(finding)}"):
        action = st.radio(
            "What do you want to do?",
            choices,
            format_func=lambda choice: _ACTION_LABELS[choice],
            key=f"review-action-{document_id}",
        )
        corrected = None
        if correctable:
            corrected = st.text_input(
                f"Corrected {_FIELD_LABELS.get(finding.field, finding.field)} "
                f"(only used if you choose to correct)",
                value="",
                key=f"review-value-{document_id}",
            )
        note = st.text_area("Note (optional)", key=f"review-note-{document_id}")
        if st.form_submit_button("Record decision"):
            if action is ReviewAction.CORRECTED and not str(corrected).strip():
                st.error("Enter the corrected value, or choose confirm or dismiss.")
            else:
                _store(
                    resolve_finding(
                        result, finding, action,
                        note=note,
                        corrected_value=corrected if action is ReviewAction.CORRECTED else None,
                    )
                )
                st.rerun()


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
    _accounts_panel()
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
    reviewed_count = sum(1 for did in state["order"] if _is_reviewed(did))

    if state.pop("last_added", None):
        st.success(f"Added {len(results)} total document(s) to the queue.")

    cols = st.columns(5)
    cols[0].metric("Documents", len(results))
    cols[1].metric("Needs mapping", statuses.count("mapping"))
    cols[2].metric("Needs review", statuses.count("needs_review"))
    cols[3].metric("Reconciled", statuses.count("clean"))
    cols[4].metric("Reviewed", f"{reviewed_count} / {len(results)}")


#: Widths and headings for the queue table. The heading row and the data rows
#: are laid out separately, so they share these to stay aligned.
_QUEUE_COLUMNS = [0.6, 0.8, 2.5, 1.4, 0.9, 1.3, 1.1, 0.8]
#: Rows rendered before the list is capped. Roughly a screenful.
_QUEUE_PAGE_SIZE = 20
_QUEUE_HEADINGS = [
    "Export", "Reviewed", "Document", "Status", "Claims",
    "Total incurred", "Valuation", "",
]

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
    hide_reviewed = st.checkbox(
        "Hide reviewed",
        key="hide_reviewed",
        help="Already looked at these? Hide them so what's left to do is all "
             "that's on screen.",
    )

    visible_ids = []
    for document_id in state["order"]:
        result = _result(document_id)
        if result is None:
            continue
        if hide_reviewed and _is_reviewed(document_id):
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
        st.caption(
            "Nothing left to review." if hide_reviewed else "No documents match this filter."
        )
        return

    _batch_export_bar(visible_ids)

    # Every row is a live set of controls, and the browser re-renders all of
    # them on any interaction anywhere on the page. Past a screenful that cost
    # is paid on every click for rows nobody is looking at, so show a page at a
    # time and let the search and filters above do the narrowing.
    shown, hidden = visible_ids, 0
    if len(visible_ids) > _QUEUE_PAGE_SIZE:
        if not st.checkbox(
            f"Show all {len(visible_ids)} documents",
            key="show_all_rows",
            help="Off by default: a long list re-renders every row on every "
                 "click, which is what makes the queue feel slow.",
        ):
            shown = visible_ids[:_QUEUE_PAGE_SIZE]
            hidden = len(visible_ids) - _QUEUE_PAGE_SIZE
    st.divider()

    header = st.columns(_QUEUE_COLUMNS)
    for col, title in zip(header, _QUEUE_HEADINGS):
        col.markdown(f"**{title}**")

    for document_id in shown:
        _queue_row(document_id)

    if hidden:
        st.caption(
            f"{hidden} more not shown. Search or filter to narrow the list, "
            f"or tick \u201cShow all\u201d above."
        )


def _queue_row(document_id: str) -> None:
    result = _result(document_id)
    document = result.document
    reviewed = _is_reviewed(document_id)

    with st.container():
        check, done, name, status_col, claims, incurred, valuation, action = st.columns(
            _QUEUE_COLUMNS
        )
        check.checkbox(
            "Export", key=f"select-{document_id}", label_visibility="collapsed",
            help="Include this report in the batch export above.",
        )
        done.checkbox(
            "Reviewed", key=f"reviewed-{document_id}", label_visibility="collapsed",
            help="Mark it as looked at, so you can hide it and see what is left.",
        )
        filename_style = "opacity: 0.55;" if reviewed else ""
        name.markdown(
            f"<div style='{filename_style}'>📄 <b>{document.source_filename}</b><br>"
            f"<span style='color: gray; font-size: 0.85rem;'>"
            f"{document.carrier or 'Carrier unknown'}</span></div>",
            unsafe_allow_html=True,
        )
        status_col.markdown(_status_pill(result), unsafe_allow_html=True)
        summary = summarise_review(result.reconciliation.findings, document.review_log)
        if summary.total:
            # The headline names the worst thing that is true, so a document
            # that does not reconcile is described that way however much of it
            # has been reviewed. Progress sits beside it, never in place of it.
            progress = (
                "all reviewed" if summary.fully_reviewed
                else f"{summary.outstanding} to review"
            )
            status_col.caption(f"{summary.headline()} · {progress}")
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

    # Streamlit's default rerun dimming reads as a stall on a heavy table, inviting re-clicks.
    with st.spinner(f"Opening {result.document.source_filename}…"):
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


def _loss_snapshot(document) -> None:
    """The aggregate risk picture -- what an underwriter opens the document
    for. Every number here is a Decimal sum or count over already-parsed
    claims; nothing is estimated and no LLM is involved (spec section 2)."""
    claims = document.claims
    if not claims:
        return

    open_count = sum(
        1 for c in claims if c.claim_status in (ClaimStatus.OPEN, ClaimStatus.REOPENED)
    )
    closed_count = sum(1 for c in claims if c.claim_status is ClaimStatus.CLOSED)
    incurred_claims = [c for c in claims if c.incurred_total is not None]
    largest = max(incurred_claims, key=lambda c: c.incurred_total, default=None)

    st.markdown("**Loss snapshot**")
    row1 = st.columns(4)
    row1[0].metric("Claims", len(claims))
    row1[1].metric("Open", open_count)
    row1[2].metric("Closed", closed_count)
    row1[3].metric(
        "Largest loss", f"{largest.incurred_total:,.2f}" if largest else "—"
    )
    row2 = st.columns(4)
    row2[0].metric("Total paid", f"{document.column_total('paid_total'):,.2f}")
    row2[1].metric("Outstanding reserve", f"{document.column_total('reserve_total'):,.2f}")
    row2[2].metric("Recoveries", f"{document.column_total('recovery_total'):,.2f}")
    row2[3].metric("Total incurred", f"{document.column_total('incurred_total'):,.2f}")

    threshold = st.number_input(
        "Large loss threshold",
        min_value=0, value=25000, step=5000,
        key=f"threshold-{document.document_id}",
    )
    large = sorted(
        (c for c in incurred_claims if c.incurred_total >= threshold),
        key=lambda c: c.incurred_total, reverse=True,
    )
    if large:
        st.caption(f"{len(large)} claim(s) at or above {threshold:,.0f}")
        st.dataframe(
            [
                {
                    "Claim #": c.claim_number,
                    "Status": c.claim_status.value,
                    "Loss date": c.date_of_loss.isoformat() if c.date_of_loss else "—",
                    "Paid": _money(c.paid_total),
                    "Reserve": _money(c.reserve_total),
                    "Incurred": _money(c.incurred_total),
                }
                for c in large
            ],
            hide_index=True, width="stretch",
        )
    else:
        st.caption(f"No claims at or above {threshold:,.0f}.")


def _period_summary(document) -> None:
    """Claims by policy term — the loss history a submission actually asks for.

    Where the carrier printed a subtotal per term, each row says whether that
    term ties to it. Knowing which year is out, and by how much, is what a
    reviewer can act on; a single document-level pass or fail is not.
    """
    periods = summarise_by_period(document)
    if len(periods) < 2:
        return  # a single term is the snapshot above, said twice

    st.markdown("**Loss summary by policy term**")
    checked = [period for period in periods if period.has_printed_check]
    if checked:
        agree = sum(1 for period in checked if period.ties())
        if agree == len(checked):
            st.markdown(
                f'<div class="ll-status ll-status-pass">✓ All {agree} policy '
                f"terms tie to the totals printed for them</div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div class="ll-status ll-status-fail">{len(checked) - agree} of '
                f"{len(checked)} policy terms do not tie to the totals printed "
                f"for them</div>",
                unsafe_allow_html=True,
            )

    rows = []
    for period in periods:
        ties = period.ties()
        difference = period.difference("incurred_total")
        rows.append({
            "Policy term": period.label,
            "Claims": period.claims,
            "Open": period.open_claims,
            "Closed": period.closed_claims,
            "Paid": _money(period.totals["paid_total"]),
            "Reserves": _money(period.totals["reserve_total"]),
            "Incurred": _money(period.totals["incurred_total"]),
            "Largest": _money(period.largest_loss),
            "Ties to carrier": (
                "—" if ties is None
                else "✓" if ties
                else f"off by {difference:,.2f}" if difference
                else "differs"
            ),
        })
    st.dataframe(rows, hide_index=True, width="stretch")


def _accounts_panel() -> None:
    """Loss runs for one insured, merged into the history a submission asks for.

    Runs arrive one per carrier per term, so the document an underwriter prices
    from has to be assembled. Only shown once an insured has more than one run,
    since merging one run with nothing is the review screen again.
    """
    state = _state()
    documents = [
        state["documents"][did].document
        for did in state["order"]
        if not state["documents"][did].needs_mapping
    ]
    accounts = [
        account
        for account in build_accounts(documents)
        if len(account.documents) > 1 and account.name != UNNAMED_ACCOUNT
    ]
    if not accounts:
        return

    st.markdown("### Accounts")
    st.caption(
        "Loss runs filed under the same insured, merged. Where a claim appears "
        "in more than one run, the newest valuation is the one carried forward."
    )
    for account in accounts:
        with st.expander(
            f"{account.name} — {len(account.documents)} loss runs, "
            f"{len(account.histories)} claims"
        ):
            st.caption(
                "Valued at "
                + ", ".join(d.isoformat() for d in account.valuation_dates)
                + " · " + ", ".join(
                    d.source_filename for d in account.documents
                )
            )
            st.dataframe(
                [
                    {
                        "Policy term": period.label,
                        "Claims": period.claims,
                        "Open": period.open_claims,
                        "Paid": _money(period.totals["paid_total"]),
                        "Reserves": _money(period.totals["reserve_total"]),
                        "Incurred": _money(period.totals["incurred_total"]),
                    }
                    for period in account.periods
                ],
                hide_index=True, width="stretch",
            )

            if account.developed:
                st.markdown("**Claims that moved between valuations**")
                st.dataframe(
                    [
                        {
                            "Claim #": history.claim_number,
                            "Loss date": (
                                history.current.date_of_loss.isoformat()
                                if history.current.date_of_loss else "—"
                            ),
                            "Incurred now": _money(history.current.incurred_total),
                            "Movement": _money(history.development),
                            "Runs": len(history.appearances),
                        }
                        for history in account.developed
                    ],
                    hide_index=True, width="stretch",
                )

            if account.dropped:
                st.caption(
                    f"{len(account.dropped)} claim(s) appear in an earlier run but "
                    f"not in a later one covering the same term: "
                    + ", ".join(h.claim_number for h in account.dropped[:8])
                    + ". Confirm they were closed and purged rather than left out."
                )

            st.download_button(
                "Download account workbook",
                data=export_module.account_to_bytes(account),
                file_name=f"{account.name.replace(' ', '_')}_loss_history.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"account-dl-{account.name}",
            )


def screen_review(document_id: str, result: ExtractionResult) -> None:
    if result.mapping.source == "profile":
        st.info(
            f"Mapped automatically from a saved **{result.document.carrier or 'carrier'}** "
            f"format — no mapping step was needed for this document. Saved "
            f"formats are shown at the bottom of the queue."
        )
    _reconciliation_card(result)

    with st.expander("Extraction details"):
        _document_facts(result)
        _document_notes(result)

    _period_summary(result.document)
    _loss_snapshot(result.document)

    findings = result.reconciliation.findings
    with st.expander(
        f"Exceptions ({len(findings)})" if findings else "Exceptions (none)",
        expanded=bool(findings),
    ):
        _findings_table(result)

    _review_status_bar(result)

    with st.expander("Review findings", expanded=bool(result.reconciliation.findings)):
        _review_workspace(result, document_id)

    with st.expander("Source evidence", expanded=False):
        _evidence_panel(result, document_id)

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
    _reconciliation_card(result)

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
