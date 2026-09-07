"""Which printed claim count, if any, is the *document's*.

R-05 compares the extracted row count against a number the carrier printed.
It is one of only two rules that check against the document rather than
against the app's own arithmetic, so a count adopted from the wrong scope does
not merely mislead -- it makes the strongest rule in the engine report a
discrepancy nobody made.

A run grouped by policy prints a count under each group, and any of them will
look like an answer. Only wording that states a scope is trusted:

1. A grand, report, overall or final total naming a count says its own scope.
   Illinois is read here -- it prints seven section counts and its report total
   in identical "# Claims: N" wording, and what separates the 50 is the
   "Report Totals:" label above it, not the figure.
2. Otherwise a document stating exactly one count anywhere has stated its own,
   having no other section to be confused with.
3. Otherwise a single count introduced as a total ("Total Claims: 3") where
   every other count is written in the section form ("Claim Count = 4").
4. Otherwise the scope is unestablished and no count is adopted.

Arithmetic is never consulted. An earlier version of this file asserted that
the one count equalling the sum of the others was the report's; 50 really is
6+9+6+8+6+7+8 on Illinois, but sections of one, two and three claims print a 3
that is also 1+2, and by number alone the coincidence cannot be told from a
real total. Agreement is no better: two policies of four claims each agree at
four while the document holds eight.

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


#: Illinois's seven section counts and its report total, in the identical
#: "# Claims: N" wording the document really uses.
_ILLINOIS_SECTIONS = [
    "# Claims: 6 $28,055.10",
    "# Claims: 9 $13,292.32",
    "# Claims: 6 $46,791.96",
    "# Claims: 8 $10,214.90",
    "# Claims: 6 $104,068.07",
    "# Claims: 7 $144,537.62",
]


def test_identical_wording_alone_leaves_the_count_unresolved():
    """50 is 6+9+6+8+6+7+8, and that is not evidence.

    This test previously asserted the opposite: that the one count equalling
    the sum of the others could be taken as the report's. It cannot. Sections
    holding one, two and three claims print a 3 that is also 1+2, and by number
    alone the coincidence is indistinguishable from a real report total --
    adopting it reports six correctly extracted claims as a discrepancy on
    R-05. Arithmetic is no longer consulted; the wording below is.
    """
    assert _count_from(
        _ILLINOIS_SECTIONS + ["# Claims: 8 $11,233.08\n# Claims: 50 $358,193.05"]
    ) is None


def test_illinois_report_label_establishes_its_count():
    """What the real document prints, and why Illinois still ties 50/50.

    Page 7 carries the section's own count and then "Report Totals:" over
    "# Claims: 50". The label states the scope; nothing is inferred from the
    figures.
    """
    assert _count_from(
        _ILLINOIS_SECTIONS
        + ["# Claims: 8 $11,233.08\nReport Totals:\n# Claims: 50 $358,193.05"]
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
    assert [count for count, _total_led in ed.stated_claim_counts(
        "# Claims: 8 $11,233.08\n# Claims: 50 $358,193.05"
    )] == [8, 50]


def test_a_count_matched_by_two_patterns_is_still_one_count():
    """"Total Claims: 3" answers more than one pattern; it is one statement."""
    assert ed.stated_claim_counts("Total Claims: 3") == [(3, True)]
    # And the section form is recorded as what it is.
    assert ed.stated_claim_counts("Claim Count = 4") == [(4, False)]


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
