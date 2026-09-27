# TOPOLOGY — how agents work on LossLift together

For orchestrators and integrators. Implementers need only `AGENTS.md` and
their task file. Design goals, in order: no silent regression, one writer
per worktree, integration separated from implementation, then cost. More
agents are added only where they buy independence (a second opinion that
can disagree) or real parallelism (questions that do not depend on each
other).

## Roles

| Role | Purpose | Receives | Must NOT receive | Model | Writes | Worktree / branch | Produces | May spawn / escalate |
|---|---|---|---|---|---|---|---|---|
| **Orchestrator** (tech lead) | Turn intake into bounded task files; choose mode; decide conflicts short of integration | AGENTS, REPO_MAP, CURRENT_STATE, INVARIANTS, the request, handoffs | Full transcripts; private corpus | strongest available | task files only (`agent_tasks/`) | main worktree, read-only for code | task file(s), mode, dispatch plan, final decision memo | spawns every other role; escalates product decisions to the human |
| **Investigator** | Answer one bounded question when the locus is unknown | stable prefix + one question + allowed paths | other candidates' diffs; solutions | mid-tier (cheap if the question is lookup-only) | nothing | detached read-only worktree at base | ≤1-page memo: file:line facts, reproduction script, open questions | none; flags "semantic ambiguity" to orchestrator |
| **Implementer A** | Make the change with tests | stable prefix + task file + investigator memo | other implementer's work; reviewer notes before first handoff | mid-tier by default; strongest when escalated | its worktree only | `agent/<impl-a>/<task>` from base | commits + handoff | escalates per the rules below; never spawns |
| **Implementer B** | Independent second candidate — only in hard-problem mode or after a disputed first attempt | same as A, different model/provider | A's diff or handoff | a different family from A | its worktree only | `agent/<impl-b>/<task>` | commits + handoff | as A |
| **Test / corpus adversary** | Try to break the change: write attack tests from the task and invariants, run them on every candidate; request the gate | stable prefix + task file + INVARIANTS; candidates' branch names (to run tests), not their reasoning | implementers' handoff narratives (to stay unbiased) | mid-tier | tests only | `agent/adversary/<task>-attack` from base | failing-first attack tests; per-candidate results table; gate request | escalates "unexplained corpus change" |
| **Diff reviewer** | Independent read-only review of each candidate | AGENTS, INVARIANTS, task file, `git diff base...candidate`, handoff, adversary results | implementer transcripts | strongest, different provider from the implementer | nothing | detached worktree of the candidate | findings (file:line, failure mode, severity, repro) + verdict | none |
| **Integrator** | Sole merge authority; runs the private corpus gate | all handoffs, reviews, gate results | — | human, or strongest model with explicit authority | integration branch + CURRENT_STATE/Remember | `integration/<task>` | merged branch, gate evidence, updated CURRENT_STATE / Remember.md | returns work to the orchestrator; escalates to the human |

The corpus gate is a tool, not an agent: only the integrator (or a human)
runs it, because only they hold the corpus.

## Lifecycle

1. **Intake** (orchestrator): restate the request as an objective; check
   CURRENT_STATE for overlap; decide whether it is one task or several.
2. **Decomposition**: write `agent_tasks/<slug>.md` per task from
   `TASK_TEMPLATE.md`. Split only along surfaces that do not overlap (see
   conflict rule 1). Choose the mode (below).
