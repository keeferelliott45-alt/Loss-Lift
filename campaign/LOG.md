# LossLift long-horizon reliability campaign — log

Lead/integrator: `agent/lead/campaign-log` (this branch, docs only).
Started 2026-09-28T00:05Z. Budget: **$25 total, $5/UTC-day, 7 days max**
(hard end 2026-10-05T00:05Z). No OpenRouter cost feed is observable here, so
estimates are conservative and the campaign stops early rather than guess.

## Ground truth (verified 2026-09-28)

- `origin/main` = `7f86de8a0314ccff28d80c06b86f046381f584d7` ✓
- PR #9 `claude/packet-series-and-signed-totals` head =
  `230c0fcd404385fc3ed2e6f9ac6c614ea9429886` ✓ (draft, CLEAN, awaiting owner
  verification of two corpus documents). **Never committed to, rebased,
  force-pushed or merged.**

## Team and models (requested → actual)

| Role | Requested | Actual in this run |
|---|---|---|
| Lead / integrator | strongest | `deepseek/deepseek-v4.1-flash` (this session) |
| Investigator | cheap | not spawned; lead investigated |
| Implementer | mid | same session (lead) for P0-1 |
| Adversary | mid, other family | same session; adversarial test written from the task |
| Reviewer | strong, third family | same session, separate red-first pass |

Single-session execution was chosen because no cost feed is observable: fewer
agent prefixes is the conservative choice under a $25 cap. Spawning distinct
model families remains available for mode-B/C items if budget allows.

## Environment facts

- **pytest basetemp is broken on this machine**: `%TEMP%\pytest-of-keefe`
  exists with an unreadable ACL, so the default run errors every test with
  `PermissionError [WinError 5]`. Every run here passes
  `--basetemp=<fresh dir>`.
