"""Which printed claim count, if any, is the *document's*.

R-05 compares the extracted row count against a number the carrier printed.
It is one of only two rules that check against the document rather than
against the app's own arithmetic, so a count adopted from the wrong scope does
not merely mislead -- it makes the strongest rule in the engine report a
discrepancy nobody made.

A run grouped by policy prints a count under each group. Illinois shows why
vocabulary cannot settle which one is the report's: it prints seven section
counts and one document total in *identical* wording,

    # Claims: 6 ... # Claims: 9 ... # Claims: 8      (per page)
    # Claims: 50                                     (the report)

and 50 is exactly 6+9+6+8+6+7+8. The document total is the one that adds up.
That is arithmetic the carrier printed, not an inference about labels, and it
is the discriminator used here where the wording gives nothing.

Four rules, in order:

1. A grand/report/overall/final total naming a count says its own scope.
2. Otherwise a document stating exactly one count has stated the document's.
3. Otherwise, if exactly one stated count equals the sum of all the others, it
   totals them.
4. Otherwise the scope is unestablished, and no count is adopted. Agreement is
   not enough: two policies of four claims each agree at four, and the
   document has eight.

The page texts here are synthetic; the arrangements are the ones the reference
corpus and the review's probes exhibit.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core import extract_digital as ed


def _count_from(page_texts: list[str]) -> int | None:
    """The document count ``extract_pdf`` adopts from these printed pages.

    Word and table extraction are stubbed out: this asks only which count the
    metadata path adopts, which is what R-05 later reads.
    """
    pages = [SimpleNamespace(extract_text=lambda t=text: t) for text in page_texts]
    context = MagicMock()
    context.__enter__.return_value = SimpleNamespace(pages=pages)
    with patch.object(ed.pdfplumber, "open", return_value=context), \
            patch.object(ed, "page_words", return_value=[]), \
            patch.object(ed, "extract_page_table", return_value=None):
        return ed.extract_pdf("synthetic.pdf").metadata.printed_claim_count


# --- rule 4: sections alone establish nothing -----------------------------

@pytest.mark.parametrize(
    "case, pages",
    [
        (
            "a section count on the very first page",
            ["Policy A\nClaim Count = 1", "Policy B\nClaim Count = 2"],
        ),
        (
            "equal section counts after a cover page",
            ["Loss run cover", "Policy A\nClaim Count = 4", "Policy B\nClaim Count = 4"],
        ),
        (
            "two section counts on one page",
            ["Loss run cover", "Policy A\nClaim Count = 1\nPolicy B\nClaim Count = 2"],
        ),
        (
            "four sections, none of them the report",
            [
                "Pol-Asco-Mod: 000 Claim Count = 4",
                "Pol-Asco-Mod: 001 Claim Count = 0",
                "Pol-Asco-Mod: 002 Claim Count = 4",
                "Pol-Asco-Mod: 003 Claim Count = 4",
            ],
        ),
    ],
)
def test_policy_sections_alone_leave_the_count_unverified(case, pages):
    """None of these documents ever says how many claims it holds."""
    assert _count_from(pages) is None, case


# --- rules 1-3: a document count that is actually stated -------------------

def test_a_single_stated_count_is_the_documents():
    """The ordinary one-section loss run, and what most fixtures print."""
    assert _count_from(["TOTALS Claims: 2 $17,350.50 $5,000.00 $22,350.50"]) == 2


def test_a_report_total_outranks_the_sections_under_it():
    """Liberty's wording: the label says the scope outright."""
    assert _count_from(
        [
            "Claim Count : 28 $25,297.00",
            "Report Totals Claim Count : 28 $25,297.00 $37,438.75",
        ]
    ) == 28


def test_the_count_that_totals_the_others_is_the_documents():
    """Illinois: identical wording throughout, and 50 is the sum of the rest."""
    assert _count_from(
        [
            "# Claims: 6 $28,055.10",
            "# Claims: 9 $13,292.32",
            "# Claims: 6 $46,791.96",
            "# Claims: 8 $10,214.90",
            "# Claims: 6 $104,068.07",
            "# Claims: 7 $144,537.62",
            "# Claims: 8 $11,233.08\n# Claims: 50 $358,193.05",
        ]
    ) == 50


def test_a_document_total_is_not_lost_to_an_earlier_subtotal():
    """The review's control case, and the one the guard used to fail worst.

    The base commit read the printed 3 here. Adopting the first page's
    subtotal instead turned a correct document total into a false discrepancy
    against three correctly extracted claims.
    """
    assert _count_from(
        ["Policy A\nClaim Count = 1", "Policy B\nClaim Count = 2\nTotal Claims: 3"]
    ) == 3


def test_every_count_on_a_page_is_read_not_just_the_first():
    """Illinois prints a section count and the report total on one page.

    Asserted on the reading rather than on what is adopted, because those are
    different questions: two counts on one page with nothing else to weigh
    them against establish no scope between them, and the arrangement that
    resolves 50 is the whole seven-section document above. What this holds is
    that the second count is *seen* at all -- reading only the first per page
    took the section's 8 and never knew the 50 beneath it existed.
    """
    assert ed.stated_claim_counts(
        "# Claims: 8 $11,233.08\n# Claims: 50 $358,193.05"
    ) == [8, 50]


def test_a_count_matched_by_two_patterns_is_still_one_count():
    """"Total Claims: 3" answers more than one pattern; it is one statement."""
    assert ed.stated_claim_counts("Total Claims: 3") == [3]


# --- the guard must not be sidestepped ------------------------------------

def test_a_first_page_section_count_does_not_short_circuit_the_guard():
    """The count on page 1 is still only a section's.

    The guard ran only where the first page had no recognised count, so a
    document whose first page opened with a policy subtotal never reached it.
    """
    assert _count_from(
        [
            "Pol-Asco-Mod: 000 Claim Count = 4",
            "Pol-Asco-Mod: 001 Claim Count = 9",
        ]
    ) is None


def test_equal_counts_are_not_a_document_total():
    """Two four-claim policies agree at four. The document holds eight."""
    assert _count_from(["Claim Count = 4", "Claim Count = 4"]) is None
