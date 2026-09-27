# AGENTS.md — LossLift agent entry point

LossLift turns insurance loss run PDFs into clean, reconciled, underwriting-ready
spreadsheets. It is a **buyer-neutral trusted loss-history engine**: the product
is accountability and reconciliation, not extraction. Read `CLAUDE.md` first —
it is the authoritative spec — then the files below.

## Authoritative sources (read in this order)

| Document | What it governs |
|---|---|
| `CLAUDE.md` | Product spec: data model, pipeline, reconciliation rules, data handling. **Authoritative.** |
| `.kilo/rules/*.md` | LossLift guardrails loaded automatically (see `kilo.jsonc`). |
| `docs/agent/INVARIANTS.md` | Product invariants any change must preserve. |
| `docs/agent/REPO_MAP.md` | Where each module and procedure lives. |
| `docs/agent/CURRENT_STATE.md` | What exists now, derived from the repo. |
| `docs/kilo-code.md` | How Kilo is set up here: chat vs Agent Manager, worktrees, workflow. |

This file does not restate the spec. It points at it.

## Non-negotiable guardrails

- **Fail closed.** Never silently lose, fabricate, merge or reassign a claim or
  an amount. An unparseable value is `null` with a reason, never `0`. Incorrect
  association is worse than incomplete association.
- **Every number carries provenance** (page, region, method), answered per field.
- **Reconcile against what the carrier printed.** R-04 (money columns tie to the
  printed footer total) and R-05 (row count equals printed claim count) are the
  product. ERROR blocks a clean export; WARN/INFO never do.
- **Review is not reconciling.** A decision is recorded beside a finding, never
  in place of it.
- **Never commit** corpus PDFs, manifests, document contents, API keys or
  tokens.

Details: `.kilo/rules/10-extraction-reconciliation.md`,
`.kilo/rules/40-secrets-privacy.md`, `docs/review-invariants.md`.

## Commands

Project slash commands live in `.kilo/commands/`:

| Command | Use |
|---|---|
| `/start-feature` | Start a change safely on an isolated branch from current `main`. |
| `/verify-pr` | Verify a PR at an exact head against the required checks. |
| `/adversarial-review` | Attack a change for fail-open regressions. |
| `/corpus-gate` | Prepare a cloud corpus-gate run and read its verdict. |

## Validation

```bash
python -m pytest                     # full suite
python -m tests.golden.baseline      # per-carrier ratchet (non-zero = regression)
python -m tests.golden.report        # accuracy table (money threshold, nulls-as-zeros)
python -m benchmark.run --docs <corpus dir> --label <label>   # real-document benchmark
```

Extraction/reconciliation changes also require the corpus gate: locally
`python -m tools.corpus_gate run --baseline <sha> --candidate <sha> --corpus
<dir> --manifest <file>`, or the cloud **Corpus gate** workflow from `main`.
Exit 0 is the only pass. See `docs/corpus-gate.md` and
`docs/cloud-corpus-gate.md`.

Never edit a golden expected output, accuracy baseline, tolerance or gate to
make a failing check pass.

## Git and PRs

- Start from current `main` on an isolated feature branch; one writing agent per
  worktree; supporting subagents are read-only.
- Verify an exact 40-character commit SHA, never a moving branch name.
- Open **draft** PRs. Do not mark ready or merge without the owner's explicit
  authorization. Keep work isolated from unrelated open PRs (for example PR #9).
- Generated Kilo state and worktrees stay local (`.kilo/worktrees/`,
  `.kilo/agent-manager.json`).

## Measurement objective

The number that matters is the **AUTO-SAFE vs NEEDS_REVIEW vs unresolved rate,
broken down by document class**. In code, AUTO-SAFE is `DocumentStatus.CLEAN`
(no ERROR finding); NEEDS_REVIEW is any ERROR; unresolved is a page or document
the pipeline could not measure. Prioritize by corpus frequency and severity, not
speculative edge cases. See `benchmark/README.md`.

## Handoff

Use `agent_tasks/TASK_TEMPLATE.md` to define a task and
`docs/agent/HANDOFF_TEMPLATE.md` to hand off or resume one.