- Local corpus absent (`D:\losslift-corpus\`), so the local corpus gate cannot
  run. Cloud **Corpus gate** is the only path and is dispatched from `main`.
- `git fetch --prune` errors on stale worktree registrations (`baseline`,
  `candidate`, `slow-parmesan`) — non-fatal; do not `--prune`.
- Git has no configured identity; commits use per-invocation
  `-c user.name=Kilo -c user.email=keeferelliott45-alt@users.noreply.github.com`.

## Items

### P0-1 — Bridged unnumbered page (held-out H2) — FIXED (draft PR)

- Base `230c0fc`, head `ec83f3dbc44b3e5ae327aa900f5c7608123cb1be`, branch
  `agent/lead/p0-1-bridged-page`.
- Draft PR: https://github.com/keeferelliott45-alt/Loss-Lift/pull/12 (stacked on
  PR #9; not ready, not merged).
- Change: `core/runs.py` `_Planner.unnumbered` marks a page joining a report's
  count `blind` whenever its heading does not confirm the report, regardless of
  the `exact` fit. One condition changed.
- Tests: 2 added; behavioural one red at base (`status=CLEAN`, `CR-40117`
  refused unnamed), green at head (`NEEDS_REVIEW`, R-29 names the row).
- Full suite: 2673 collected, 27 failed — all `test_cloud_gate*`, identical on
  the unmodified base (Windows symlink/fifo/container), 0 non-cloud.
- Golden ratchet: exit 0; ALL 108/108.
- Corpus gate: **NOT RUN** (no local corpus). Required for a stacked PR; owner
  input needed. Expected: wrong CLEAN → NEEDS_REVIEW, no claim lost.
- Retires held-out benchmark H2 (record at retirement time).
- DEFERRED: none.
- Remaining risk: the page is joined blind but still read under the report's
  run; its *read* (non-refused) claims remain attributed to that run. H2 is
  about the refused row; whether a foreign page's read claims should also be
  split is a follow-up product question.

## Open owner decisions

1. Corpus gate for a stacked PR: PR #9 is itself awaiting owner verification.
   Does the owner want my stacked gate run against #9's head, or should H1/H2
   fixes wait until #9's gate clears?
2. Whether a foreign page joined blind should be kept in the run (current) or
   split into its own settled run when its heading names another carrier.

### P0-2 — Hand-added packet row (held-out H1) — FIXED (draft PR)

- Base `230c0fc`, head `6f75cb1d2faac1791212bf8ea422cb2243fa90f8`, branch
  `agent/lead/p0-2-hand-row`.
- Draft PR: https://github.com/keeferelliott45-alt/Loss-Lift/pull/13 (stacked on
  PR #9; not ready, not merged).
- Change: `Claim.source_page` is `int | None` (default 1); a hand-added row with
  no page in a packet gets `None`, belongs to no run, and `_claims_in_no_run`
  (R-11) makes the packet NEEDS_REVIEW. A page the reviewer gives, and a read
  row's page, are unchanged.
- Tests: 1 added, red at base (`source_page == 1`, status CLEAN), green at head.
- Full suite: 27 failed — all `test_cloud_gate*`, identical on base, 0 non-cloud,
  0 errors. Ratchet exit 0 (108/108). compileall 0, `git diff --check` 0.
- Corpus gate: **NOT RUN** (no local corpus). Expected: no real-corpus change
  (extraction output has no review-added claims).
- Retires held-out benchmark H1 (record at retirement time).
- DEFERRED: none.

## Budget after 2026-09-28

- Recon/setup $0.50, P0-1 $2.00, P0-2 $2.00 = **$4.50 / $5.00** for the day,
  **$4.50 / $25.00** total. No further item fits the day's headroom (a
  conservative item costs $2.00), so the campaign **pauses until the next UTC
  day** per the owner's rule.
- At 12 items total this pacing lands at ~$24 and ~6 days, inside both caps.

### Base movement (2026-09-28T11:20Z)

- `origin/main` unchanged `7f86de8a`. PR #9 head moved `230c0fcd` -> `4ca0d41`
  (fast-forward, 230c0fc an ancestor; commit "Name an incomplete single report
  instead of reading it clean", author Claude). No rewrite/force-push. The peer
  campaign `kilo/silent-clean-hunt` (PR #16) logged the same move and stopped.
- Concurrent peer PRs: #14 export-privacy-hardening (P3-10, base main),
  #15 packet-aware summary (base main), #16 silent-clean fuzz (harness only).
- Action: new items serialize from the new head `4ca0d41`, per the conflict rule.

### P0-3 — Refused row on a bounded single report — FIXED (draft PR)

- Base `4ca0d41`, head `59fe42a75ced62eab2a66cbd1c557b3ebfc29f9b`, branch
  `agent/lead/p0-3-refused-single`.
- Draft PR: https://github.com/keeferelliott45-alt/Loss-Lift/pull/17 (stacked on
  PR #9; not ready, not merged).
- Decision (settles the open rule behaviour): fail closed; `row.report =
  not (run is not None and run.ambiguous)`. R-28 keeps the ambiguous run's
  refusals; R-29 names every other refusal, including a bounded single report.
  Rule id R-29 unchanged; message generalised; function renamed.
- Tests: 1 added, red at base (`report=False`, CLEAN), green at head.
- Full suite: 27 failed, all `test_cloud_gate*`, identical on base, 0 non-cloud,
  0 errors. Ratchet 108/108. compileall 0, diff-check 0.
- Corpus gate: **NOT RUN** (no local corpus). Effect: a bounded single report
  that refuses a claim-like row moves CLEAN -> NEEDS_REVIEW; every affected
  document needs owner approval. Marked awaiting approval.

## Budget note (2026-09-28)

- No OpenRouter key is available to this session, so actual spend is
  unobservable here (the peer campaign could read `GET /api/v1/key`; I cannot).
- Owner directed continuation at 11:20Z, over the earlier placeholder pause.
  Placeholder accounting: recon $0.50 + P0-1 $2.00 + P0-2 $2.00 + P0-3 $2.00 =
  **$6.50 total**, nominally above the $5/day placeholder. The caps govern actual
  OpenRouter spend, which is likely far below this; owner should reconcile.
- Total placeholder: **$6.50 / $25.00**.

## Next action

P1-4 (recovery sign per run: `normalize.infer_recovery_sign` runs once per
document, so a packet mixing credit and positive-recovery runs raises a false
R-01 on one run; infer per run, single reports unchanged), base `4ca0d41`, own
branch + draft PR stacked on #9.
