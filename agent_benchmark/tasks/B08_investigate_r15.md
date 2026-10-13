# B08 — Why is this packet not reconciled? (token-efficient investigation)

| Field | Value |
|---|---|
| Starting commit | `b60e93ce5f14f1036888334f6aec6d6c207df02c` |
| Category | investigation — read-only |
| Allowed scope | none (no code changes) |
| Max unrelated diff | n/a |
| Corpus expectation | n/a |
| Difficulty | low–medium |
| Known correct solution | yes (hidden answer key, 5 points) |
| Acceptance | answer points; tokens and time to a correct answer |

## Prompt
The evaluator gives you `b08_scenario.py`. From the repository root,
`PYTHONPATH=. python b08_scenario.py` builds a synthetic two-report packet;
both reports' printed totals tie, yet the packet reads NEEDS_REVIEW. Explain
precisely: which finding(s) cause it, on which run, where in the code they
are produced and why, what evidence from the document the decision rests on
(with the actual values), why that finding blocks trust, and the smallest
change to the input document (not the code) that would make it CLEAN. Be
economical: search before reading, and do not change any code.
