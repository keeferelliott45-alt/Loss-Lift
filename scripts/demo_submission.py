"""Walk a synthetic saved email through the submission workflow, end to end.

    python scripts/demo_submission.py [--out DIR]

It builds a broker email with two readable loss runs, a second copy of one of
them, a spreadsheet and a forwarded email; reads it the way the app does;
prints the inventory, the outstanding items and the loss summary; then sets
the spreadsheet and the forwarded email aside and writes the redacted
submission workbook. Everything is synthetic; staged files are deleted before
it exits.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.eml_intake import read_submission  # noqa: E402
from core.ingest import discard  # noqa: E402
from core.submission import (  # noqa: E402
    OUTCOME_LABELS,
    PROCESSING_LABELS,
    IntakeOutcome,
    status_label,
    summarise_submission,
)
from core.submission_export import submission_filename, submission_to_bytes  # noqa: E402
from tests.submission_fixtures import claim_rows, eml, loss_run_pdf, run  # noqa: E402


def _print_summary(submission, summary) -> None:
    print(f"\nStatus: {status_label(summary)}")
    print(f"Named insured: {summary.named_insured or summary.named_insured_note}")
    for account in summary.accounts:
        total = (f"{account.incurred_total:,.2f}" if account.incurred_total is not None
                 else account.incurred_unavailable)
        print(f"  {account.name}: {account.claims} claims, {account.open_claims} open, "
              f"total incurred {total}")
        print(f"    valuation dates {[d.isoformat() for d in account.valuation_dates]}, "
              f"terms {list(account.policy_periods)}")
        for note in account.period_notes:
            print(f"    {note}")
        for claim in account.large_claims:
            where = submission.attachment(claim.provenance.attachment_id)
            print(f"    large claim {claim.claim_number} {claim.incurred_total:,.2f} "
                  f"-> attachment {where.position}, page {claim.provenance.page}, "
                  f"row {claim.provenance.row}")
    print("Outstanding:" if summary.blockers else "Outstanding: nothing")
    for blocker in summary.blockers:
        print(f"  - {blocker}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None,
                        help="where to write the workbook (default: a temp folder)")
    args = parser.parse_args()

    run_2022 = loss_run_pdf(rows=claim_rows(4, big=38000), valuation="12/31/2022",
                            period="01/01/2022 to 12/31/2022")
    run_2023 = loss_run_pdf(rows=claim_rows(3, start=91000000), policy="GL-200",
                            valuation="12/31/2023", period="01/01/2022 to 12/31/2022")
    forwarded = eml([("older.pdf", "application/pdf", run_2022)], subject="Fwd: older runs")
    raw = eml([
        ("GL loss run 2022.pdf", "application/pdf", run_2022),
        ("GL loss run 2023.pdf", "application/octet-stream", run_2023),
        ("GL loss run 2022 (copy).pdf", "application/pdf", run_2022),
        ("schedule of values.xlsx", "application/vnd.ms-excel", b"PK\x03\x04" + b"\0" * 64),
        ("fwd older runs.eml", "message/rfc822", forwarded),
    ])

    with tempfile.TemporaryDirectory() as scratch:
        submission, done = read_submission(raw, run(Path(scratch) / "profiles"))
        results = {doc_id: result for doc_id, (_s, result) in done.items()}
        try:
            print(f"Submission {submission.submission_id}: "
                  f"{len(submission.attachments)} attachments")
            for a in submission.attachments:
                print(f"  {a.position}. {a.display_filename:<30} {OUTCOME_LABELS[a.outcome]:<9} "
                      f"{PROCESSING_LABELS[a.processing]:<18} {a.reason}")
            _print_summary(submission, summarise_submission(submission, results))

            print("\nA reviewer sets the spreadsheet and the forwarded email aside.")
            for a in submission.attachments:
                if a.outcome in (IntakeOutcome.REJECTED, IntakeOutcome.FAILED):
                    submission.set_attachment_aside(a.attachment_id)
            summary = summarise_submission(submission, results)
            _print_summary(submission, summary)

            out = args.out or Path(tempfile.mkdtemp(prefix="losslift-demo-out-"))
            out.mkdir(parents=True, exist_ok=True)
            target = out / submission_filename(submission)
            target.write_bytes(submission_to_bytes(submission, summary, results, redact=True))
            print(f"\nRedacted submission workbook: {target}")
        finally:
            for staged, _r in done.values():
                discard(staged)
            print(f"Staged files deleted: {all(not s.path.exists() for s, _r in done.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
