# Remember.md — decisions and why (read on demand)

Historical context, retrieved only when a task needs to know *why*. Not
startup reading. Rules live in `AGENTS.md`, the present in
`docs/agent/CURRENT_STATE.md`. Newest first; one entry per decision; add, don't
rewrite. Never record document text or private-corpus details here.

- **2026-09 — Conventions stay per document; per-run borrowing is flagged.**
  Per-run locale/date/recovery/profile inference was too large for the
  run-propagation cycle. `_borrowed_conventions` + run-scoped R-15 make the
  assumption loud instead. Real corpus: no document affected.
- **2026-09 — Account identity = carrier + policy (number or covering term) +
  claim number.** Merging on claim number alone collided carriers. Unknown
  carrier/policy is kept apart and flagged, never merged, never silently
  double-counted. Reconcile's cross-run R-11 deliberately still flags repeats
  across carriers (a summary page repeating a claim is the bigger risk there).
- **2026-09 — One status policy in `core.review`.** The app used "no
  financial/extraction findings", the workbook used "no ERROR"; an extraction
  WARN read differently in each. The engine status stays ERROR-only (spec §6);
  `canonical_status` is what every layer shows.
- **2026-09 — Run facts never borrowed.** `run_view` used the document's
  (page 1) valuation/term; R-09/R-06 judged a run by another report's facts.
  A run without its own valuation now raises R-06 for itself.
- **2026-09 — Restart under an undiscriminating heading is ambiguous.** Codex
  showed generic-word carrier names and partial name overlaps merging two
  reports as "sections" into a CLEAN packet. `same_heading` is True only on
  equal naming words; otherwise the restart is R-28. Cost: a single report
  exported page by page ("Page 1 of 1" each, letterhead on page 1 only) now
  reads NEEDS_REVIEW.
- **2026-09 — Boundaries from page furniture only.** Page-number text in body
  prose or table cells split reports (P1). Only header/footer bands count;
  vision labels must be placed there and restate the numbers.
- **2026-09 — Corpus gate normalises representation.** Absent `runs`,
  `runs=[]` and a single report measure alike; a revision without the policy
  functions is measured under a mirrored fallback (pinned by a drift test), so
  representation changes never read as behaviour changes.
- **2026-09 — Cloud gate runs main's code.** The candidate never controls the
  gate or sees the salt; consequence: gate improvements take effect in the
  cloud only after merge.
- **2026-09 — Vision recordings keyed by document hash, page, DPI, model,
  prompt and schema.** A prompt change invalidates recordings (replay fails
  closed) rather than replaying stale answers.
- **Spec — Reconciliation over extraction.** The product is the verification
  layer (spec §1, §6); R-04/R-05 are the checks that verify against what the
  carrier printed.
