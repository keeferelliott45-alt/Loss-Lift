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

## Next action

Checkpoint. Next item: P0-2 (hand-added packet row, `pipeline.apply_edits`
`record["_page"] or 1`), base `230c0fc`, own branch + draft PR stacked on #9.
