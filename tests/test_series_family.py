"""A claim-number series whose numbers grew a digit.

A pool numbers claims by year and sequence: "AL201406511-1" in 2014,
"AL20158493-2" from 2015. The shapes differ by one digit, and the 2014
series, a fifth of the report, fell below the floor a shape must clear to
count as this document's -- so its 76 claims were refused, reported but never
read.

Shapes that differ only in how long their runs of letters and digits are
belong to one family. A family member recurring at least twice is admitted
beside the admitted shape; a one-off, or a shape of another pattern, is still
refused.

Synthetic PDFs only.
"""

from __future__ import annotations

import pymupdf

from core.pipeline import run_pipeline
from core.records import consensus_shapes, identifier_shape


def test_a_series_that_grew_a_digit_is_one_family():
    later = [f"AL2015{n:04d}-1" for n in range(8400, 8420)]
    earlier = [f"AL2014{n:05d}-1" for n in range(6500, 6504)]
    shapes = consensus_shapes(later + earlier)
    assert identifier_shape(earlier[0]) in shapes


def test_a_one_off_of_the_family_is_still_refused():
    later = [f"AL2015{n:04d}-1" for n in range(8400, 8420)]
    shapes = consensus_shapes([*later, "AL201406511-1"])
    assert identifier_shape("AL201406511-1") not in shapes


def test_another_pattern_is_still_refused():
    later = [f"AL2015{n:04d}-1" for n in range(8400, 8420)]
    other = ["RMP-00370-201407", "RMP-00370-201507", "RMP-00370-201607"]
    shapes = consensus_shapes(later + other)
    assert identifier_shape(other[0]) not in shapes


def test_an_office_code_is_not_a_long_numeric_series():
    numbers = [f"0405121460{n:02d}" for n in range(20)]
    shapes = consensus_shapes([*numbers, "234", "234", "028"])
    assert identifier_shape("234") not in shapes


def _pdf(tmp_path, numbers):
    document = pymupdf.open()
    page = document.new_page(width=612, height=792)
    page.insert_text((40, 40), "RIDGEWAY TEST POOL LOSS RUN", fontsize=9)
    page.insert_text((40, 52), "Valuation Date: 02/18/2019", fontsize=8)
    y = 76.0
    for x, label in ((40, "Claim Number"), (140, "Loss Date"), (210, "Status"),
                     (270, "Paid Total"), (350, "Reserve Total"), (440, "Incurred Total")):
        page.insert_text((x, y), label, fontsize=8)
    y += 12
    for index, number in enumerate(numbers):
        paid = 1000 + 10 * index
        for x, value in ((40, number), (140, f"0{1 + index % 9}/1{index % 9}/2015"),
                         (210, "Closed"), (270, f"{paid:,}.00"), (350, "0.00"),
                         (440, f"{paid:,}.00")):
            page.insert_text((x, y), value, fontsize=8)
        y += 12
    path = tmp_path / "pool.pdf"
    document.save(path)
    document.close()
    return path


def test_both_series_are_read(tmp_path):
    numbers = [f"AL2014{n:05d}-1" for n in range(6500, 6504)] + [
        f"AL2015{n:04d}-1" for n in range(8400, 8420)]
    result = run_pipeline(_pdf(tmp_path, numbers), use_vision=False,
                          profiles_dir=tmp_path / "profiles")
    assert sorted(c.claim_number for c in result.document.claims) == sorted(numbers)
