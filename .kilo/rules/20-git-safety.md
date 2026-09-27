# Git safety

- Start every change from the **current `main`** on an **isolated feature
  branch** (for example `kilo/<short-topic>`). One writing agent per worktree;
  never edit another worktree's branch.
- Do not modify or depend on unrelated open pull requests. In particular, keep
  this work isolated from **PR #9** and other in-flight branches.
- Verify an **exact head**: review and corpus-gate a full 40-character commit
  SHA, not a branch name. A branch can move; a SHA cannot.
- Open pull requests as **draft**. Do not mark a PR ready or merge it without
  **explicit owner authorization** in the current task. Silence is not
  authorization.
- Do not force-push, rewrite shared history, or use `git stash` (stashes are
  shared across worktrees).
- Do not commit generated Kilo state or worktrees; they stay local (see
  `.gitignore`).
- Never amend a commit that already failed review or CI; add a new commit.