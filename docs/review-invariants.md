# Review identity and mutation contracts

New rules must supply `scope`, `category`, and an explainable, non-empty
`subject`. Findings are immutable. There is no claim-number fallback for
physical-row identity.

| Scope | Subject | Additional contract |
| --- | --- | --- |
| `claim` | `claim.row_id` | Claim number and optional page must match that row. |
| `claim_group` | `claim-number:<number>` | `related_rows` names every physical row in the group. |
| `document` | `document` | No claim number; field and condition distinguish checks. |
| `column` | `column N` (one-based) | Field and column must match one mapping record. |

`condition` defaults to `primary`. Independent failures on the same subject
and field need stable names, as R-10 uses `reported-before-loss` and
`after-valuation`. Do not use messages, amounts, list positions, or UUIDs.

The identity is an unambiguous JSON tuple of rule, category, scope, subject,
field, condition and group membership. The runner rejects duplicate active
keys, mismatched rule IDs, invalid row/page associations, and invalid
column/group associations. It revalidates instances made with Pydantic's
explicit construction/copy escape hatches too.

Every finding declares `financial`, `extraction`, or `underwriting` category.
Financial failures are ERRORs; underwriting observations are WARN or INFO.
Unknown future rule IDs no longer silently become underwriting observations.

Expected, actual and delta are the material assertion. Changed assertions or
severity reopen findings; numerically equal Decimal scales remain equivalent.
If no assertion values exist, changed message text reopens the finding. Old
decisions remain historical and their notes do not masquerade as current ones.

Validation proves identity consistency, not business-rule correctness. A rule
can still evaluate the wrong predicate or associate an assertion with a
plausible but wrong row. Test the rule's logic and provenance as well.

## Mutation boundaries

Grid changes use `core.pipeline.edit_claims(result, records)`. Finding
decisions use `resolve_finding`. Both publish document, reconciliation and
audit chronology only after all work succeeds, using the carrier profile's
tolerance. Never assign the new document before reconciliation finishes.

`apply_edits` is the lower-level rebuild operation. It preserves hidden claim
fields and carrier evidence. Existing rows cannot be deleted or have their
claim numbers cleared. There is no exclusion model. A duplicate-number group
is not one correctable cell: select the physical row in the grid.

Money crosses the UI boundary as exact text. Unchanged canonical Decimal text
is not reparsed using source locale. Explicit null-reason changes are audited;
untouched null representations retain their reasons. Semantic no-ops do not
publish state or trigger UI reruns.

## Verification

Run `python -m pytest` and `python -m tests.golden.report`. The adversarial
handoff suite covers chronology, missing/overlapping metadata, group
membership, hidden fields, exact decimals, null reasons, failure injection,
export failure and fresh same-PDF rereads. Real PDFs are intentionally absent;
synthetic success does not prove real-corpus parity.
