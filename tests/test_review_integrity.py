"""What a reviewer's decision is attached to, and what it survives.

The workflow tests next door prove that reviewing is not reconciling. These
prove the quieter half: that a decision lands on the thing the reviewer was
looking at, and stops applying when that thing changes underneath it.

Both failures are silent by nature. A dismissal that resolves three findings
instead of one leaves a tidier screen, not an error. A correction that lands on
the row below overwrites a carrier figure with a number meant for a different
claim, and the audit trail records it in good faith. Neither shows up in the
reconciliation status, which is exactly why they are tested here.

The identity everything hangs on is the physical one: a claim is the lines it
was read from, and a finding is about a claim or about a named column. Both
survive an edit, because neither is made of the values a reviewer can change.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.pipeline import (
    apply_edits,
    rerun_reconciliation,
    resolve_finding,
    review_columns,
    run_pipeline,
    to_records,
)
from core.review import ReviewAction, finding_key, summarise_review
from core.schema import SourceMethod


@pytest.fixture()
def broken(golden_dir):
    return run_pipeline(golden_dir / "arithmetic_error.pdf", use_vision=False)


def _finding(result, rule_id: str):
    return next(f for f in result.reconciliation.findings if f.rule_id == rule_id)


def _collide(result, onto: str) -> str:
    """Rename one innocent claim so two physical rows share a number."""
    records = to_records(result.document, review_columns(result.document))
    victim = next(row for row in records if row["claim_number"] != onto)
    victim["claim_number"] = onto
    result.document = apply_edits(result.document, records)
    result.reconciliation = rerun_reconciliation(result.document)
    return victim["claim_number"]


# --------------------------------------------------------------------------
# A claim is the lines it was read from
# --------------------------------------------------------------------------


def test_every_claim_says_which_lines_it_came_from(broken):
    ids = [claim.row_id for claim in broken.document.claims]
    assert all(ids), "a claim read off a page can always name where"
    assert len(set(ids)) == len(ids), "two rows must never share an identity"
    for claim in broken.document.claims:
        assert str(claim.source_page) in claim.row_id


def test_row_identity_survives_an_edit_of_every_visible_field(broken):
    before = [claim.row_id for claim in broken.document.claims]
    records = to_records(broken.document, review_columns(broken.document))
    for row in records:
        row["claim_number"] = f"RENAMED-{row['claim_number']}"
        row["incurred_total"] = 1.0
    after = [claim.row_id for claim in apply_edits(broken.document, records).claims]
    assert after == before


def test_a_hand_added_row_says_it_was_added(broken):
    records = to_records(broken.document, review_columns(broken.document))
    blank = {name: None for name in records[0]}
    blank["claim_number"] = "TYPED-1"
    records.append(blank)
    added = apply_edits(broken.document, records).claims[-1]
    assert added.row_id
    assert "added" in added.row_id
    assert added.where() == "added on the review screen"


def test_row_identity_reads_as_a_place_on_the_page(broken):
    claim = broken.document.claims[0]
    assert claim.where() == f"page {claim.source_page}, line {claim.source_row + 1}"


# --------------------------------------------------------------------------
# One decision, one finding
# --------------------------------------------------------------------------


def _three_contested_columns(document):
    """Liberty's shape: three printed columns that could all be the incurred.

    "Incurred Medical", "Incurred Expense" and "Total Incurred" all read as the
    incurred total; one wins and R-21 says so about the other two, plus the one
    that won. Real, and reproduced structurally rather than from the document.
    """
    from core.schema import ColumnMappingRecord, MappingState

    document.column_mapping = [
        ColumnMappingRecord(
            source_index=index,
            source_header_raw=label,
            state=MappingState.AMBIGUOUS,
            contested_field="incurred_total",
        )
        for index, label in (
            (7, "Incurred Medical"),
            (8, "Incurred Expense"),
            (9, "Total Incurred"),
        )
    ]
    return document


def test_two_findings_about_different_columns_are_different_findings(broken):
    from core.reconcile import reconcile

    contested = [
        f for f in reconcile(_three_contested_columns(broken.document)).findings
        if f.rule_id == "R-21"
    ]
    assert len(contested) == 3
    assert len({finding_key(f) for f in contested}) == 3
    assert all("column" in f.subject for f in contested)


def test_dismissing_one_contested_column_leaves_the_others_open(broken):
    from core.reconcile import reconcile

    broken.reconciliation = reconcile(_three_contested_columns(broken.document))
    contested = [f for f in broken.reconciliation.findings if f.rule_id == "R-21"]
    medical = next(f for f in contested if "Medical" in f.message)
    total = next(f for f in contested if "Total Incurred" in f.message)

    resolved = resolve_finding(broken, medical, ReviewAction.DISMISSED, note="not the one")
    log = resolved.document.review_log
    assert log.is_resolved(medical)
    assert not log.is_resolved(total), (
        "dismissing one contested column must not answer for the incurred column"
    )
    summary = summarise_review(resolved.reconciliation.findings, log)
    assert summary.outstanding >= 2


# --------------------------------------------------------------------------
# A correction lands on the row the finding was about
# --------------------------------------------------------------------------


def test_a_correction_lands_on_the_row_the_finding_named(broken):
    """Two rows sharing a claim number must not share a reviewer's decision.

    Carriers do print the same claim number twice, and a reviewer correcting
    the row that fails R-01 must not overwrite a carrier figure on the row that
    does not. Finding the claim by its number picks whichever comes first.
    """
    failing = _finding(broken, "R-01")
    innocent_before = {
        claim.row_id: claim.incurred_total
        for claim in broken.document.claims
        if claim.claim_number != failing.claim_number
    }
    _collide(broken, failing.claim_number)
    failing = next(
        f for f in broken.reconciliation.findings
        if f.rule_id == "R-01" and f.field == "incurred_total"
    )
    result = resolve_finding(
        broken, failing, ReviewAction.CORRECTED, corrected_value="31400.00"
    )
    for claim in result.document.claims:
        if claim.row_id in innocent_before:
            assert claim.incurred_total == innocent_before[claim.row_id], (
                f"{claim.row_id} was not the row the finding was about"
            )


def test_the_audit_trail_names_the_row_that_changed(broken):
    failing = _finding(broken, "R-01")
    result = resolve_finding(
        broken, failing, ReviewAction.CORRECTED, corrected_value="31400.00"
    )
    entry = result.document.review_log.entries[-1]
    changed = next(
        claim for claim in result.document.claims
        if claim.claim_number == failing.claim_number
    )
    assert entry.row_id == changed.row_id
    assert entry.where == changed.where()


def test_correcting_a_claim_number_records_the_change_it_made(broken):
    """R-11 is the duplicate-claim-number rule, so its correction renames a row.

    Reading the result back by the old number finds the other duplicate and
    reports that nothing changed.
    """
    _collide(broken, broken.document.claims[0].claim_number)
    duplicate = _finding(broken, "R-11")
    with pytest.raises(ValueError, match="multiple physical rows"):
        resolve_finding(
            broken, duplicate, ReviewAction.CORRECTED, corrected_value="NEW-999"
        )
    records = to_records(broken.document, review_columns(broken.document))
    selected_id = records[0]["_id"]
    records[0]["claim_number"] = "NEW-999"
    document = apply_edits(broken.document, records)
    entry = document.review_log.entries[-1]
    assert entry.row_id == selected_id
    assert entry.after == "NEW-999"
    assert entry.before != entry.after
    assert entry.changed_a_value
    assert "NEW-999" in [claim.claim_number for claim in document.claims]


# --------------------------------------------------------------------------
# A decision applies to what it was taken about
# --------------------------------------------------------------------------


def test_a_resolution_retires_when_its_finding_changes_materially(broken):
    """Confirming a $10,000 discrepancy is not confirming a $2,223 one."""
    failing = _finding(broken, "R-01")
    original_delta = failing.delta
    result = resolve_finding(broken, failing, ReviewAction.CONFIRMED, note="carrier is right")
    assert result.document.review_log.is_resolved(failing)

    records = to_records(result.document, review_columns(result.document))
    row = next(r for r in records if r["claim_number"] == failing.claim_number)
    row["paid_total"] = float(Decimal(str(row["paid_total"] or 0)) + Decimal("7777"))
    result.document = apply_edits(result.document, records)
    result.reconciliation = rerun_reconciliation(result.document)

    now = next(
        f for f in result.reconciliation.findings
        if f.rule_id == "R-01" and f.claim_number == failing.claim_number
    )
    assert now.delta != original_delta
    assert not result.document.review_log.is_resolved(now), (
        "a decision taken about a different discrepancy must not carry over"
    )
    confirmations = [
        entry for entry in result.document.review_log.entries
        if entry.action is ReviewAction.CONFIRMED
    ]
    assert len(confirmations) == 1, "history is kept, not rewritten"
    assert confirmations[0].delta == str(original_delta)


def test_a_resolution_survives_a_change_that_leaves_the_finding_alone(broken):
    failing = _finding(broken, "R-01")
    result = resolve_finding(broken, failing, ReviewAction.CONFIRMED)
    records = to_records(result.document, review_columns(result.document))
    for row in records:
        row["loss_description"] = "retyped by a reviewer"
    result.document = apply_edits(result.document, records)
    result.reconciliation = rerun_reconciliation(result.document)
    now = next(
        f for f in result.reconciliation.findings
        if f.rule_id == "R-01" and f.claim_number == failing.claim_number
    )
    assert result.document.review_log.is_resolved(now)


# --------------------------------------------------------------------------
# Nothing changes a value without saying so
# --------------------------------------------------------------------------


def test_editing_a_cell_in_the_table_reaches_the_audit_trail(broken):
    """The claims grid is the main editing surface, not a side door."""
    claim = broken.document.claims[0]
    records = to_records(broken.document, review_columns(broken.document))
    records[0]["incurred_total"] = 99999.0
    document = apply_edits(broken.document, records)
    entries = document.review_log.entries
    assert entries, "an edited value with no audit entry is an unrecorded change"
    entry = entries[-1]
    assert entry.row_id == claim.row_id
    assert entry.field == "incurred_total"
    assert entry.before == str(claim.incurred_total)
    assert entry.after == "99999.0"
    assert entry.changed_a_value


def test_deleting_a_row_is_refused_without_losing_carrier_evidence(broken):
    doomed = broken.document.claims[1]
    records = to_records(broken.document, review_columns(broken.document))
    records = [r for r in records if r["claim_number"] != doomed.claim_number]
    before = broken.document.model_dump(mode="json")
    with pytest.raises(ValueError, match="cannot be deleted"):
        apply_edits(broken.document, records)
    assert broken.document.model_dump(mode="json") == before
    assert next(c for c in broken.document.claims if c.row_id == doomed.row_id) == doomed


def test_a_partly_applied_correction_changes_nothing(broken, monkeypatch):
    """Prefer failing visibly over leaving the claims and the rules disagreeing."""
    import core.pipeline as pipeline

    failing = _finding(broken, "R-01")
    claims_before = [claim.model_copy(deep=True) for claim in broken.document.claims]
    reconciliation_before = broken.reconciliation

    def explode(*args, **kwargs):
        raise RuntimeError("the rule engine failed")

    monkeypatch.setattr(pipeline, "rerun_reconciliation", explode)
    with pytest.raises(RuntimeError):
        resolve_finding(broken, failing, ReviewAction.CORRECTED, corrected_value="31400.00")

    assert broken.reconciliation is reconciliation_before
    assert [c.incurred_total for c in broken.document.claims] == [
        c.incurred_total for c in claims_before
    ]
    assert not broken.document.review_log.entries


def test_a_failed_audit_write_does_not_publish_a_correction(broken, monkeypatch):
    from core.schema import ReviewLog

    finding = _finding(broken, "R-01")
    before = broken.document.model_dump(mode="json")
    reconciliation = broken.reconciliation

    def fail_audit(self, entry):
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(ReviewLog, "record", fail_audit)
    with pytest.raises(RuntimeError, match="audit storage"):
        resolve_finding(
            broken, finding, ReviewAction.CORRECTED, corrected_value="31400.00"
        )
    assert broken.document.model_dump(mode="json") == before
    assert broken.reconciliation is reconciliation


def test_evidence_uses_the_physical_row_even_with_duplicate_claim_numbers(broken):
    from core.evidence import finding_evidence

    finding = _finding(broken, "R-01")
    _collide(broken, finding.claim_number)
    finding = _finding(broken, "R-01")
    target = next(c for c in broken.document.claims if c.row_id == finding.subject)
    evidence = finding_evidence(broken.document, finding)
    assert evidence.page == target.source_page
    assert evidence.bbox == target.source_bbox
    assert evidence.text == target.raw_cells.get(finding.field, "")


def test_review_decision_does_not_survive_changed_severity(broken):
    from core.schema import Severity

    finding = _finding(broken, "R-01")
    resolve_finding(broken, finding, ReviewAction.CONFIRMED)
    changed = finding.model_copy(update={"severity": Severity.WARN})
    assert not broken.document.review_log.is_resolved(changed)


def test_money_editor_records_preserve_exact_decimal_spelling(broken):
    amount = Decimal("9007199254740993.0100")
    broken.document.claims[0].incurred_total = amount
    records = to_records(broken.document, review_columns(broken.document))
    assert records[0]["incurred_total"] == "9007199254740993.0100"
    unchanged = apply_edits(broken.document, records)
    assert unchanged.claims[0].incurred_total.as_tuple() == amount.as_tuple()
    assert unchanged.review_log.entries == []


# --------------------------------------------------------------------------
# The workbook says what the screen says
# --------------------------------------------------------------------------


def test_the_workbook_does_not_claim_a_region_the_screen_withdraws(broken):
    """Evidence is confirmed by reading it back, on the screen and on paper.

    A rectangle can be arithmetically perfect and name the wrong row — that is
    why the review screen reads its own highlight back before drawing it. The
    workbook outlives the upload and is the version an underwriter files, so it
    must not be the more confident of the two.
    """
    from core.evidence import EvidenceKind, claim_evidence, confirm_region
    from core.export import build_workbook

    claim = broken.document.claims[0]
    claim.source_bbox = (10.0, 10.0, 60.0, 20.0)  # a region over the letterhead
    onscreen = confirm_region(
        broken.source_path, claim_evidence(claim), claim.claim_number
    )
    assert onscreen.kind is EvidenceKind.PAGE, "the screen withdraws this one"

    sheet = build_workbook(
        broken.document, broken.reconciliation, source_path=broken.source_path
    )["Claim Detail"]
    column = [c.value for c in sheet[1]].index("Source location") + 1
    written = sheet.cell(row=2, column=column).value
    assert "at (" not in written, f"the workbook still points at a region: {written}"
    assert written.startswith("page 1")


def test_the_workbook_names_the_row_a_decision_was_about(broken):
    from core.export import build_workbook

    failing = _finding(broken, "R-01")
    result = resolve_finding(
        broken, failing, ReviewAction.CORRECTED, corrected_value="31400.00"
    )
    sheet = build_workbook(result.document, result.reconciliation)["Review History"]
    headers = [c.value for c in sheet[1]]
    assert "Where" in headers
    written = sheet.cell(row=2, column=headers.index("Where") + 1).value
    assert written == result.document.review_log.entries[-1].where
    assert "page" in written


def test_the_status_columns_do_not_call_review_progress_reconciliation(broken):
    """Dismissing the last flag moves the headline; it does not reconcile it."""
    from core.export import build_workbook

    sheet = build_workbook(broken.document, broken.reconciliation)["Review History"]
    headers = [c.value for c in sheet[1]]
    assert "Reconciliation before" not in headers
    assert "Document status before" in headers
    assert "Document status after" in headers


def test_the_recorded_status_includes_the_decision_it_describes(golden_dir):
    result = run_pipeline(golden_dir / "us_basic.pdf", use_vision=False)
    findings = list(result.reconciliation.findings)
    assert findings, "us_basic raises the unstated-basis warnings"
    for finding in findings:
        result = resolve_finding(result, finding, ReviewAction.DISMISSED, note="ok")
    last = result.document.review_log.entries[-1]
    assert last.status_after == summarise_review(
        result.reconciliation.findings, result.document.review_log
    ).headline()
    assert last.status_after != last.status_before


# --------------------------------------------------------------------------
# A null keeps its reason
# --------------------------------------------------------------------------


def test_correcting_an_unreadable_cell_keeps_why_it_was_unreadable(broken):
    """The reason a figure could not be read is carrier evidence too.

    Overwriting an AMBIGUOUS_SEPARATOR with a typed number answers the
    question; it does not un-ask it. An audit still needs to know the document
    printed something LossLift could not resolve.
    """
    from core.schema import NullReason

    claim = broken.document.claims[0]
    claim.field_issues = {"recovery_total": NullReason.AMBIGUOUS_SEPARATOR}
    claim.raw_cells = {**claim.raw_cells, "recovery_total": "1.234"}

    records = to_records(broken.document, review_columns(broken.document))
    records[0]["recovery_total"] = 1234.0
    corrected = apply_edits(broken.document, records).claims[0]

    assert corrected.recovery_total == Decimal("1234.0")
    assert "recovery_total" not in corrected.field_issues, "the value now reads"
    assert corrected.original_issue_of("recovery_total") is NullReason.AMBIGUOUS_SEPARATOR
    assert corrected.raw_cells["recovery_total"] == "1.234"


def test_correcting_something_a_claim_does_not_store_is_refused(broken):
    """A rule can flag what no cell holds, and no cell is invented for it."""
    from core.schema import Finding, FindingScope, Severity

    invented = Finding(
        rule_id="R-05",
        category="financial",
        scope=FindingScope.CLAIM,
        severity=Severity.ERROR,
        claim_number=broken.document.claims[0].claim_number,
        subject=broken.document.claims[0].row_id,
        field="claim_count",
        message="The printed count does not match the rows extracted.",
    )
    with pytest.raises(ValueError, match="not a\n?\\s*field of a claim"):
        resolve_finding(broken, invented, ReviewAction.CORRECTED, corrected_value="7")
    assert not broken.document.review_log.entries


def test_a_correction_follows_a_row_whose_number_changed(broken):
    """Identity is the row, so renaming a claim does not orphan its findings."""
    failing = _finding(broken, "R-01")
    row = next(
        c for c in broken.document.claims if c.claim_number == failing.claim_number
    ).row_id

    records = to_records(broken.document, review_columns(broken.document))
    renamed = next(r for r in records if r["claim_number"] == failing.claim_number)
    renamed["claim_number"] = "RENUMBERED-1"
    broken.document = apply_edits(broken.document, records)
    broken.reconciliation = rerun_reconciliation(broken.document)

    result = resolve_finding(
        broken, failing, ReviewAction.CORRECTED, corrected_value="31400.00"
    )
    moved = next(c for c in result.document.claims if c.row_id == row)
    assert moved.claim_number == "RENUMBERED-1"
    assert moved.incurred_total == Decimal("31400.00")
    entry = result.document.review_log.entries[-1]
    assert entry.row_id == row
    assert entry.where == moved.where()


def test_typing_the_carriers_own_figure_back_is_still_a_typed_figure(broken):
    """Reverting an edit does not restore the document as the source.

    The value matches what the carrier printed, and a person put it there. The
    two are different facts, and the row keeps saying which is which — with
    both edits standing in the log, since an audit asks what was decided and
    when, not what the last word was.
    """
    claim = broken.document.claims[0]
    carrier = claim.incurred_total

    records = to_records(broken.document, review_columns(broken.document))
    records[0]["incurred_total"] = 120.0
    document = apply_edits(broken.document, records)

    records = to_records(document, review_columns(document))
    records[0]["incurred_total"] = float(carrier)
    document = apply_edits(document, records)

    reverted = document.claims[0]
    assert reverted.incurred_total == carrier
    assert reverted.provenance_of("incurred_total") is SourceMethod.MANUAL
    assert reverted.original_of("incurred_total") == str(carrier)
    assert reverted.raw_cells.get("incurred_total") is not None
    assert len([e for e in document.review_log.entries if e.field == "incurred_total"]) == 2
