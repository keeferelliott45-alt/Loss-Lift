"""A column header or a claim row is never a carrier.

A page with no letterhead starts with its table, and ``detect_carrier`` read
that first line as the carrier's name. The column header then named the
profile, the run's facts and the account identity: two readings of one claim
under two "carriers" were kept as two claims, and nothing said the identity
was uncertain. A missing carrier must stay unknown, and unknown keeps the
account's claim identity uncertain.

Synthetic documents only.
"""

from __future__ import annotations

import pymupdf
import pytest

from core.account import build_accounts
from core.pipeline import run_pipeline
from core.profiles import detect_carrier
from tests.test_packet_adversarial import LETTER, _sheet, _total
from tests.test_packet_claim_series import LARGE_CARRIER, LARGE_HEADERS, _large_run

INSURED = "Named Insured: Kestrel Test Logistics LLC"


@pytest.mark.parametrize("text", [
    "Claim No Date of Loss Status Paid Total Total Incurred\nCN-1 01/01/2023",
    "LOSS RUN REPORT\nClaim # Loss Date Status Incurred",
    "Claim Number   Loss Date   Claimant   Paid   Reserve   Incurred",
    "CN-1001 03/12/2024 OPEN 1,200.00 5,000.00",
    " ".join(LARGE_HEADERS),
])
def test_a_column_header_or_claim_row_is_never_the_carrier(text):
    assert detect_carrier(text) is None


@pytest.mark.parametrize("text, carrier", [
    ("Meridian Casualty Company\nClaim No Date of Loss Status Incurred",
     "Meridian Casualty Company"),
    ("STATE AUTO - HISTORICAL LOSS REPORT", "STATE AUTO"),
    ("Great Basin Indemnity  Printed: 3/1/24", "Great Basin Indemnity"),
    ("Workers Compensation Fund", "Workers Compensation Fund"),
    ("Atlantic States Ins Co", "Atlantic States Ins Co"),
    (f"{LARGE_CARRIER}\n" + " ".join(LARGE_HEADERS), LARGE_CARRIER),
])
def test_a_genuine_letterhead_is_still_read_above_its_table(text, carrier):
    assert detect_carrier(text) == carrier


def _pdf(tmp_path, name, sheets):
    document = pymupdf.open()
    for sheet in sheets:
        _sheet(document, **sheet)
    path = tmp_path / name
    document.save(path)
    document.close()
    return path


def _no_letterhead(rows):
    """A report whose page begins with its column header: no carrier printed."""
    return dict(top=(INSURED, "Valuation Date: 12/31/2022"), rows=rows, total=_total(rows),
                bottom=("Page 1 of 1",))


def test_a_report_without_a_letterhead_has_no_carrier(tmp_path):
    rows = _large_run(3)
    result = run_pipeline(_pdf(tmp_path, "bare.pdf", [_no_letterhead(rows)]),
                          use_vision=False, profiles_dir=tmp_path / "profiles")
    carrier = result.document.carrier
    assert carrier is None or not any(label.lower() in carrier.lower()
                                      for label in LARGE_HEADERS), carrier


def test_a_packet_run_without_a_letterhead_keeps_its_carrier_unknown(tmp_path):
    first, second = _large_run(3), _large_run(2)
    path = _pdf(tmp_path, "packet.pdf", [
        dict(top=(LARGE_CARRIER, INSURED, *LETTER), rows=first, total=_total(first),
             bottom=("Page 1 of 1",)),
        _no_letterhead(second),
    ])
    result = run_pipeline(path, use_vision=False, profiles_dir=tmp_path / "profiles")
    for run in result.document.runs:
        if run.carrier is not None:
            assert not any(label.lower() in run.carrier.lower()
                           for label in LARGE_HEADERS), run.carrier


def test_an_unknown_carrier_leaves_a_repeated_claim_uncertain_not_doubled(tmp_path):
    """The same claims, once under a letterhead and once without one: the
    account may not treat the second reading as a different carrier's claims."""
    rows = _large_run(3)
    named = run_pipeline(
        _pdf(tmp_path, "named.pdf", [dict(top=(LARGE_CARRIER, INSURED, *LETTER), rows=rows,
                                          total=_total(rows), bottom=("Page 1 of 1",))]),
        use_vision=False, profiles_dir=tmp_path / "profiles-a")
    bare = run_pipeline(_pdf(tmp_path, "bare.pdf", [_no_letterhead(rows)]),
                        use_vision=False, profiles_dir=tmp_path / "profiles-b")
    (account,) = build_accounts(
        [named.document, bare.document],
        {r.document.document_id: r.reconciliation for r in (named, bare)})
    # Either one history per claim, or the doubt is said out loud.
    assert len(account.histories) == len(rows) or account.uncertain, (
        [h.claim_number for h in account.histories])
