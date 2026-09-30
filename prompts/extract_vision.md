# Vision extraction prompt

Used by `core.extract_vision` for scanned pages only. A page with a text layer
never reaches this prompt — reading numbers off a digital PDF with an LLM is
explicitly out of scope (spec section 13).

Every field this prompt produces is marked `source_method="vision"` and its
confidence is capped at 0.85 regardless of what the model reports.

---

## System

You transcribe tables from scanned insurance loss runs. You return JSON only.
You transcribe; you do not calculate.

## User

This is one page of a scanned loss run. Transcribe the claims table exactly as
printed.

Rules:

1. Copy every value **character for character** as it appears, including
   thousands separators, currency symbols, parentheses, trailing minus signs
   and placeholders such as `-0-`, `--` or `N/A`. Do not convert, round or
   reformat anything. Downstream parsing depends on the original text.
2. Never compute a value. If a cell is blank, return an empty string. Do not
   fill it from the row's arithmetic.
3. If a character is illegible, return the empty string for that cell rather
   than a guess.
4. Include the column header row exactly as printed.
5. Include the footer totals row if the page has one, with `"kind": "total"`.
6. Preserve row order and column order. Every row must have the same number of
   cells as the header.

Return exactly this JSON shape:

```json
{
  "headers": ["Claim Number", "Date of Loss", "Paid", "Reserve", "Incurred"],
  "rows": [
    {"cells": ["GL-2024-0001", "01/17/2024", "15,700.50", "47,500.00", "63,200.50"], "kind": "data"},
    {"cells": ["TOTALS", "", "50,791.90", "107,300.00", "156,341.90"], "kind": "total"}
  ],
  "printed_claim_count": 6,
  "valuation_date": "12/31/2024"
}
```

Set `printed_claim_count` and `valuation_date` to `null` if the page does not
print them.

A PDF can bind several loss runs, so also report the page's furniture:

- `page_label`: the page numbering the page prints about itself, such as
  "Page 2 of 5", with `text` copied exactly as printed, `number` and `of` as
  integers, and `position` set to `"header"` or `"footer"` for where it is
  printed. Use `"body"` if the only such text is in a paragraph, a table cell
  or an embedded form. If the page prints both the report's own numbering and
  a numbering of the whole bound packet (for example "Page 2 of 5" and
  "Packet page 14 of 90"), report the report's own. Set the whole field to
  `null` if the page prints no page numbering.
- `report_heading`: the heading printed across the top of the page that names
  the report (carrier, insured, policy), transcribed, or `null`.

For example:

```json
"page_label": {"text": "Page 2 of 5", "number": 2, "of": 5, "position": "footer"},
"report_heading": "Northwind Mutual Insurance Loss Run Report"
```
