---
description: Verify a LossLift pull request at an exact head against the required checks
---
Verify PR or commit: $ARGUMENTS

Verify the change at an exact revision and report evidence. Do not merge and do
not mark anything ready.

1. Resolve the full 40-character candidate SHA (the PR's live head, not a
   branch name). Resolve the baseline as the current `origin/main` SHA. Record
   both.
2. Confirm the working tree is clean and the candidate SHA is checked out.
3. Run the full suite: `python -m pytest`.
4. Run the golden ratchet: `python -m tests.golden.baseline` (non-zero exit is
   a regression). Run `python -m tests.golden.report` and confirm the money
   threshold passes and "nulls silently read as zero" is 0.
5. If extraction or reconciliation changed, prepare the cloud corpus gate with
   both exact SHAs and the PR number (see `/corpus-gate`), or run the local
   gate: `python -m tools.corpus_gate run --baseline <sha> --candidate <sha>
   --corpus <dir> --manifest <file>`. Exit 0 is the only pass.
6. Review the diff for: silently lost/fabricated/merged/reassigned claims or
   amounts, weakened reconciliation checks or tolerances, edited golden
   expected outputs or baseline, and any committed secret or corpus content.
7. Report: exact baseline and candidate SHAs, each command run with its exit
   code, the corpus-gate verdict, and any unresolved concern. State plainly if
   anything could not be verified.
