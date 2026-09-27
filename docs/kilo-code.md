# Working on LossLift with Kilo

This page explains how Kilo is set up for this repository and how to use it
without endangering the product's core promise: explicit accountability for
every logical run, claim and amount, with fail-closed uncertainty. `CLAUDE.md`
remains the authoritative spec; `AGENTS.md` is the agent entry point; the
project rules live in `.kilo/rules/` and the slash commands in
`.kilo/commands/`.

## Ordinary Kilo chat vs Agent Manager

**Ordinary Kilo chat** is the conversation you are having now: one agent, one
working directory, reading and editing files in place. It is right for small,
sequential work — a bug fix, a doc change, a question about the code.

**Agent Manager** runs several *visible* agent sessions in parallel, each in its
own managed git worktree, with its own branch, terminal and session state. It is
right when you have genuinely independent pieces of work and want them isolated.
Each session is a separate checkout, so dependencies, caches and generated files
can multiply on disk — use it deliberately, not by default.

Both share the same project config, rules and commands.

## Local vs cloud execution

- **Local** work happens on this machine, in this worktree, against the local
  Python environment. It is the only place the private corpus exists (see
  below), and the only place the full test suite and golden ratchet should be
  run before a corpus change is proposed.
- **Cloud** execution means Kilo sessions or GitHub Actions running off this
  machine. The only cloud workflow that touches real documents is the
  **Corpus gate** (and **Corpus update**), dispatched from `main` in the
  browser on the private corpus repository. No real loss run is ever committed
  here. See `docs/cloud-corpus-gate.md`.

## Worktrees

Kilo worktrees live under `.kilo/worktrees/` and are **git-ignored**: they are
local checkouts, not repository content. Treat each worktree as a separate
checkout on its own branch:

- one **writing** agent per worktree;
- supporting/research subagents are read-only unless a task explicitly
  authorizes writes;
- do not edit another worktree's files or branch;
- do not use `git stash` — stashes are shared across worktrees.

To bring work back, choose one path: Agent Manager *Apply*, merge the worktree
branch, or open/update a PR from the worktree. For conflict-heavy work, merge or
rebase `main` into the worktree and resolve there before integrating.

## Model roles

- **Primary/writing agent:** the model you are chatting with, which edits files
  and runs commands. It owns the change.
- **Subagents (Task tool / `explore`):** cheaper, read-only research helpers for
  auditing instructions, tests, procedures and the final config. They report
  findings; they do not write.
- **Agent Manager sessions:** full agents, each with its own worktree; use them
  for parallel independent work, not for routine lookups.

There is no hard model pin in `kilo.jsonc`; pick the model per task with `/models`
and keep the writing agent on the strongest model for correctness-sensitive
work.

## The normal LossLift feature-to-draft-PR workflow

1. Start from current `main` on an isolated branch — run `/start-feature`.
2. Read `CLAUDE.md`, `AGENTS.md`, `docs/agent/INVARIANTS.md` and `.kilo/rules/`.
   Name the invariant the change preserves.
3. Write a focused regression test first, then the smallest general fix. No
   filename checks, no per-document exceptions.
4. Run the focused test(s), the full suite (`python -m pytest`), and the golden
   ratchet (`python -m tests.golden.baseline`). Refresh the baseline only for a
   real improvement.
5. If extraction or reconciliation behavior changed, prepare the corpus gate —
   run `/corpus-gate` (cloud) or the local `python -m tools.corpus_gate run`.
   Exit 0 is the only pass.
6. Run `/adversarial-review` against the change.
7. Open a **draft** PR targeting `main` and link it. Do **not** mark it ready or
   merge it without the owner's explicit authorization. Run `/verify-pr` for an
   exact-head verification.

Keep this work isolated from unrelated open PRs (for example PR #9).

## How the private corpus stays outside Git

Real loss runs carry claimant names and injury descriptions. They are never
committed, and neither is the manifest that lists them (it holds the digest
salt).

- The corpus directory and its `manifest.json` live **outside the repository**
  (the local gate refuses either inside the working tree).
- The gate copies each verified document into a private, temporary snapshot,
  named by position, and deletes it afterwards. Documents are reported by
  manifest id and SHA-256, never by file name; all values are counts, enums or
  keyed digests.
- In the cloud, the corpus is a release of the private
  `keeferelliott45-alt/LossLift-Corpus` repository, read with a token scoped to
  that one repository. The workflows never commit a document, file name,
  manifest or salt to LossLift.
- `.gitignore` keeps `.env`, `data/profiles/`, `uploads/`, `*.pdf`,
  `.streamlit/secrets.toml` and the local Kilo state out of Git. Never add a
  real document to `tests/golden/` — fixtures there are synthetic.

If a secret or document ever reaches a commit, stop and report it; do not just
delete the line and continue.
