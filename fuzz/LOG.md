# Silent-CLEAN fuzz campaign — log

Append-only. One entry per batch, checkpoint or root cause. Times are UTC.

## Campaign 1 — 2026-09-28

### Preflight (verified before any edit)

| Check | Value |
|---|---|
| PR #9 head | `230c0fcd404385fc3ed2e6f9ac6c614ea9429886` (open, draft) |
| Required base | `230c0fcd404385fc3ed2e6f9ac6c614ea9429886` |
| Worktree HEAD | `230c0fcd404385fc3ed2e6f9ac6c614ea9429886` |
| `origin/main` | `7f86de8a0314ccff28d80c06b86f046381f584d7` |
| Branch | `kilo/silent-clean-hunt` |
| Working tree | clean |
| Status policy | `core.review.canonical_status` / `canonical_run_status` present |
| OpenRouter auth | direct `https://openrouter.ai/api/v1/key` responds; monthly limit 25 |

### Budget ledger (campaign key)

Preflight read only; no campaign model calls were made (the lead ran through the
session model, OpenRouter key untouched by fuzzing). Recorded from the numeric
fields of `GET https://openrouter.ai/api/v1/key`:

| Reading | usage | usage_daily | limit | limit_remaining |
|---|---|---|---|---|
| 2026-09-28 preflight | 2.990366613 | 1.341236599 | 25 | 22.009633387 |

Spend this campaign: **$0.00** of a $25 cap and $5/day. Well under budget.

### Batching model

- Lead/integrator: `openrouter/deepseek/deepseek-v4.1-flash`, high.
- Investigators: none used this campaign (a single writer; no read-only
  investigators needed — the harness is self-triage). No Kilo Gateway calls.

### Harness

Delivered under `tests/fuzz/`: `generator.py` (seeded synthetic loss runs +
ground truth as `Decimal`), `mutations.py` (26 hazard operators),
`oracle.py` (SAFE / SILENT / LOUD / UNNAMED via `canonical_status` only),
`shrink.py` (greedy operator minimiser), `run.py` (CLI). Smoke:
`tests/test_fuzz_smoke.py` (50 fixed seeds per family, < 60 s).

### Batches

| Batch | Command | Cases | SAFE | SILENT | LOUD | UNNAMED | ERROR |
|---|---|---|---|---|---|---|---|
| clean baseline | `--family clean --count 50` | 50 | 50 | 0 | 0 | 0 | 0 |
| hostile | `--family hostile --count 60` | 60 | 38 | 0 | 21 | 0 | 0 |
| hostile wide | `--max-ops 4 --count 500` | 500 | 370 | 1* | 121 | 0 | 0 |
| hostile wide | `--max-ops 6 --count 800` | 800 | 651 | 0 | 131 | 1 | 0 |
| hostile wide | `--seed-start 10000 --max-ops 8 --count 900` | 900 | 750 | 0 | 110 | 0 | 0 |
| scanned clean | `--family clean --vision --count 20` | 20 | 0 | 0 | 20 | 0 | 0 |

Per-operator sweeps (40 seeds each, every operator): zero SILENT.

\* the single SILENT in the 500-batch was a harness false positive: the
`missing_page` operator removed the only page of a run the oracle still counted
as a second run. Fixed in `oracle.classify` (run count now derives from the
claims that survived). No product change.

### Root causes

- **No confirmed silent-CLEAN product defect reproduced** across ~2,300 seeded
  cases plus per-operator sweeps. The accounting that closes the modelled gaps
  is real: R-19 counts claim-number-bearing rows and fires when one is dropped;
  R-23 catches unattached money; R-04/R-05/R-25 catch wrong printed totals and
  counts; R-11/R-12 catch duplicates. Verified directly that known lead #2
  (hand-added packet claim with no page) does not read CLEAN: R-19 fires because
  the manual row has no printed row to account for.
- Harness defects fixed (not product): oracle run-count after a mutation removed
  a run; `parse_printed` sign/parenthesis ordering; operator robustness
  (`SHAPES`, `DATES` imports; `wrong_total` reading a European/signed total).

### Near misses (tracked, not silent)

- 1 UNNAMED (§ batch 4): a `wrong_count` printed count not scoped by R-05/R-27,
  but the document was already NEEDS_REVIEW on R-04/R-11. Not a silent CLEAN.
- Scanned clean cases are always LOUD (R-27: the model's printed count is not
  adopted). Recorded as the scanned LOUD baseline; see `CURRENT_STATE.md`
  known gaps.

### Corpus gate

No product code was changed (harness and docs only), so the corpus gate is not
triggered by this campaign. Gate run: none.

### Checkpoint 1

- Commits: `1f43b07` (harness + docs + log), `ad26f7d` (whitespace).
- `git rev-parse origin/main` = `7f86de8a0314ccff28d80c06b86f046381f584d7` (unchanged).
- PR #9 head = `230c0fcd404385fc3ed2e6f9ac6c614ea9429886` (unchanged, open, draft).
- Branch `kilo/silent-clean-hunt`; working tree clean before the whitespace fix.
- OpenRouter usage unchanged from preflight (no campaign model calls).
- `scripts/agent_validate.sh --base 230c0fc --tests tests/test_fuzz_smoke.py`:
  - PASS targeted, private-data, compile, golden (108/108).
  - FAIL full-suite: only `tests/test_cloud_gate*.py`, which need POSIX
    symlink privilege, `os.mkfifo` and container tooling absent on Windows.
    They are unrelated to this change (harness + docs only) and fail at the
    base too. Same file set passes when those two files are excluded (exit 0).
  - FAIL worktree: the project script requires branch `agent/<agent>/<task>`;
    this campaign's branch is mandated as `kilo/silent-clean-hunt`, so the
    convention check cannot pass. The worktree is isolated (Kilo managed).
  - NOT RUN corpus-gate: no product code changed, so no extraction or
    reconciliation behaviour moved.

### Next action

Campaign 1 closed PASS: harness delivered, no silent failure found within
budget. Resume by seeding new operator families from a real silent report if one
appears, and by extending the scanned family once R-27 adopts the model count.

## STOP — PR #9 head moved

Observed after PR #16 was opened:

| Ref | At preflight | Now |
|---|---|---|
| `refs/heads/claude/packet-series-and-signed-totals` | `230c0fcd404385fc3ed2e6f9ac6c614ea9429886` | `4ca0d414dc8ef94b526014af11b650e56392b2a1` |
| `refs/heads/main` | `7f86de8a0314ccff28d80c06b86f046381f584d7` | `7f86de8a0314ccff28d80c06b86f046381f584d7` (unchanged) |
| `refs/heads/kilo/silent-clean-hunt` | — | `b7fc0b1f6a53f90f84673462e9d80109d352f9bf` |

PR #9 is still OPEN and draft, updated `2026-09-28T01:08:25Z`. No push, rebase,
merge or edit was made to PR #9's branch by this campaign.

Effect: every validation in this log was run at base `230c0fc`. The branch that
PR #16 targets has advanced, so the validated base is no longer the branch tip.
The cluster is stopped: no further fuzzing or commits are made against a moving
base.

Next safe action (owner): decide whether to re-validate this harness against
`4ca0d41` (a fresh worktree from that SHA), or pin PR #16's base to `230c0fc`.
Do not rebase PR #9's branch.

