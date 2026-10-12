# Task: Stage A — refresh PR #18 onto main and make it merge-ready

## Objective
Create a clean integration candidate for PR #18 by merging `origin/main`
(`c7de782`) into PR #18's head (`597ff9d`) on a new branch, fix the one
diff-owned test that fails on lxml environments, and run the full suite + golden
ratchet so the candidate is merge-ready (gate NOT required for #18).

## Why it matters
The independent audit found #18 changes export/UI and `.eml` intake only — no
extraction/reconciliation change, so the private corpus gate is NOT required —
but (a) PR #18's branch does not contain current main, and (b) one diff-owned
test (`tests/test_export_security.py:129`) asserts a raw `\r` byte in the sheet
XML that lxml serialises as `&#13;`, so it fails on any lxml-equipped machine and
blocks a trustworthy suite-green claim. The branch also has no CI checks.

## Allowed scope
- `tests/test_export_security.py` — fix the serializer-fragile assertion only,
  keeping the security property (quoted text / no `<f>` / quotePrefix) asserted.
- Merge `origin/main` into the #18 head; resolve conflicts without changing #18's
  intent and without touching extraction/reconciliation.
- `docs/agent/CURRENT_STATE.md` only if a real milestone changes.

## Likely files
- `tests/test_export_security.py` — the failing assertion.
- `core/xlsx_safety.py` (read only) — confirm the property actually holds.
- Any merge-conflict files between main and `597ff9d`.

## Invariants that must hold
Formula-injection safety must remain asserted against raw package bytes
(no `<f>`, `quotePrefix="1"` in styles, value stored as text). Do not weaken the
test's security claim; broaden the assertion to accept `&#13;=1` OR raw `\r=1`.
Preserve provenance/redaction guarantees. No rule-id/field renames.

## Required tests
- Red evidence: run `tests/test_export_security.py` on the refreshed branch
  BEFORE the fix and capture the exact failure.
- After the fix: `python -m pytest -q -p no:cacheprovider --basetemp=... tests/test_export_security.py`
- Full suite + `python -m tests.golden.baseline`.

## Corpus expectations
Not applicable: export/UI/intake/tests only — private corpus gate NOT required
(the audit concluded this; record the reasoning). Record other suite failures and
state whether each is pre-existing on main (Windows/env) or caused by the merge.

## Definition of done
- [ ] Branch merges `origin/main` into `597ff9d` cleanly
- [ ] `test_export_security.py` red before, green after, security property intact
- [ ] Full suite run with counts; every failure classified pre-existing vs new
- [ ] Golden ratchet green
- [ ] Handoff written

## Prohibited scope expansion
- No behaviour changes beyond the test fix; do not merge #21 here; no
  manifest/allowlist/labels/golden edits; do not touch other worktrees.

## Environment
- Python: the local Python 3.14 interpreter with every dependency installed (`py -3`)
- pytest basetemp: `--basetemp=%TEMP%\kilo\pytest-a18` (fresh per run)
- PR #18 head sha: `597ff9dd12c1c5cdda32b4dbe7f5fbfed3993241`

## Handoff
Use `docs/agent/HANDOFF_TEMPLATE.md`.
