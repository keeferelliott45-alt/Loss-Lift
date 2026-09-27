# LossLift — product direction

CLAUDE.md is the authoritative spec. These rules do not restate it; they keep
work pointed at the same product.

- LossLift is a **buyer-neutral trusted loss-history engine**. Its promise is
  explicit accountability for every logical run, claim and financial amount:
  source-verifiable evidence, reconciliation against the totals the carrier
  printed, and fail-closed uncertainty.
- Do not position or configure it as an MGA platform, a broker-only product,
  or a generic OCR tool.
- Do not plan or build ACORD/SOV ingestion, carrier portals, BMS integrations,
  underwriting rules, or other workflow-specific expansion without customer
  evidence. The spec's out-of-scope list (§13) still binds.
- The value is **reconciliation, not extraction**. The verification layer is
  the product.
- The central measurement objective is the **AUTO-SAFE vs NEEDS_REVIEW vs
  unresolved rate, broken down by document class**. In code, AUTO-SAFE is the
  document-level `CLEAN` status (`core.schema.DocumentStatus.CLEAN`, no ERROR
  finding); NEEDS_REVIEW is any ERROR finding; unresolved is a page or document
  the pipeline could not measure.
- Engineering priorities come from **corpus frequency and severity**, not
  unlimited speculative edge-case hardening. Use the benchmark and corpus gate
  evidence to choose what to fix next.