# Testing, the golden ratchet and the corpus gate

CLAUDE.md §10, `README.md` and `docs/corpus-gate.md` are authoritative.

- Write **focused regression tests** for every behavior change, then run the
  **full suite**: `python -m pytest`. A regression that lowers accuracy is a
  failure even if it stays above the floor.
- Golden ratchet: `python -m tests.golden.baseline` prints per-carrier accuracy
  and fails on any drop below the committed baseline. Refresh it with
  `python -m tests.golden.baseline --update` **only after a real improvement**,
  so the number being defended is the best one reached.
- Accuracy table: `python -m tests.golden.report` (money threshold 99.5%;
  "nulls silently read as zero" must stay 0).
- Golden fixtures are **synthetic**; the expected CSVs under
  `tests/golden/expected/` are hand-written. **Never edit a golden expected
  output, threshold or baseline to make a failing test pass** without explicit
  owner authorization — fix the code or the fixture generator.
- **Cloud corpus gate** (from `main`, `Corpus gate` workflow): inputs are exact
  `baseline_sha`, `candidate_sha`, optional `corpus_tag`, optional `pr_number`
  (candidate must equal the live PR head, checked before and after). Only exit 0
  passes; every other code blocks the change. See `docs/cloud-corpus-gate.md`.
- **Local corpus gate**: `python -m tools.corpus_gate run --baseline <sha>
  --candidate <sha> --corpus <dir> --manifest <file>`. Exit 0 is the only pass.
  Corpus, manifest and allowlist live outside the repository.
- Never weaken a reconciliation check, tolerance or gate to make a change pass.