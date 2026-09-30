"""The scorer, against hand-built extractions: every way a reading can be wrong.

Each extraction here is built directly from LossLift's own models, so a test
states exactly what was read and the score has one right answer.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from core.schema import (
    Claim,
    ClaimStatus,
    DocumentStatus,
    LogicalRun,
    LossRunDocument,
    ReconciliationResult,
    RunConfidence,
)
from tests.qualification.synthetic import document_spec, rows, spec_copy
from tools.qualification.score import combine, score_result
from tools.qualification.truth import parse

CLEAN, REVIEW = DocumentStatus.CLEAN, DocumentStatus.NEEDS_REVIEW
PRINTED = rows(3)  # 71004410 (30,000 open), 71004411, 71004412


def _truth(spec):
    return parse({"version": 1, "documents": [spec]}).documents[0]


def _claim(row, *, page=1, source_row=None, **changes):
    number, loss, status, paid, reserve, incurred = row
    month, day, year = (int(part) for part in loss.split("/"))
    values = dict(
        claim_number=number, date_of_loss=date(year, month, day),
        claim_status=ClaimStatus(status),
        paid_total=Decimal(paid.replace(",", "")),
        reserve_total=Decimal(reserve.replace(",", "")),
        incurred_total=Decimal(incurred.replace(",", "")),
        source_page=page, source_row=source_row,
    )
    values.update(changes)
    return Claim(**values)


def _result(claims, *, status=CLEAN, pages=1, runs=(), run_status=None, mapping=False,
            printed_count=None, printed_totals=None):
    document = LossRunDocument(
        source_filename="synthetic.pdf", file_sha256="0" * 64, page_count=pages,
        claims=list(claims),
        runs=list(runs), printed_claim_count=printed_count,
        printed_totals=printed_totals or {},
    )
    reconciliation = ReconciliationResult(status=status, findings=[],
                                          run_status=run_status or {})
    return SimpleNamespace(document=document, reconciliation=reconciliation,
                           needs_mapping=mapping)


def _score(claims, spec=None, **result):
    return score_result(_result(claims, **result), _truth(spec or document_spec(PRINTED)))


# --------------------------------------------------------------------------
# Claims
# --------------------------------------------------------------------------


def test_an_exact_reading_scores_perfectly_and_is_auto_accepted():
    metrics = _score([_claim(r) for r in PRINTED],
                     printed_totals={"paid_total": Decimal("32000.00"),
                                     "reserve_total": Decimal("0"),
                                     "incurred_total": Decimal("32000.00")})
    claims = metrics.overall.claims
    assert (claims.matched, claims.missing, claims.invented) == (3, 0, 0)
    assert claims.rates()["precision"] == claims.rates()["recall"] == 1.0
    assert metrics.overall.money.rates()["precision"] == 1.0
    assert metrics.overall.critical.correct == 3 * 5
    assert metrics.overall.critical.correct_absent == 3  # recovery blank, read blank
    assert metrics.auto_accepted.claims.matched == 3
    assert metrics.status.false_clean_documents == 0
    assert metrics.printed.errors == 0


def test_a_replaced_claim_is_missed_and_invented_even_when_the_count_ties():
    replaced = [_claim(PRINTED[0]), _claim(PRINTED[1]),
                _claim(("99999999", "03/12/2022", "CLOSED", "1,000.00", "0.00", "1,000.00"))]
    metrics = _score(replaced)
    claims = metrics.overall.claims
    assert claims.extracted == claims.truth == 3  # the count ties
    assert (claims.matched, claims.missing, claims.invented) == (2, 1, 1)
    assert claims.rates()["precision"] == claims.rates()["recall"] == 2 / 3
    assert metrics.status.false_clean_documents == 1
    assert metrics.status.false_clean_documents_errors == 1


def test_money_never_decides_a_match():
    # Same page, same amounts, different identifier: not the same claim.
    other = _claim(PRINTED[2], claim_number="71009999")
    metrics = _score([_claim(PRINTED[0]), _claim(PRINTED[1]), other])
    assert metrics.overall.claims.missing == 1 and metrics.overall.claims.invented == 1
    assert metrics.overall.critical.correct == 2 * 5


def test_a_second_read_of_one_claim_is_an_invented_duplicate():
    metrics = _score([_claim(r) for r in PRINTED] + [_claim(PRINTED[1])])
    claims = metrics.overall.claims
    assert (claims.matched, claims.invented, claims.duplicates) == (3, 1, 1)


def test_two_printed_occurrences_are_two_claims_when_rows_tell_them_apart():
    spec = document_spec(PRINTED)
    twin = spec_copy(spec["claims"][1])
    spec["claims"][1]["anchor"]["row"] = 5
    twin["anchor"]["row"] = 9
    spec["claims"].append(twin)
    read_one = _score([_claim(PRINTED[0]), _claim(PRINTED[1], source_row=5),
                       _claim(PRINTED[2])], spec)
    assert (read_one.overall.claims.matched, read_one.overall.claims.missing) == (3, 1)
    read_both = _score([_claim(PRINTED[0]), _claim(PRINTED[1], source_row=5),
                        _claim(PRINTED[1], source_row=9), _claim(PRINTED[2])], spec)
    assert (read_both.overall.claims.matched, read_both.overall.claims.missing) == (4, 0)


def test_occurrences_nothing_can_tell_apart_stay_visible_and_unscored():
    spec = document_spec(PRINTED)
    spec["claims"].append(spec_copy(spec["claims"][1]))  # same number, page, no rows
    metrics = _score([_claim(r) for r in PRINTED] + [_claim(PRINTED[1])], spec)
    claims = metrics.overall.claims
    assert claims.ambiguous_truth == 2 and claims.ambiguous_extracted == 2
    assert (claims.matched, claims.missing, claims.invented) == (2, 0, 0)
    assert claims.rates()["scored_coverage"] == 2 / 4


def test_a_claim_read_on_the_wrong_page_is_misplaced_not_matched():
    metrics = _score([_claim(PRINTED[0]), _claim(PRINTED[1], page=2), _claim(PRINTED[2])],
                     pages=2, spec=_two_pages())
    claims = metrics.overall.claims
    assert (claims.matched, claims.missing, claims.invented, claims.misplaced) == (2, 1, 1, 1)


def _two_pages():
    spec = document_spec(PRINTED)
    spec["page_count"] = 2
    spec["pages"].append({"page": 2, "run": None, "role": "claim_free"})
    return spec


def _two_runs():
    spec = document_spec(PRINTED)
    spec["page_count"] = 2
    spec["claims"][2]["anchor"]["page"] = 2
    spec["pages"] = [{"page": 1, "run": "r1", "role": "claims"},
                     {"page": 2, "run": "r2", "role": "claims"}]
    spec["runs"] = [
        {"id": "r1", "pages": [1], "status": "CLEAN",
         "printed": {"claim_count": {"state": "absent"}}},
        {"id": "r2", "pages": [2], "status": "CLEAN",
         "printed": {"claim_count": {"state": "absent"}}},
    ]
    return spec


def _run(run_id, pages):
    return LogicalRun(run_id=run_id, pages=pages, confidence=RunConfidence.PRINTED)


def test_two_reports_read_as_one_are_a_run_error_and_not_clean():
    read = [_claim(PRINTED[0]), _claim(PRINTED[1]), _claim(PRINTED[2], page=2)]
    merged = _score(read, _two_runs(), pages=2)
    assert merged.accounting.runs_matched == 0 and merged.accounting.truth_runs == 2
    assert merged.overall.claims.wrong_run == 3
    assert merged.status.false_clean_documents == 1

    split = _score(read, _two_runs(), pages=2,
                   runs=[_run("run-1", [1]), _run("run-2", [2])],
                   run_status={"run-1": CLEAN, "run-2": CLEAN})
    assert split.accounting.runs_matched == 2 and split.overall.claims.wrong_run == 0
    assert split.status.runs_compared == 2 and split.status.false_clean_runs == 0


# --------------------------------------------------------------------------
# Fields
# --------------------------------------------------------------------------


def test_swapped_money_is_two_errors_even_though_the_total_ties():
    swapped = _claim(PRINTED[0], paid_total=Decimal("0"), reserve_total=Decimal("30000"))
    metrics = _score([swapped, _claim(PRINTED[1]), _claim(PRINTED[2])])
    money = metrics.overall.money
    assert money.incorrect == 2 and money.correct == 3 * 3 - 2
    assert money.rates()["precision"] < 1 and money.rates()["recall"] < 1
    assert metrics.status.false_clean_documents == 1


def test_a_recovery_read_with_the_wrong_sign_is_wrong():
    spec = document_spec(PRINTED)
    spec["claims"][0]["fields"]["recovery_total"] = "-138.26"
    right = _score([_claim(PRINTED[0], recovery_total=Decimal("-138.26")),
                    _claim(PRINTED[1]), _claim(PRINTED[2])], spec)
    wrong = _score([_claim(PRINTED[0], recovery_total=Decimal("138.26")),
                    _claim(PRINTED[1]), _claim(PRINTED[2])], spec)
    assert right.overall.money.incorrect == 0
    assert wrong.overall.money.incorrect == 1


def test_null_and_zero_are_different_facts():
    spec = document_spec(PRINTED)
    spec["claims"][1]["fields"]["reserve_total"] = "0.00"  # printed zero
    read = [_claim(PRINTED[0], recovery_total=Decimal("0")),  # zero into a blank
            _claim(PRINTED[1], reserve_total=None),             # printed zero read as nothing
            _claim(PRINTED[2])]
    metrics = _score(read, spec)
    critical = metrics.overall.critical
    assert critical.null_as_zero == 1 and critical.extra == 1
    assert critical.zero_as_null == 1 and critical.missing == 1
    assert metrics.must_be_zero["null_as_zero"] == 1


def test_an_unreadable_identifier_is_neither_missed_nor_invented():
    spec = document_spec(PRINTED)
    spec["claims"][2]["identity"] = "ambiguous"
    del spec["claims"][2]["claim_number"]
    metrics = _score([_claim(PRINTED[0]), _claim(PRINTED[1]),
                      _claim(PRINTED[2], claim_number="7I0O4412")], spec)
    claims = metrics.overall.claims
    assert (claims.matched, claims.missing, claims.invented) == (2, 0, 0)
    assert claims.ambiguous_truth == 1 and claims.ambiguous_extracted == 1


def test_ambiguous_and_unscorable_labels_are_counted_not_scored():
    spec = document_spec(PRINTED)
    spec["claims"][0]["fields"]["paid_total"] = {"state": "ambiguous"}
    spec["claims"][0]["fields"]["date_of_loss"] = {"state": "unscorable"}
    metrics = _score([_claim(r) for r in PRINTED], spec)
    assert metrics.overall.critical.unscored == 2
    assert metrics.overall.money.unscored == 1


# --------------------------------------------------------------------------
# Status, acceptance and empty denominators
# --------------------------------------------------------------------------


def test_a_clean_engine_with_a_mapping_pending_is_not_auto_accepted():
    metrics = _score([_claim(r) for r in PRINTED], mapping=True)
    assert metrics.status.pending_mapping == 1
    assert metrics.status.documents_auto_accepted == 0
    assert metrics.auto_accepted.claims.matched == 0
    assert metrics.status.false_clean_documents == 0


def test_clean_on_a_document_that_needs_review_is_false_clean():
    metrics = _score([_claim(r) for r in PRINTED], document_spec(PRINTED, status="NEEDS_REVIEW"))
    assert metrics.status.false_clean_documents_status == 1
    assert metrics.status.documents_agree == 0


def test_errors_in_a_reviewed_document_are_measured_but_not_false_clean():
    metrics = _score([_claim(PRINTED[0]), _claim(PRINTED[1])], status=REVIEW)
    assert metrics.overall.claims.missing == 1
    assert metrics.status.false_clean_documents == 0
    assert metrics.auto_accepted.claims.truth == 0  # nothing here was accepted


def test_nothing_to_count_is_no_rate_not_a_perfect_one():
    spec = document_spec(PRINTED)
    spec["claims"] = []
    metrics = _score([], spec)
    rates = metrics.overall.claims.rates()
    assert rates["precision"] is None and rates["recall"] is None
    assert metrics.as_dict()["overall"]["claims"]["matched"] == 0


def test_documents_combine_by_adding_counts():
    perfect = _score([_claim(r) for r in PRINTED])
    missing = _score([_claim(PRINTED[0])])
    total = combine([perfect, missing])
    assert total.documents == 2
    assert (total.overall.claims.matched, total.overall.claims.missing) == (4, 2)
    assert total.overall.claims.rates()["recall"] == 4 / 6


def test_a_provisional_truth_does_not_count_as_qualification():
    spec = document_spec(PRINTED)
    spec["adjudication"] = "provisional"
    assert _score([_claim(r) for r in PRINTED], spec).qualified == 0
