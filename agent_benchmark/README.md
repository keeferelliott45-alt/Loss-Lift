# LossLift agent benchmark

A reproducible way to compare coding-agent stacks (e.g. Claude Code
multi-agent vs OpenRouter configurations) on real LossLift work, so a choice
of models rests on measurements, not impressions.

This directory holds only what agents may see: the protocol, the scorecard,
the results template and the prompts of the **development** tasks. Answer
keys, hidden checks, mutants, the seeded review diff and the **held-out**
tasks live in a separate evaluator pack kept outside every repository; its
SHA-256 is recorded in `EVALUATOR_PACK.sha256`. This directory is added after
the tasks' starting commit, so worktrees made from that commit do not
contain it at all.

## Tasks

| ID | Category | Difficulty | Known solution | Set |
|---|---|---|---|---|
| B01 | localized bug fix | medium | yes | development |
| B02 | cross-module correctness | medium | yes | development |
| B03 | extraction edge case | medium | partial | development |
| B04 | logical run / reconciliation | hard | yes | development |
| B05 | test modification (mutation-scored) | medium | objective | development |
| B06 | small architecture change (behaviour-preserving) | medium–hard | yes | development |
| B07 | adversarial review of a seeded diff | medium–hard | answer key | **held out** (content only in evaluator pack) |
| B08 | token-efficient investigation | low–medium | answer key | development |
| H1 | logical run: hand-added claim | medium | yes | **held out** |
| H2 | claim accountability across a bridged page | hard | partial | **held out** |

Every task starts from `b60e93ce5f14f1036888334f6aec6d6c207df02c`. All
documents are synthetic; no task needs the private corpus to be read by the
agent. Where a task changes behaviour, the evaluator runs the private corpus
gate.

**Held out:** B07, H1 and H2 are never used while tuning a configuration's
prompts, roles or model choices. Use B01–B06 and B08 for development and
calibration. Some development tasks touch issues named in
`docs/agent/CURRENT_STATE.md`; that is deliberate and equal for every
configuration, which is why they are not held out. After each evaluation
round, retire the held-out tasks into the development set and write new ones
(see "Rotation").

## Protocol (per configuration, per task)

1. **Fresh start.** New session, no memory, from a worktree at the starting
   commit made with `scripts/agent_worktree.sh create <task> --agent <config>`.
   No access to the evaluator pack or other runs.
2. **Same inputs.** The task file's Prompt, verbatim; the repository's own
   agent docs. Configuration-specific system prompts are recorded with the
   results.
3. **Same tools and budget.** Shell, git, file edit, test runner; no web.
   Budget per task: 90 minutes wall-clock, 4M input tokens, 300k output
   tokens. Hitting a budget ends the run; what exists is graded.
4. **Scripted human.** Questions to the operator are answered only from the
   evaluator's FAQ; anything else gets "Proceed with your best judgement and
   record the assumption." Each question counts as a clarification; each
   operator action beyond answering (restart, hint, fix) is an intervention.
5. **Grading.** The evaluator runs the hidden checks, the synthetic behaviour
   diff, `scripts/agent_validate.sh`, the golden ratchet and, where required,
   the private corpus gate. Two graders score the subjective items blind
   (configuration labels removed); disagreements over 1 point are discussed
   and the resolution recorded.
6. **Repetition.** 3 runs per task per configuration, in randomised order.
   Report the median and the range; a configuration "solves" a task only
   when at least 2 of 3 runs pass every hidden check.

## Rotation and contamination

- Never commit answer keys, hidden checks, reference patches or held-out
  prompts to any repository an agent can read, including PR descriptions.
- Before a round, re-verify with the evaluator pack that every hidden check
  still fails at the starting commit (it must not have been fixed
  meanwhile).
- After a round, check transcripts for reads outside the worktree; a run that
  touched the evaluator pack is void.
- Held-out tasks become development tasks after one round; add new held-out
  tasks built the same way (a verified, undocumented behaviour; black-box
  checks red at the starting commit).

See `SCORECARD.md` for measurement and `RESULTS_TEMPLATE.md` for reporting.
