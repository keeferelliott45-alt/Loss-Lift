"""What LossLift accounted for in a document, and what it could not.

One row per logical run -- or one for a document that is a single report --
answering the reviewer's first question before any figure is trusted: how many
claims the run says it holds, how many were read, which claim-like rows were
refused or left unplaced, whether what the run printed about itself ties, and
therefore whether the run can be used as it stands and, if not, why.

Every value comes from the document and its reconciliation as they already
are; nothing here decides anything. Status is the canonical policy
(``core.review``). The review screen and the JSON export both read this, so
they cannot disagree about it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from core.review import blocks_trust, canonical_run_status, canonical_status
from core.schema import DocumentStatus, LossRunDocument, ReconciliationResult


def _span(pages: list[int]) -> str:
    if not pages:
        return ""
    ordered = sorted(set(pages))
    spans, start, last = [], ordered[0], ordered[0]
    for page in ordered[1:]:
        if page == last + 1:
            last = page
            continue
        spans.append(f"{start}" if start == last else f"{start}-{last}")
        start = last = page
    spans.append(f"{start}" if start == last else f"{start}-{last}")
    return ", ".join(spans)


def _tie(present: bool, failed: bool) -> str:
    if not present:
        return "not printed"
    return "does not tie" if failed else "ties"


@dataclass(frozen=True)
class RunAccount:
    """One logical run's accounting."""

    run_id: str | None
    pages: str
    carrier: str | None
    policy_number: str | None
    policy_term_start: date | None
    policy_term_end: date | None
    boundary_settled: bool
    printed_claim_count: int | None
    claims_read: int
    refused_rows: int
    refused_pages: list[int]
    unplaced_rows: int
    unplaced_pages: list[int]
    printed_totals_present: bool
    totals: str
    claim_count: str
    status: DocumentStatus
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        for name in ("policy_term_start", "policy_term_end"):
            data[name] = data[name].isoformat() if data[name] else None
        return data


def claim_accounting(
    document: LossRunDocument, reconciliation: ReconciliationResult | None
) -> list[RunAccount]:
    """One account per logical run; a single report is one run."""
    findings = list(reconciliation.findings) if reconciliation is not None else []

    def account(run_id: str | None, pages: list[int], facts: Any, claims: int,
                printed_totals: dict, printed_count: int | None, settled: bool,
                status: DocumentStatus) -> RunAccount:
        held = set(pages)
        own = [f for f in findings if run_id is None or f.run_id in (None, run_id)]
        refused = [row for row in document.refused_claim_rows if row.page in held]
        unplaced = [row for row in document.unplaced_rows if row.page in held]
        present = any(value is not None for value in printed_totals.values())
        return RunAccount(
            run_id=run_id,
            pages=_span(pages),
            carrier=facts.carrier,
            policy_number=facts.policy_number,
            policy_term_start=facts.policy_period_start,
            policy_term_end=facts.policy_period_end,
            boundary_settled=settled,
            printed_claim_count=printed_count,
            claims_read=claims,
            refused_rows=len(refused),
            refused_pages=sorted({row.page for row in refused}),
            unplaced_rows=len(unplaced),
            unplaced_pages=sorted({row.page for row in unplaced}),
            printed_totals_present=present,
            totals=_tie(present, any(f.rule_id == "R-04" for f in own)),
            claim_count=_tie(printed_count is not None, any(f.rule_id == "R-05" for f in own)),
            status=status,
            reasons=[f"{f.rule_id}: {f.message}" for f in own if blocks_trust(f)],
        )

    if not document.is_packet:
        return [account(
            None, list(range(1, document.page_count + 1)) or
            sorted({claim.source_page for claim in document.claims}),
            document, len(document.claims), document.printed_totals,
            document.printed_claim_count, True, canonical_status(reconciliation),
        )]
    return [
        account(
            run.run_id, run.pages, run, len(document.run_claims(run)), run.printed_totals,
            run.printed_claim_count, not run.ambiguous,
            canonical_run_status(reconciliation, run.run_id),
        )
        for run in document.runs
    ]
