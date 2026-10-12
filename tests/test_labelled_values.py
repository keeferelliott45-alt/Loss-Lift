"""A labelled letterhead value ends where the next labelled fact begins.

A header band prints several labelled facts on one line. The value after
"Policy Number:" ran on until a short list of known labels ("policy",
"valuation", ...) and so swallowed any other: a real loss run's policy number
was read as "ESP730024901 Report run date: Mar 7, 2019 9:16:54 AM".

A value now also stops before any following label -- a few words ending in a
colon. A value with no colon after it is read exactly as before.
"""

from __future__ import annotations

import pytest

from core.extract_digital import extract_metadata


@pytest.mark.parametrize("line, expected", [
    ("Policy Number: ESP730024901 Report run date: Mar 7, 2019 9:16:54 AM", "ESP730024901"),
    ("Policy No: GL-7003 Printed By: jdoe", "GL-7003"),
    ("Policy #: 44-918 Account Manager: R Lane", "44-918"),
])
def test_the_policy_number_stops_at_the_next_label(line, expected):
    assert extract_metadata(line).policy_number == expected


def test_the_insured_stops_at_the_next_label():
    text = "Named Insured: Ridgeway Test Freight LLC Report ran by: jdoe"
    assert extract_metadata(text).named_insured == "Ridgeway Test Freight LLC"


@pytest.mark.parametrize("line, field, expected", [
    ("Policy Number: GL 7003 A", "policy_number", "GL 7003 A"),
    ("Named Insured: Smith & Sons, Inc.", "named_insured", "Smith & Sons, Inc."),
    ("Policy Number: GL-7003 Valuation Date: 12/31/2024", "policy_number", "GL-7003"),
    ("Line of Business: General Liability", "line_of_business", "General Liability"),
    ("Named Insured: Ridgeway Freight Printed 09:16:54", "named_insured",
     "Ridgeway Freight Printed 09:16:54"),
])
def test_a_value_without_a_following_label_is_read_as_before(line, field, expected):
    assert getattr(extract_metadata(line), field) == expected
