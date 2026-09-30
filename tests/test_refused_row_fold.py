"""A claim-like row whose number the vote refused is never folded silently.

Codex P1 on 2e62e69: a row carrying a differently shaped identifier, a loss
date that is day/month ambiguous (``03/04/2022``), no status and no amounts
was not recognised as claim data -- the date was parsed with no date order --
so it was not recorded as refused and its text was folded into the claim
above it. The document read CLEAN with a claim missing.
"""

from __future__ import annotations

from pathlib import Path

from core.review import canonical_status
from core.schema import DocumentStatus
from tests.test_packet_adversarial import LETTER, _read, _total
from tests.test_packet_claim_series import LARGE_CARRIER, _large_run

ODD = "CR-40117"


def _read_with(tmp_path: Path, row: tuple, body: list[tuple] | None = None):
    body = body or _large_run(8)
    return _read(tmp_path, [{"top": (LARGE_CARRIER, *LETTER),
                             "rows": body[:4] + [row] + body[4:],
                             "total": _total(body)}])


def _folded(result) -> list[str]:
    return [c.claim_number for c in result.document.claims
            if c.loss_description and ODD in c.loss_description]


def _accounted(result) -> bool:
    return (ODD in [c.claim_number for c in result.document.claims]
            or ODD in [r.identifier for r in result.document.refused_claim_rows])


def test_an_ambiguous_dated_row_is_recorded_not_folded(tmp_path):
    result = _read_with(tmp_path, (ODD, "03/04/2022", "", "", "", ""))
    assert _folded(result) == []
    assert _accounted(result)
    assert canonical_status(result.reconciliation) is DocumentStatus.NEEDS_REVIEW


def test_a_day_first_document_gives_the_same_guarantee(tmp_path):
    body = [(f"{71004410 + n}", f"{13 + n:02d}/0{1 + n % 9}/2022", "CLOSED",
             "1.000,00", "0,00", "1.000,00") for n in range(8)]
    result = _read(tmp_path, [{"top": (LARGE_CARRIER, *LETTER),
                               "rows": body[:4] + [(ODD, "04/03/2022", "", "", "", "")]
                               + body[4:]}])
    assert _folded(result) == []
    assert _accounted(result)
    assert canonical_status(result.reconciliation) is DocumentStatus.NEEDS_REVIEW


def test_an_unambiguous_dated_row_is_unchanged(tmp_path):
    result = _read_with(tmp_path, (ODD, "03/14/2022", "", "", "", ""))
    assert _folded(result) == []
    assert _accounted(result)
    assert canonical_status(result.reconciliation) is DocumentStatus.NEEDS_REVIEW
