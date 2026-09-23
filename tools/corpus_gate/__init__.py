"""Real-document corpus A/B gate.

Runs every locally held real loss run through two revisions of LossLift --
each in its own isolated worktree -- and fails unless their behavior is the
same, document by document, or every difference is named in an explicit
allowlist. See docs/corpus-gate.md for the command and what it compares.

Exit codes:

* 0 -- no behavioral change
* 1 -- behavioral changes not approved by the allowlist, or a stale allowlist
* 2 -- the command line was wrong
* 3 -- the corpus, manifest, allowlist or revisions cannot be trusted; nothing ran
* 4 -- a revision crashed, timed out, or left documents unmeasured
"""
