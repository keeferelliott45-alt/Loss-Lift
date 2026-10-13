> **Draft for counsel/broker review — not legal advice.**

# Privacy impact assessment support pack (Quebec Law 25, PIPEDA, GDPR)

What a client needs from LossLift to complete its own privacy impact
assessment (PIA) before it sends personal information to LossLift, or before
it acquires LossLift as a system. Under Law 25 and PIPEDA the **client** is
the accountable organisation and LossLift is its service provider. Under the
GDPR, LossLift acts as a **processor**. Which regimes apply to a given client,
and whether the GDPR applies to LossLift's own processing, are questions for
counsel.

## 1. Personal information processed

| Category | Example fields | Sensitivity |
|---|---|---|
| Identity of claimants | `claimant_name`, `claimant_ref` | Personal information |
| Description of a loss | `loss_description`, `cause_of_loss` | Can identify a person and describe an event involving them |
| Injury (workers' compensation runs) | `body_part`, `nature_of_injury` | Health-related; treat as sensitive |
| Claim facts | claim number, dates, amounts, status, litigation flag, state of loss | Personal information when linked to a named claimant |
| Business contacts (in saved emails) | sender and recipient names and addresses | Personal information |

LossLift does not collect these from individuals. It receives them inside
documents a client uploads.

## 2. Data flow inventory

| Step | Where it happens | What leaves the machine |
|---|---|---|
| Upload | The LossLift process; one private temporary directory per file | Nothing |
| Digital pages read | The LossLift process | Nothing |
| Scanned pages read | Google Gemini API, **only if a key is configured** | The page image |
| Unknown column format | Google Gemini API, **only if enabled** (`LOSSLIFT_ENABLE_LLM=1`) | Header row and up to 3 sample rows |
| Review and edits | The LossLift process (session memory) | Nothing |
| Export | Downloaded by the user | The workbook, to the user |
| After export | Temporary copy deleted and the deletion verified | Nothing |
| Kept afterwards | Carrier profiles (structure only), local telemetry (counts only) | Nothing |

With no Gemini key configured, **no personal information leaves the machine
LossLift runs on.** Scanned pages are then reported as unread rather than
read.

## 3. Hosting options and transfer outside Quebec

| Option | Where data is processed | Notes for the PIA |
|---|---|---|
| A. Local-first | On the client's own computer, inside its network | No transfer by LossLift. Simplest PIA. LossLift has no access. |
| B. Region-pinned cloud, e.g. AWS `ca-central-1` (Montréal) | In Canada | Not built yet. Requires a signed cloud agreement, access controls and TLS before use. |
| C. Streamlit Community Cloud | Unspecified region | **Not offered for client data**: no DPA, no region choice. |

Remote support from outside Quebec (or outside Canada) can itself count as a
transfer. Under option A, LossLift needs no remote access. Under option B,
any support access, and its location, must be stated in the DPA.

If a client enables the Gemini paths, page images or sample rows go to
Google's API. Google's processing location for that API must be confirmed
against the terms of the tier used (*decision pending*). That transfer must
be in the client's PIA.

**Development caveat (does not involve client data).** LossLift's own
regression checks run its private test corpus of real loss runs on
GitHub-hosted runners in the United States. That corpus is the founder's own
test material. **Client documents are never added to it** unless a client
consents in writing. A DPA should say so.

## 4. Safeguards (with code references)

- Temporary files are deleted after export and the deletion verified.
  Orphans are swept after 24 hours (`core/ingest.py`).
- No database, and no claim data kept after the session (spec §13).
- Carrier profiles are structure only, enforced by a whitelist
  (`core/profiles.py`).
- Telemetry is counts only, schema-checked, local, and can be switched off
  (`core/telemetry.py`).
- Exports can drop claimant names and loss descriptions
  (`core/schema.REDACTED_FIELDS`). One default for every export is *decision
  pending* (recommended: redacted unless the user opts in).
- Files are typed from their bytes, size and page limits apply, and
  spreadsheet formulas are never evaluated.
- Exported text is neutralised against spreadsheet formula injection
  (`core/xlsx_safety.py`).

**Not in place:** user authentication, encryption at rest by the
application, access logging, certifications. See `security-questionnaire.md`.

## 5. DPA checklist (what the service-provider agreement should cover)

- [ ] Purpose limitation: processing only to extract and reconcile the
      client's loss runs, on its instructions.
- [ ] Categories of data (§1) and of data subjects (claimants, contacts).
- [ ] Retention: deletion after export; nothing kept but structure-only
      profiles and counts-only telemetry.
- [ ] Sub-processors named (§2: Google, if enabled; the hosting provider, if
      option B), with prior notice of changes.
- [ ] Location of processing and of any remote access (§3).
- [ ] No use of client data to train models. The AI provider's terms must
      match (paid tier, no training or retention: *decision pending*).
- [ ] No addition of client documents to LossLift's test corpus without
      written consent.
- [ ] Security measures (§4), stated honestly, including what is not in
      place.
- [ ] Confidentiality incident notification to the client without undue
      delay (§6), and cooperation with the client's own notification to
      regulators and individuals.
- [ ] Audit and cooperation: answer the client's due-diligence questions and
      cooperate with a regulator's inquiry through the client.
- [ ] Return or deletion at the end of the engagement (nothing is held, so
      this is a confirmation).
- [ ] Governing law and the contracting entity (*owner to confirm*).
- [ ] French-language versions of client-facing privacy documents on request.

## 6. Confidentiality incident flow (draft)

1. **Detect and contain.** Stop the affected instance. Revoke exposed keys.
   Preserve what is needed to understand the incident.
2. **Record.** Open an incident record with the time found, what happened,
   what data, which client, and what was done. Every incident is recorded,
   including those that turn out to pose no risk. This supports the client's
   own register (Law 25) and breach records (PIPEDA).
3. **Notify the client** without undue delay, with what is known: nature of
   the incident, categories and approximate number of people affected, likely
   consequences, measures taken. Update as facts emerge.
4. **Support the client's assessment** of serious injury (Law 25) or real
   risk of significant harm (PIPEDA). The client decides on and makes the
   notifications to the Commission d'accès à l'information, the Office of
   the Privacy Commissioner and the individuals.
5. **Close.** Root cause, fix, and a note of what changed.

## 7. French-language documents

Client-facing privacy documents (this pack, the DPA, the privacy notice) will
be provided in French on request. *Owner to confirm* the translation route
before the first Quebec client.

## 8. Draft legislation (watch only)

Canada's Bill C-36 would replace PIPEDA's privacy part. It is not law. No
product work depends on it yet.
