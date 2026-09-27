---
description: Start a LossLift feature safely on its own branch from current main
---
Start a feature safely. Topic: $ARGUMENTS

Do this in order, and stop if any step fails:

1. Confirm the worktree is clean and on `main` (or a branch created from the
   latest `main`). Fetch `origin`, and confirm the base is the current
   `origin/main` commit, not a stale local copy. Record the base SHA.
2. Create an isolated feature branch: `kilo/<short-topic>`. Do not reuse a
   branch, and do not touch unrelated branches or PRs (especially PR #9).
3. Read `CLAUDE.md`, `AGENTS.md`, `docs/agent/INVARIANTS.md` and the rules in
   `.kilo/rules/` before changing anything. State the invariant the change must
   preserve and the failure it must not introduce.
4. Identify the module(s) and the reconciliation rule(s) involved. Prefer the
   smallest change that fixes the mechanism generally; no filename checks and
   no per-document exceptions.
5. Write the focused regression test first, then the change.
6. Run the focused test(s), then the full suite (`python -m pytest`), then the
   golden ratchet (`python -m tests.golden.baseline`). Refresh the baseline
   (`--update`) only for a real improvement.
7. If extraction or reconciliation behavior changed, prepare the corpus gate
   (see `/corpus-gate`) before opening a draft PR.
8. Open a draft PR targeting `main`. Do not mark it ready or merge without the
   owner's explicit authorization.
