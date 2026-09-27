---
description: Prepare a cloud corpus-gate run and interpret its verdict
---
Prepare a cloud corpus gate. Details: $ARGUMENTS

The cloud gate runs from GitHub's browser on the private corpus. It never
commits a document, file name, manifest or salt. See
`docs/cloud-corpus-gate.md`; `docs/corpus-gate.md` decides the verdict.

1. Confirm the change touches extraction or reconciliation. If it does not, the
   gate is not required.
2. Gather the inputs — never fabricate them:
   - `baseline_sha`: the current `origin/main` full 40-character SHA;
   - `candidate_sha`: the exact full SHA under review (not a branch name);
   - `corpus_tag`: blank for the active corpus, or an exact release tag;
   - `pr_number`: the pull request whose **live head must equal**
     `candidate_sha`; the workflow checks this before and after the run.
3. Confirm the corpus and its manifest are **outside the repository** and no
   corpus content is staged, committed or printed.
4. Dispatch the **Corpus gate** workflow from `main` (*Actions → Corpus gate →
   Run workflow*) with those inputs. It is manually dispatched and runs only
   from `main`.
5. Interpret the exit code: **0 is the only pass.** 1 = behavior changed and
   the allowlist does not approve it (or an entry matched nothing); 2 = bad
   command line; 3 = the corpus/manifest/revision cannot be trusted and nothing
   ran; 4 = a revision failed or left something unmeasured. Any non-zero code
   blocks the change.
6. An intended behavior change is approved only by an explicit allowlist entry
   naming the document's manifest id and hash, the field, and both values — one
   entry per change, with a reason. Never approve by relaxing the gate.
7. Report the two SHAs, the corpus release, the verdict and the exit code. Do
   not paste document text.
