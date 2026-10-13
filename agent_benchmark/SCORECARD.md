# Scorecard

Every item is observable. Subjective items use the anchored scales below and
two blind graders. Quality and cost are reported separately and combined
only as "cost per solved task", so a cheap stack cannot buy score with
failures and an expensive one cannot hide its cost.

## Correctness (per run; 50 points)

| Item | How measured | Points |
|---|---|---|
| Hidden checks | fraction of the task's hidden checks passing (B05: mutants killed; B06: equivalence identical; B07/B08: answer-key points) | 25 |
| Visible suite + golden | full suite and `python -m tests.golden.baseline` pass | 10 (0 if either fails) |
| Corpus gate | exit 0, or every changed document explained correctly against the expectation | 8 (0 if an unexplained or wrong change) |
| Invariants preserved | no INVARIANTS.md item weakened (grader checklist + synthetic behaviour diff shows no unintended change) | 7 |

**Hard fail (run scores 0 correctness):** a claim silently lost, a document
reading CLEAN that the hidden checks require not to, private data committed,
a test skipped/xfailed/loosened to get green, or a force-push/merge.

## Engineering quality (per run; 25 points)

| Item | Anchors | Points |
|---|---|---|
| Scope discipline | lines outside Allowed scope, and in-scope lines the change does not need, against the task's maximum: 5 within limit, 3 ≤2× limit, 0 beyond | 5 |
| Unnecessary refactoring | 5 none, 3 cosmetic only, 0 renames/moves unrelated code | 5 |
| Maintainability | 5 reads like the surrounding code, one clear mechanism; 3 works but awkward/duplicated; 0 obscure or fragile | 5 |
| Test quality | 5 tests fail on the starting commit, test behaviour (not implementation), cover the regression guard; 3 partial; 0 none or tautological | 5 |
| Unsafe assumptions | 5 none; 3 an assumption stated in the handoff; 0 an unstated guess that changes semantics (e.g. inventing a boundary, borrowing another run's fact) | 5 |

## Autonomy (per run; 15 points)

| Item | Measure | Points |
|---|---|---|
| Human interventions | 0 → 8, 1 → 4, ≥2 → 0 | 8 |
| Clarification requests | 0–1 → 4, 2 → 2, ≥3 → 0 (a question the FAQ answers is not penalised when the answer changes the work) | 4 |
| Failed attempts / retries | orchestrator-level re-dispatches of the same subtask: 0 → 3, 1 → 2, ≥2 → 0 | 3 |

## Review quality (per run; 10 points; B07 and any task where a reviewer role ran)

| Item | Measure | Points |
|---|---|---|
| Bugs caught | seeded/real defects found with correct location and failure mode ÷ total | 6 |
| Bugs missed | reported as a count (the complement of the above) | — |
| False positives | 4 − 1 per finding that flags correct code as a defect (min 0) | 4 |

For tasks without a reviewer role, the 10 points are scaled out (score ÷ 90).

## Efficiency (recorded, never added to the points)

| Metric | Source |
|---|---|
| Wall-clock time | start of first model call → handoff |
| Input tokens, output tokens | provider usage logs, summed over every agent and model |
| Cached input tokens | provider usage logs (report separately) |
| Dollar cost | tokens × the provider's list price on the run date |
| Agent/model calls | count of model requests; number of agents spawned |

Derived: **cost per solved task** = total cost of all runs ÷ tasks solved
(a task is solved when ≥2 of 3 runs pass every hidden check), and **tokens to
correct answer** for B08.

## Aggregates

Per configuration: median points per task; solve rate; hard-fail count;
median cost and time per task; cost per solved task; held-out results
reported separately from development results. Differences of fewer than 5
points on a task's median are reported as ties.
