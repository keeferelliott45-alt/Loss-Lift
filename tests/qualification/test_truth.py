"""The private truth format: complete, explicit, and silent about its contents."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.qualification.synthetic import document_spec, rows, spec_copy
from tools.corpus_gate.manifest import Entry
from tools.qualification.truth import (
    CRITICAL_FIELDS,
    LabelState,
    TruthError,
    check_against_manifest,
    parse,
)

SECRET_NUMBER = "SECRET-CLAIM-9911"


def _set(documents):
    return {"version": 1, "documents": documents}


def _valid():
    return document_spec(rows(3))


def test_a_complete_truth_parses_with_its_label_states():
    truth = parse(_set([_valid()]))
    (document,) = truth.documents
    assert document.qualifies and len(document.claims) == 3
    claim = document.claims[0]
    assert claim.fields["paid_total"].value == Decimal("30000.00")
    assert claim.fields["recovery_total"].state is LabelState.ABSENT
    assert set(CRITICAL_FIELDS) <= set(claim.fields)


def test_money_written_as_a_json_number_is_refused():
    spec = _valid()
    spec["claims"][0]["fields"]["paid_total"] = 1000.5
    with pytest.raises(TruthError, match="not a JSON number|floats are refused"):
        parse(_set([spec]))


@pytest.mark.parametrize("value", ["NaN", "Infinity", "12,000.00", "twelve"])
def test_money_must_be_a_finite_decimal_string(value):
    spec = _valid()
    spec["claims"][0]["fields"]["incurred_total"] = value
    with pytest.raises(TruthError):
        parse(_set([spec]))


@pytest.mark.parametrize("field", CRITICAL_FIELDS)
def test_every_critical_field_must_be_labelled(field):
    spec = _valid()
    del spec["claims"][1]["fields"][field]
    with pytest.raises(TruthError, match="critical fields not labelled"):
        parse(_set([spec]))


def test_null_is_not_a_label():
    spec = _valid()
    spec["claims"][0]["fields"]["recovery_total"] = None
    with pytest.raises(TruthError, match="null is not a label"):
        parse(_set([spec]))


def test_known_absent_ambiguous_and_unscorable_are_distinct():
    spec = _valid()
    fields = spec["claims"][0]["fields"]
    fields["paid_total"] = {"state": "known", "value": "0.00"}
    fields["reserve_total"] = {"state": "absent"}
    fields["recovery_total"] = {"state": "ambiguous"}
    fields["incurred_total"] = {"state": "unscorable"}
    labels = parse(_set([spec])).documents[0].claims[0].fields
    assert [labels[n].state for n in ("paid_total", "reserve_total", "recovery_total",
                                      "incurred_total")] == [
        LabelState.KNOWN, LabelState.ABSENT, LabelState.AMBIGUOUS, LabelState.UNSCORABLE]
    assert labels["paid_total"].value == Decimal("0")


def test_every_page_must_be_placed():
    spec = _valid()
    spec["page_count"] = 2
    with pytest.raises(TruthError, match="pages are missing"):
        parse(_set([spec]))


def test_a_page_holding_claims_must_belong_to_a_declared_run():
    spec = _valid()
    spec["runs"] = [{"id": "r1", "pages": [1], "status": "CLEAN",
                     "printed": {"claim_count": {"state": "absent"}}}]
    with pytest.raises(TruthError, match="run r1 and the page list disagree"):
        parse(_set([spec]))
    spec["pages"][0]["run"] = "r1"
    assert parse(_set([spec])).documents[0].runs[0].pages == (1,)


def test_statuses_and_adjudication_must_be_stated():
    for key, bad in (("status", None), ("adjudication", "maybe"), ("format_family", "fax")):
        spec = _valid()
        spec[key] = bad
        with pytest.raises(TruthError):
            parse(_set([spec]))


def test_a_claim_cannot_sit_on_a_page_the_truth_says_holds_none():
    spec = _valid()
    spec["pages"][0]["role"] = "claim_free"
    with pytest.raises(TruthError, match="holds no claims"):
        parse(_set([spec]))


def test_two_occurrences_cannot_share_an_exact_anchor():
    spec = _valid()
    spec["claims"][0]["anchor"] = {"page": 1, "row": 4}
    spec["claims"][1] = spec_copy(spec["claims"][0])
    with pytest.raises(TruthError, match="repeats another occurrence's anchor"):
        parse(_set([spec]))


def test_an_ambiguous_identity_carries_no_number():
    spec = _valid()
    spec["claims"][0]["identity"] = "ambiguous"
    with pytest.raises(TruthError, match="carries no claim number"):
        parse(_set([spec]))
    del spec["claims"][0]["claim_number"]
    claim = parse(_set([spec])).documents[0].claims[0]
    assert claim.identity is LabelState.AMBIGUOUS and claim.claim_number is None


def test_truth_for_other_bytes_is_refused():
    truth = parse(_set([_valid()]))
    entry = Entry(id="doc-synthetic", path="a.pdf", sha256="1" * 64, bytes=10)
    with pytest.raises(TruthError, match="other bytes"):
        check_against_manifest(truth, [entry])
    with pytest.raises(TruthError, match="not in the manifest"):
        check_against_manifest(truth, [])


def test_an_error_message_never_repeats_a_value():
    spec = _valid()
    spec["claims"][0]["claim_number"] = SECRET_NUMBER
    spec["claims"][0]["fields"]["paid_total"] = "99887766.55x"
    del spec["claims"][0]["fields"]["claim_status"]
    for mutate in (lambda s: None,
                   lambda s: s["claims"][0]["fields"].__setitem__("claim_status", "OPENISH")):
        candidate = spec_copy(spec)
        mutate(candidate)
        with pytest.raises(TruthError) as caught:
            parse(_set([candidate]))
        message = str(caught.value)
        assert SECRET_NUMBER not in message and "99887766" not in message
        assert "OPENISH" not in message
