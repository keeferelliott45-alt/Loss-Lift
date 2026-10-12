"""The public report: sealed ids, fixed categories, and a guard against leaks."""

from __future__ import annotations

import pytest

from tools.qualification import public
from tools.qualification.score import QualificationMetrics


def _report(records=None):
    return public.build_report(commit="a" * 40, manifest_sha256="b" * 64,
                               truth_sha256="c" * 64, records=records or [],
                               totals=QualificationMetrics().as_dict(), vision_replay=False)


def test_a_sealed_id_is_stable_under_one_salt_and_differs_under_another():
    one, two = bytes(range(32)), bytes(range(1, 33))
    assert public.seal("doc-abc", one) == public.seal("doc-abc", one)
    assert public.seal("doc-abc", one) != public.seal("doc-abc", two)
    assert "abc" not in public.seal("doc-abc", one)


def test_only_fixed_categories_are_accepted():
    with pytest.raises(ValueError):
        public.document_record("doc:1", "crashed with details", qualified=True)


def test_the_leak_guard_refuses_any_private_string():
    report = _report([public.document_record("doc:0011", "scored", qualified=True)])
    public.check_public(report, ["CLM-778899", "Some Insured LLC"])
    report["per_document"][0]["note"] = "clm-778899"
    with pytest.raises(public.PrivateDataInReport):
        public.check_public(report, ["CLM-778899"])


def test_no_documents_is_insufficient_and_every_rate_is_unknown():
    report = _report()
    assert report["qualification"] == "insufficient"
    text = public.render(report)
    assert "precision n/a recall n/a" in text
