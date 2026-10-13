# E3 — shared PDF preflight

Base B0: `0f13c5c90a66621774000c6eb368df82b10e95cd`.
Branch `agent/sol/pdf-preflight`; PR target `agent/lead/reliability-phase-1`.
Own only `core/ingest.py`, `core/eml_intake.py` and preflight/ingestion,
source/staged/attack lifecycle/email-intake tests. Dedicated synthetic PDF
helper allowed. No pipeline/schema/app/reconciliation/export/qualification edits.
Read repository instructions and relevant provenance/privacy/accountability
invariants. Lead runs the private corpus; do not access its documents.

Shared validator/result/error with fixed privacy-safe reason codes/messages,
compatible with existing IngestError handlers. Defaults: 64 MB, 1000 pages;
keyword-only custom limits without breaking existing callers. Validate actual
bytes staged or the verified path snapshot. Reject opening-password PDFs,
structurally unreadable/malformed magic-prefixed bytes, zero pages and limits.
Allow owner-only encryption. Preserve ownership markers, source-change checks,
duplicates, valid siblings, failed cleanup and BaseException interruption cleanup.
No parser exception text, supplied filename or private path in new errors.

Red tests: valid; exact/over limits; opening/owner passwords; malformed/zero
pages; custom limits; direct/path/email equivalence; siblings; duplicates;
interruption/failed-cleanup paths. Replace magic-only fixture bytes with valid
synthetic PDFs ONLY where tests expect successful staging; malformed rejection
fixtures remain. Do not weaken assertion contracts.

Run targeted, agent_validate/full/golden. Request B0->candidate private gate
from lead. Stop at ownership collision or semantic decisions outside scope.
Commit and draft PR/handoff with full SHAs, tests, risks, deferred items; no merge.
