"""Synthetic documents and hand-written truth for the qualification tests.

Truth here is authored from how each PDF is built -- the rows it prints --
never from what LossLift reads back.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from tests.submission_fixtures import claim_rows, loss_run_pdf

SHA = "0" * 64


def rows(count: int = 4, *, start: int = 71004410, big: int | None = 30000) -> list[tuple]:
    return claim_rows(count, start=start, big=big)


def _iso(printed: str) -> str:
    return datetime.strptime(printed, "%m/%d/%Y").date().isoformat()


def _money(printed: str) -> str:
    return printed.replace(",", "")


def claim_truth(row: Sequence[str], page: int = 1, row_index: int | None = None) -> dict:
    number, loss, status, paid, reserve, incurred = row
    return {
        "claim_number": number,
        "anchor": {"page": page, **({"row": row_index} if row_index is not None else {})},
        "fields": {
            "date_of_loss": _iso(loss),
            "claim_status": status,
            "paid_total": _money(paid),
            "reserve_total": _money(reserve),
            # The synthetic loss run prints no recovery column: blank, not zero.
            "recovery_total": {"state": "absent"},
            "incurred_total": _money(incurred),
        },
    }


def totals(printed_rows: Sequence[Sequence[str]]) -> dict[str, str]:
    from decimal import Decimal

    def total(column: int) -> str:
        return str(sum(Decimal(_money(r[column])) for r in printed_rows))

    return {"paid_total": total(3), "reserve_total": total(4), "incurred_total": total(5)}


def document_spec(printed_rows: Sequence[Sequence[str]], *, status: str = "CLEAN",
                  doc_id: str = "doc-synthetic", sha256: str = SHA) -> dict[str, Any]:
    """A complete truth document for ``tests.submission_fixtures.loss_run_pdf``."""
    return {
        "id": doc_id,
        "sha256": sha256,
        "format_family": "digital",
        "adjudication": "adjudicated",
        "page_count": 1,
        "pages": [{"page": 1, "run": None, "role": "claims"}],
        "runs": [],
        "status": status,
        "printed": {"claim_count": {"state": "absent"}, "totals": totals(printed_rows)},
        "claims": [claim_truth(row) for row in printed_rows],
    }


def truth_file(documents: list[dict], path: Path) -> Path:
    path.write_text(json.dumps({"version": 1, "documents": documents}), encoding="utf-8")
    return path


def spec_copy(spec: dict) -> dict:
    return copy.deepcopy(spec)


__all__ = ["rows", "claim_truth", "document_spec", "truth_file", "spec_copy", "loss_run_pdf",
           "totals"]
