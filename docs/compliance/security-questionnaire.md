> **Draft for counsel/broker review — not legal advice.**

# Security questionnaire (SIG-Lite / CAIQ style)

Answers reflect the code on `main` and the way LossLift is operated today:
pre-revenue, one founder, no production customer data. **"No" means no.**
Where an answer depends on a decision not yet made, it says *Owner to
confirm* or *Decision pending*. Code references are given so each answer can
be checked.

## A. Company and governance

| # | Question | Answer |
|---|---|---|
| A1 | Legal entity contracting with the customer | *Owner to confirm.* Not yet formed for insurance contracting. |
| A2 | Information security policy | No written policy yet. The engineering rules that bind every change (fail closed, private data never committed, one status policy) are in `AGENTS.md` and `docs/agent/INVARIANTS.md`. |
| A3 | Certifications (SOC 2, ISO 27001) | No. Not started; planned once paying customers ask for it. |
| A4 | Cyber and Tech E&O insurance | No. *Owner to confirm* before any paid pilot that touches real loss runs. |
| A5 | Staff with access to customer data | One founder. No employees or contractors. AI coding agents work on the source code and on synthetic data only; they are given no customer documents. |
| A6 | Background checks | Not applicable (no staff). |

## B. Data

| # | Question | Answer |
|---|---|---|
| B1 | Customer data processed | Loss runs: carrier, insured, policy, claim numbers, dates, amounts, claim status, **claimant names**, **loss descriptions**. Workers' comp runs can carry **body part and nature of injury** (health-related). |
| B2 | Where it is stored | In the running process's memory and one private temporary directory per upload (`core/ingest.py`). No database (by design, spec §13). |
| B3 | Retention | Uploaded bytes are deleted and the deletion verified on export, on *Delete*, or on removal from the queue. Orphans from an interrupted session are swept at the next start after 24 hours. Extracted data lives only for the session. Details: `docs/security/data-handling.md`. |
| B4 | Data kept after a session | Carrier **profiles** (column labels, formats, carrier name only; an explicit whitelist in `core/profiles.py` refuses anything else) and local **telemetry** (counts, flags and fixed tokens only; schema-checked; never sent anywhere; off with `LOSSLIFT_TELEMETRY=off`, `core/telemetry.py`). Neither holds claim data. |
| B5 | Used to train models | No. LossLift trains no model. Whether a model provider may use submitted content depends on the provider tier: see D1. |
| B6 | Redaction | Claimant names and loss descriptions can be dropped from every export (`core/schema.REDACTED_FIELDS`). Today the default differs by export. *Decision pending:* redact by default everywhere. |
| B7 | Data segregation between customers | Each upload has its own private directory and session. No multi-tenant storage exists, because there is no storage. |
| B8 | Customer data in development or testing | No. Tests use synthetic documents only. Real loss runs used for regression checking are the founder's own test corpus, held outside the source repository (see F4). |

## C. Access control

| # | Question | Answer |
|---|---|---|
| C1 | User authentication in the application | **No.** No user accounts (spec §13). Anyone who can reach a running instance can use it. A pilot must run where only the customer can reach it (locally, or behind the customer's own access control). |
| C2 | MFA | Not applicable to the application (C1). *Owner to confirm* MFA on the source-hosting and cloud accounts. |
| C3 | Role-based access | No. |
| C4 | Privileged access to production | No production environment exists yet. |

## D. Sub-processors and AI providers

| # | Question | Answer |
|---|---|---|
| D1 | AI / LLM providers | **Google Gemini API**, only when a key is configured. (a) Scanned pages are sent as page images (300 DPI) for transcription. (b) Optional and off by default (`LOSSLIFT_ENABLE_LLM=0`): an unrecognised header row and up to three sample data rows are sent for column mapping. Without a key, no document content leaves the machine and scanned pages are reported as unread. *Decision pending:* use a paid tier whose terms exclude training and retention of submitted content, or run pilots with both paths off. The free tier's terms may allow use of content to improve the provider's products, so it **must not** be used with customer data. |
| D2 | Zero-retention terms with the AI provider | **Not in place.** See D1. |
| D3 | Hosting provider | *Decision pending.* Options: the customer's own machine (no sub-processor), or a region-pinned cloud deployment. Streamlit Community Cloud will not be used for customer data. |
| D4 | Source hosting | GitHub. The repository holds code and synthetic test data only. It is **public** today (*decision pending* on visibility). |
| D5 | Full sub-processor list | Today, for customer data: none, or Google (D1) if the vision/LLM paths are enabled, plus the hosting provider once chosen (D3). |

## E. Encryption

| # | Question | Answer |
|---|---|---|
| E1 | In transit | The application relies on its host. Locally it serves on `localhost`. A hosted deployment must terminate TLS (*decision pending*, D3). Calls to the Gemini API use HTTPS. |
| E2 | At rest | Not encrypted by the application. Temporary files are deleted after export (B3). At-rest encryption depends on the host's disk (*decision pending*). |
| E3 | Key management | API keys are read from environment variables or the host's secret store and never committed (`.gitignore` covers `.env` and `.streamlit/secrets.toml`). |

## F. Secure development

| # | Question | Answer |
|---|---|---|
| F1 | Change control | Every change is a pull request. Required checks: test suite, synthetic accuracy ratchet, private-data scan of the diff (`scripts/agent_validate.sh`). Changes to extraction or reconciliation are also compared on real documents (F4). |
| F2 | Input handling | File type is decided from the bytes, not the name. Size and page limits apply (64 MB, 1,000 PDF pages). Legacy and macro-enabled Excel formats are refused, and formulas are never evaluated. Email intake is parsed under limits and accepts PDF attachments only. |
| F3 | Output safety | Every exported cell passes one text policy: formula-like text is stored as text, and control characters are replaced and counted (`core/xlsx_safety.py`). |
| F4 | Testing with real data | A private corpus of real loss runs, held outside the repository, is used only to detect changes between two versions. Its runs are sandboxed: no network, no credentials, read-only mounts. They publish only ids, counts and keyed digests, never document text (`docs/cloud-corpus-gate.md`). These runs execute on GitHub-hosted runners (United States). |
| F5 | Dependency management | Python dependencies are declared in `requirements.txt` with minimum versions, not pinned. No automated vulnerability scanning yet. |
| F6 | Penetration testing | No. |

## G. Logging, monitoring and incidents

| # | Question | Answer |
|---|---|---|
| G1 | Audit logging of user activity | No access logs. Inside a session, every edit keeps the original value, and every review decision is recorded with its time. Both are exported in the workbook (Review History sheet). |
| G2 | Operational telemetry | Local, privacy-safe counts only (B4). |
| G3 | Incident response plan | No written plan yet. Draft notification flow: `law25-pia-support-pack.md` §6. |
| G4 | Breach notification to customers | Commitment proposed in the DPA checklist: notify without undue delay, with what is known, and update as facts emerge. |

## H. Business continuity

See `continuity-plan.md`.
