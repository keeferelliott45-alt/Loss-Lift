# Reliability phase 1 — integration authority and record

Implement the approved engineering batch, not business/product features.
Only the lead may integrate task branches into `agent/lead/reliability-phase-1`.
No agent may merge into main, force-push, weaken tests, change truth to fit
extraction, or add corpus approvals without document-level review.

## Pinned baseline

Start: `597ff9dd12c1c5cdda32b4dbe7f5fbfed3993241` (submission workspace).
Merged, without conflicts, in order:

1. `c7de7825e6beb58af1d0fb88b6f6ee43340ee0da`
2. `7d825186bb75bfd9b9355b4b675f0b2a41266f0b`
3. `c94f5843752fdb7ef1665d5fef9d3a252774548f`
4. `9ff962051ba6073fab147ca4a90d990c9437af94`

B0 code: `0f13c5c90a66621774000c6eb368df82b10e95cd`.
Existing PRs/main are preserved. PR #15 is not imported wholesale.
Remote reads/fetches use `github`, not the local-mirror `origin` defaults.

## Task order

E1 qualification -> E2 identifiers/carrier -> E4 bounded mixed-reader voting
-> E3 preflight -> E5 digital pack -> E6 mixed/accounting pack -> E7 validation.
E3 is independent of E1/E2/E4. Packs start only from the frozen Q1 interface.
Each implementation uses its own worktree and branch; draft task PRs target
the lead branch. No production writer shares another task's files.

The user authorized Codex subagents for independent lanes when the local
Claude authentication expired and no OpenRouter runner was available.
Do not attribute that work to Opus/OpenRouter.

## Evidence requirements

Record exact B0/Q1/L1/M1/F1 SHAs, red-first tests, targeted checks,
`scripts/agent_validate.sh --base <full task base>`, full suite, golden,
fixed-seed clean/hostile fuzz and private corpus comparisons.
Only exit zero is a pass; pending/failed evidence stays explicit.
The private corpus is unlabeled and cannot prove 99.9% accuracy.
Its documents, manifest, truth, salt and recordings never enter Git/public output.
Gate approvals for other commits do not transfer to this batch.

## Windows execution

Python: `C:/Users/keefe/AppData/Local/Python/pythoncore-3.14-64/python.exe`.
Use task-owned TEMP/TMP and explicit pytest basetemp paths. Git Bash needs
its usr/bin and mingw64/bin on PATH; use explicit safe.directory entries for
the task worktree, not changes to global Git trust. Environment failures
must be investigated separately from assertions; never skip tests to hide them.

## Handoff

Every task uses `docs/agent/HANDOFF_TEMPLATE.md`; lead records integration
evidence centrally and rewrites CURRENT_STATE at the final milestone.
