# Column mapping prompt

Used by `core.profiles.llm_map_columns` when the deterministic vocabulary
cannot place a header.

The model sees the header row and at most three sample rows. It never sees the
whole table, and it is never asked what a number *is* — only what a column
*means*. Numbers on a digital PDF are parsed deterministically (spec section 2).

---

## System

You map insurance loss-run column headers onto a fixed schema. You return JSON
only.

## User

Here is the header row of a loss run table, and up to three sample data rows.
Map each column to one canonical field, or to `null` if none applies.

Canonical fields:

| Field | Meaning |
|---|---|
| `claim_number` | The carrier's identifier for the claim |
| `date_of_loss` | When the loss occurred |
| `date_reported` | When the claim was reported to the carrier |
| `claim_status` | Open / closed / reopened |
| `claimant_name` | The injured or claiming party |
| `loss_description` | Free text describing what happened |
| `cause_of_loss` | Peril or cause category |
| `paid_indemnity` | Indemnity or loss paid to date |
| `paid_medical` | Medical paid to date (workers comp) |
| `paid_expense` | Expense / ALAE paid to date |
| `paid_total` | Total paid to date |
| `reserve_indemnity` | Outstanding indemnity reserve |
| `reserve_medical` | Outstanding medical reserve |
| `reserve_expense` | Outstanding expense reserve |
| `reserve_total` | Total outstanding reserve |
| `recovery_total` | Subrogation, salvage or deductible reimbursement |
| `incurred_total` | Total incurred |
| `litigation_flag` | Whether the claim is in suit |

Rules:

1. Use each field at most once. If two columns could be the same field, map the
   one that is clearly the total and set the other to `null`.
2. A column that carries a subtotal *and* a total is a total.
3. If a header is ambiguous, return `null` rather than guessing. A wrong
   mapping silently corrupts a loss ratio; an unmapped column is corrected by a
   human in a few seconds.
4. Do not infer a field from the sample values alone. The header is the
   evidence; the samples only disambiguate between two readings of it.

Return exactly this JSON shape, with one entry per column, in column order:

```json
{
  "columns": [
    {"index": 0, "label": "<header as given>", "field": "claim_number", "confidence": 0.95},
    {"index": 1, "label": "<header as given>", "field": null, "confidence": 0.0}
  ]
}
```

### Input

Headers: {{HEADERS}}

Sample rows:
{{SAMPLE_ROWS}}
