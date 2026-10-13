# Gate queue — owner brief (2026-10-13)

What waits on the private corpus gate, what each run should show, and what
to do first. The gate procedure itself is in `docs/cloud-corpus-gate.md` (browser)
and `docs/corpus-gate.md` (local). **Exit 0 is the only pass. Allowlist
entries are yours alone.** Add one only when the changed document is
explained and you believe the change. Then re-run with identical inputs.

## Order

1. **#56**: no prerequisite. Smallest change.
2. **#52**: no prerequisite.
3. **#49**: needs two things first:
   - #46 merged (the gate's `vision` group);
   - vision answers re-recorded against the live model for the scanned
     documents. #49 changes `prompts/extract_vision.md`, and the prompt's
     hash is part of every recording key. Old recordings no longer match.

   Without both, every scanned page reads unread on the candidate side, and
   the run measures nothing about R-27.

## The heads

| PR | Head | Baseline | Expected changes — only these | Stop if |
|---|---|---|---|---|
| #40 carrier | merged `ffc01c5` | — | Nothing pending. Merged under your written exception to run 38308067802 (exit 1: 6 changed, 0 failed, critical measures unmoved). Optional: record those 6 as allowlist entries so the audit trail lives beside the gate. | — |
| #56 pooled count guard | `e2c7f83` | `main` | Packets whose pooled GRAND TOTAL row prints a claim count in grand-total wording: a false run-level R-05 disappears, and the count is recorded as unresolved evidence (R-27). Vision is not involved. | Any claim count, status or totals change; any document without such a row moves |
| #52 records shape ceiling | `c62ba58` | `main` | A document whose claim-number vote spans more than 4 numbering families goes to 0 claims, with its rows refused (R-29 + R-20, NEEDS_REVIEW). | Any real loss run moves. That means the ceiling is too low: raise it, never allowlist |
| #49 R-27 adoption | `2419f8c` | `main` | Clean scanned pages whose parsed rows corroborate the model's count: NEEDS_REVIEW → CLEAN, plus one R-27 INFO `claim-count-adopted-vision`. Disagreeing scans stay NEEDS_REVIEW with a more specific message. | `vision.missing` non-empty (re-record first); any digital document moves |

## How to read a run

- **Critical:** `claim_count`, `status`, `review_status`. Any movement there
  needs a written reason before anything else.
- **`vision.missing`** (once #46 is merged): the candidate had no recording
  for a scanned page. This is a setup failure, never a behaviour change.
  Re-record and re-run. Do not allowlist it.
- A run that measured fewer documents than the manifest lists has not
  passed (exit 3 or 4). Fix the input, then re-run.

## After the queue

Two SILENT classes are pinned on #51: the figureless claim row across a page
break, and account `dropped`. Their fixes touch `core/pipeline.py` and
`core/account.py`. Both will need a gate run of their own.
