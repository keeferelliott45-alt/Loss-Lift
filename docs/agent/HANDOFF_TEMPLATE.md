# Handoff

Every task ends with this, filled in, in the final message (and PR body if
one is opened). Be factual; no document text, no private data.

```
TASK:                 <task file / one line>
BRANCH:               agent/<agent>/<slug>
BASE COMMIT:          <sha the worktree was created from>
FILES CHANGED:        <path — one-line purpose, per file>
BEHAVIORAL CHANGE:    <what LossLift now does differently; "none" if tooling/docs only>
INVARIANTS AFFECTED:  <INVARIANTS.md sections touched, and how they still hold>
TESTS RUN:            <exact commands and pass/fail counts; scripts/agent_validate.sh result>
CORPUS RESULT:        <gate run + verdict, changed documents explained; or "not run: <why>">
KNOWN RISKS:          <what could still be wrong>
DEFERRED ITEMS:       <found but out of scope; not done>
NEXT RECOMMENDED STEP:<one step>
COMMITS:              <sha subject, one per line>
```
