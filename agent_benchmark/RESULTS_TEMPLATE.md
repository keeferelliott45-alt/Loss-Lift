# Results — round <N>, <date>

Configurations (record exactly; same prompts and budgets across all):

| Config | Roles → models (provider) | System prompt / harness version |
|---|---|---|
| CC-ULTRA | Claude Code Ultra multi-agent: <roles and models> | <version> |
| OR-A | OpenRouter configuration A: <roles and models> | <version> |
| OR-B | OpenRouter configuration B: <roles and models> | <version> |

Starting commit `b60e93ce5f14f1036888334f6aec6d6c207df02c`; evaluator pack
SHA-256 `<from EVALUATOR_PACK.sha256>`; 3 runs per cell; values are
median (min–max).

## Per task

| Task | Metric | CC-ULTRA | OR-A | OR-B |
|---|---|---|---|---|
| B01 | points / 100 · solved (runs) · hard fails | | | |
| B01 | cost $ · time min · in/out tokens · calls | | | |
| B02 | points · solved · hard fails | | | |
| B02 | cost · time · tokens · calls | | | |
| B03 | points · solved · hard fails | | | |
| B03 | cost · time · tokens · calls | | | |
| B04 | points · solved · hard fails | | | |
| B04 | cost · time · tokens · calls | | | |
| B05 | mutants killed (of 3) · points | | | |
| B05 | cost · time · tokens · calls | | | |
| B06 | equivalence identical (runs) · points | | | |
| B06 | cost · time · tokens · calls | | | |
| B08 | answer points (of 5) · tokens to correct answer | | | |
| **Held out** | | | | |
| B07 | caught / missed / false positives · points | | | |
| H1 | points · solved · hard fails | | | |
| H2 | points · solved · hard fails | | | |

## Summary

| Metric | CC-ULTRA | OR-A | OR-B |
|---|---|---|---|
| Development: median points | | | |
| Held out: median points | | | |
| Solve rate (dev / held out) | | | |
| Hard fails (total) | | | |
| Correctness (of 50), median | | | |
| Engineering quality (of 25), median | | | |
| Autonomy (of 15), median | | | |
| Review quality (of 10), median | | | |
| Interventions / clarifications (total) | | | |
| Total cost $ · cost per solved task $ | | | |
| Median wall-clock per task | | | |
| Input / output / cached tokens (total) | | | |

Per-run raw data: `results_template.csv` (one row per run).
