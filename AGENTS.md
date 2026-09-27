# AGENTS.md — rules for any coding agent working on LossLift

Permanent, project-wide rules. The product spec is `CLAUDE.md`; this file is
how to work. Keep it short; history goes in `Remember.md`, the present state in
`docs/agent/CURRENT_STATE.md`.

## Start here (read in this order, nothing else up front)

1. `AGENTS.md` (this file)
2. `docs/agent/REPO_MAP.md` — where things are
3. `docs/agent/CURRENT_STATE.md` — what is true now
4. Your task file (`agent_tasks/<task>.md`)
5. `docs/agent/INVARIANTS.md` — only the sections your task touches

Then: search symbols (`rg`, grep) before opening files; read only the modules
the task touches; use `git log -p -- <file>` or `git blame` selectively; open
`Remember.md` only when you need to know *why* something is the way it is.
Do not "read the entire repository thoroughly" unless the task says so.

## Non-negotiable engineering rules

1. **Fail closed.** When the code cannot tell, the answer is NEEDS_REVIEW with
   a reason — never a guess that makes a check pass. Never invent a boundary,
   a value or a mapping to make reconciliation tie.
2. **No silent claim disappearance.** Every claim-like row is either read into
   exactly one claim, or recorded (refused / unplaced / unresolved page) and
   reachable by a rule. A null is never a zero.
3. **Preserve provenance.** Every value keeps its page, row/lines and method
   (`digital` | `vision` | `manual`). Edits keep the original beside the new
   value. Do not drop or approximate provenance.
4. **Reconciliation safety outranks apparent extraction success.** A change
   that reads more claims but weakens a check is a regression.
5. **Logical runs are first-class.** A PDF can bind several loss runs. Facts
   (carrier, policy, term, valuation, totals, counts) belong to the run whose
   pages print them; never borrow another run's.
6. **One status policy.** Trust/status comes only from
   `core.review.canonical_status` / `canonical_run_status`. Do not write a
   second definition in the app, export, telemetry or tools.
7. **Stable identities.** Do not rename rule ids (`R-01`…), finding
   categories, scopes, condition strings, enum values, run id format
   (`run-N`) or measured field names without an explicit task: the corpus
   gate and review history key on them.
8. **Private data stays private.** Never commit or print real loss runs,
   corpus manifests, salts, labels files, vision recordings of real documents,
   telemetry files or `.env`. Tests use synthetic documents only (spec §9).
   Never paste document text into commits, PRs or handoffs.
9. **No speculative product work.** Nothing from spec §13 (auth, billing,
   integrations, dashboards, ACORD/SOV, …) and no abstractions for
   hypothetical buyers. Do the assigned task; note other findings as deferred.

## Required checks before handoff

- `scripts/agent_validate.sh` (add `--tests "<paths>"` for your targeted tests).
  It runs: worktree check, diff/whitespace and private-data checks, compile,
  targeted tests, the full suite, and the golden accuracy ratchet.
- Behaviour change to extraction or reconciliation → the private corpus gate
  must be run by someone with access (`docs/corpus-gate.md` locally, or the
  cloud *Corpus gate* workflow, `docs/cloud-corpus-gate.md`). Exit 0 is the
  only pass. Explain every changed document; never add an allowlist entry or
  touch a manifest just to make a failure pass.
- Never skip, disable, xfail or loosen a test to get green. A strict xfail
  documents a known gap; do not flip it without fixing the gap.

## Git rules

- Work only in your own worktree and agent branch
  (`scripts/agent_worktree.sh create <task> --agent <name>`). Never commit on
  `main` or another agent's branch.
- Never force-push, rebase or reset a shared branch. Never delete branches you
  did not create.
- Do not merge your own work, mark PRs ready, or approve anything unless your
  task explicitly grants integration authority.
- Small, cohesive commits with messages that say what changed and why.

## Finish with a handoff

Every task ends with `docs/agent/HANDOFF_TEMPLATE.md`, filled in, in your
final message (and in the PR description if you open one).