3. **Investigation** (optional): only when the task names no code locus or the
   orchestrator cannot state the invariant at risk. Parallel investigators
   only for independent questions (e.g. "where is X decided" vs "what does
   the gate measure for X").
4. **Implementation**: one implementer by default; two only in hard-problem
   mode. Tests first (red on base).
5. **Tests**: implementer runs `scripts/agent_validate.sh --tests ...`. The
   adversary writes attack tests on base (blind to the implementation) and
   runs them on each candidate.
6. **Corpus gate**: the integrator runs base → candidate for every candidate
   that passes validation and the adversary's tests.
7. **Independent review**: the reviewer reads the diff plus the evidence.
8. **Integration decision**: integrator applies the conflict rules and picks
   at most one candidate; adversary tests that add value are merged with it.
9. **Handoff / state**: integrator updates CURRENT_STATE (rewrite, not
   append) and adds a Remember.md entry for any decision with a "why".

## Conflict rules

1. **Same architectural surface.** Two tasks that touch the same module
   function, rule, schema field or measured gate field never run in
   parallel; the orchestrator serialises them (the second starts from the
   first's merge).
2. **Competing implementations.** Rank by: hard fails (any = out) → hidden or
   adversary tests → corpus gate (unexplained change = out) → reviewer's
   blocking findings → invariant risk → smaller diff and scope discipline →
   cost. Never merge parts of both; if each has a needed idea, the
   orchestrator writes a follow-up task for one implementer.
3. **Gate improves 9 documents and regresses 1.** Not mergeable as is. A
   regression is merged only when the integrator can state, per document, why
   the new value is right (e.g. a wrong CLEAN becoming NEEDS_REVIEW) and adds
   an exact allowlist entry; a claim lost, a count or total now wrong, or an
   unexplained change blocks the merge regardless of the 9. Fail closed is
   not a regression; losing accountability is.
4. **Reviewer disagrees with the tests.** The reviewer must produce a
   reproduction; if it reproduces, the tests are wrong or incomplete — the
   adversary adds the failing test and the candidate goes back. If it does
   not reproduce, the orchestrator records why and the review finding is
   closed.
5. **Tests pass, invariant questionable.** Invariants outrank tests. The
   orchestrator states the invariant question in writing; if INVARIANTS.md
   answers it, apply it; if not, it is a product decision — stop and ask the
   human. Never resolve it by editing INVARIANTS.md inside the task.
6. **Implementer and adversary disagree on expected behaviour.** The task
   file decides; if silent, it is an orchestrator decision, recorded in the
   task file before work resumes.
7. **Anyone finds a problem outside scope.** Record it as DEFERRED; never fix
   it in the same branch.

## Token-efficiency rules

- **Stable cached prefix**, byte-identical for every agent and in this order:
  `AGENTS.md`, `docs/agent/REPO_MAP.md`, `docs/agent/CURRENT_STATE.md`. Then
  the role instruction, then the task file. Keeping order and content fixed
  lets provider prompt caching hit across agents and runs.
- **Task-specific context only**: the task file names likely files; an agent
  reads INVARIANTS sections by name, not the whole file.
- **Search before read**: `rg -n` for symbols, then `sed -n 'a,bp'` ranges;
  whole-file reads only for files ≤300 lines or ones being edited.
- **Diffs, not files**, for review: `git diff base...branch` plus
  `git show branch:<file>` for context windows around hunks.
- **Handoffs, not transcripts**, between agents. The handoff template is the
  only thing passed on.
- **Memory on demand**: Remember.md and `git log -p` only to answer a "why".
- **No re-reading after context compaction**: continue from the handoff and
  the diff.
- **Tool output caps**: test runs with `-q` and `| tail`; the full suite
  once per candidate, targeted tests in the loop.

## Escalation to the strongest model

A cheaper implementer stops and hands back (it does not "try harder") when:
- the task needs a semantic decision the task file and INVARIANTS do not make
  (what counts as a claim, a run boundary, a total "of these claims");
- the change touches cross-run identity, account identity, or duplicate
  semantics;
- a schema change is not purely additive, or any stored/measured identity
  (rule ids, categories, conditions, gate fields) would change;
- the synthetic behaviour diff or the gate shows a change it cannot explain;
- two invariants conflict;
- anything touches privacy/security: the gate's seal or collector, telemetry
  vocabulary, redaction, profiles' whitelist, corpus or recording handling;
- two failed attempts on the same failing test.

The orchestrator then re-dispatches to the strongest model with the handoff,
or escalates a product decision to the human.

## Modes

| Mode | When | Agents (models) | Notes |
|---|---|---|---|
| **A. Low cost** | localized fixes, tests-only, docs/tooling; no identity or gate surface | orchestrator (strong, short context) → implementer (cheap/mid) → reviewer (mid, other provider) → integrator (human) | No investigator, no adversary; the implementer's red-first test is the attack test. Gate only if behaviour changed. |
| **B. Normal** | most extraction/reconciliation changes | orchestrator (strong) → [investigator (cheap) if locus unknown] → implementer (mid) ∥ adversary (mid) → gate → reviewer (strong, other provider) → integrator | Adversary and implementer run in parallel from the same base. |
| **C. Hard problem** | cross-run identity, run planning, status/trust semantics, gate/privacy, schema migration, or a mode-B attempt that failed/disputed | orchestrator (strongest) → ≤2 investigators (independent questions) → implementer A (strong) ∥ implementer B (different family) ∥ adversary → gate on both → reviewer (strongest, third provider if possible) → integrator (human) | Two candidates only here; select by conflict rule 2. |

More agents than this did not buy independence or parallelism on LossLift's
tasks: they re-read the same modules and multiply prefix cost.

## Worked example (mode B): adopt the vision-read claim count on scans

Task: a clean scanned report reads NEEDS_REVIEW (R-27) because the claim
count the vision model reads is never treated as the document's printed
count (CURRENT_STATE blind spot 3).

| Step | Agent · model | Worktree / branch | Receives | Produces |
|---|---|---|---|---|
| 1 | Orchestrator · strongest | main (read-only) | prefix, CURRENT_STATE item, INVARIANTS §3/§8 | `agent_tasks/vision-count-r27.md`: scope `core/pipeline.py`, `core/extract_digital.py` (count functions), `tests/`; invariant: a count adopted must be the report's scope, never a section's; corpus expectation: no change (cloud gate has vision off) plus replay run on recorded scans; mode B |
| 2 | Investigator · cheap | detached at base | prefix + "Where is a printed count adopted for digital pages, and where do vision counts enter? Why does R-27 fire on the golden `scanned` fixture?" | memo with the two code paths (file:line), the R-27 condition that fires, a 10-line reproduction |
| 3a | Implementer · mid | `agent/impl-mid/vision-count-r27` | prefix + task + memo | red-first test on the scanned fixture (replayed vision answer), the change, handoff |
| 3b | Adversary · mid (in parallel) | `agent/adversary/vision-count-r27-attack` | prefix + task + INVARIANTS (not the implementation) | attack tests: a section count on a scan must not become the document's count; two scanned runs keep their own counts; a count disagreeing with claims still fires R-05 |
| 4 | Integrator · human | `integration/vision-count-r27` | both branches | attack tests run on the candidate; `scripts/agent_validate.sh`; local gate with `--vision-replay` over recorded scans |
| 5 | Reviewer · strongest, other provider | detached at candidate | diff, handoff, adversary results, gate result | findings + verdict |
| 6 | Integrator | integration branch | all of the above | merge if: no hard fail, attack tests pass, gate exit 0 or every change explained, no blocking review finding; else back to step 3a with the findings (one retry, then escalate to the strongest implementer). Update CURRENT_STATE (blind spot 3 closed), Remember.md entry for the count-scope decision. |

Selecting the winner here is trivial (one candidate). Had a mode-C second
implementer run, both candidates would pass through steps 4–5 and conflict
rule 2 would rank them.
