# Submission workspace (saved-email pilot)

For MGA and program underwriting assistants who receive loss runs as email
attachments. Upload the saved email (`.eml`); LossLift lists every attachment,
reads the loss-run PDFs through the normal pipeline, and summarises them as
one submission you can trace back to the page.

This is the pilot workflow. Live inbox connectors (Outlook, Gmail, IMAP),
OAuth, background processing and AMS/PAS integrations are separate, later
work and are not part of it.

## Workflow

1. **Save the email** from your mail program as `.eml`.
2. **Upload it** on the queue screen. PDFs can still be uploaded directly,
   exactly as before.
3. **Open the submission** under *Submissions from saved emails*. It shows:
   - the status, which is one of:
     - *Complete and reconciled*, qualified when a reviewer set attachments aside;
     - *Needs review*;
     - *Incomplete: attachments outstanding*;
   - **What arrived**: every attachment, with its outcome (accepted,
     rejected, duplicate, failed), whether it was read, and why not;
   - **Outstanding**: everything that stops the submission being complete
     and reconciled;
   - **Documents**: each readable PDF. *Open* goes to the normal mapping and
     review screens, and *Back to submission* returns;
   - **Loss summary**, merged across the documents:
     - named insured, or why it is not established;
     - claims and open claims;
     - total incurred, or why it is not given;
     - valuation dates and printed policy terms;
     - large claims (at or above $25,000) with an *Evidence* button that
       opens the document and page the claim was read from.
4. **Set aside** a rejected attachment you have confirmed is not a loss run.
   The rejection stays on record, and the status says how many were set aside.
5. **Export** the submission workbook. Its sheets:
   - Submission;
   - Attachments (with SHA-256);
   - Documents (per logical run);
   - Accounts;
   - Claims and Large Claims (with attachment, document, run, page and row);
   - Blockers.

   It is redacted by default.

Try it without real data: `python scripts/demo_submission.py`.

## What counts as complete

Intake and trust are separate:

- **Intake**: every attachment is accounted for.
  - A duplicate is resolved: it is read once, through the first copy.
  - A rejected or failed attachment is not resolved until a reviewer sets it aside.
  - An accepted PDF that did not read is never resolved.
  - An email whose MIME structure is damaged, or that hit a limit, is marked
    *not read completely*. It can never be set aside, because attachments may
    be missing from the list.
- **Trust**: every document's status comes from `core.review.canonical_status`,
  and the merged account's status from `core.account`.

"Complete and reconciled" requires both, plus one named insured.

Totals are given only when adding them up means something:

- one named insured;
- one currency;
- no claim whose identity is uncertain (a run that does not print its carrier
  or policy);
- no missing incurred amount.

Otherwise the summary says why there is no total. A claim repeated across
valuations counts once, at its latest valuation (`core.account`). A duplicate
attachment is never read twice. Two insureds are shown separately and never
combined.

## Security and privacy boundaries

- **Parsing**:
  - Python's standard `email` package only.
  - The email body (plain text or HTML) is skipped unread.
  - HTML is never rendered and nothing is fetched or followed.
  - Attachments are never executed or unpacked.
- **Attachment types**:
  - A PDF is recognised by its bytes (`%PDF-`), never by its name or
    declared type.
  - Attached emails, archives, executables and other formats are rejected
    with a plain reason.
  - Accepted PDFs go through the same `core.ingest.ingest` as a direct
    upload, plus an in-memory check that refuses password-protected, damaged
    or over-long files.
- **What is not claimed**: LossLift does not sanitise PDFs and does not
  promise to detect every kind of active content in one.
- **File names**:
  - International names are decoded.
  - A name containing a path, a drive letter or a control character is
    refused, not repaired.
  - Staged files always use generated names, so two different PDFs with the
    same name are both kept.
  - File names shown in the app are escaped, and the inventory is a table,
    not markdown.
- **Limits** (configurable in `core.eml_intake.EmlLimits`), each checked
  before the work it bounds:

  | Limit | Default |
  |---|---|
  | Raw email size | 150 MB |
  | MIME parts | 200 |
  | Nesting depth | 8 |
  | Attachments | 25 |
  | Total attachment bytes (checked on encoded size, before decoding) | 200 MB |
  | One PDF | 64 MB (the existing upload limit) |
  | PDF pages | 1000 (new: the direct-upload path has no page limit) |

- **Errors**:
  - A problem with one attachment rejects or fails that attachment only.
  - An unreadable email, or a breached message-level limit, stops intake
    with a plain message.
  - No message, reason, warning or log line contains email text, headers,
    file names chosen by LossLift's parser, or parser exception text.

## Retained metadata and cleanup

- **Session memory only**:
  - the sender, subject and date the email states;
  - each attachment's display name, declared type, size and SHA-256.

  Nothing is written to disk except the staged PDFs. Telemetry records the
  same document events as a direct upload, and never email metadata.
- **Never kept**: the email body and the raw message.
- **Identifiers**: submission and attachment ids are generated. No header is
  used as an identifier.
- **Staged PDFs** follow the existing lifecycle:
  - deleted when the document is exported or removed from the queue;
  - swept after 24 hours if a session ends unexpectedly.

  A PDF that fails to read is deleted at once. An interruption during staging
  or reading deletes everything that email had staged. Rejected attachments
  are never written to disk.
- **Export redaction** withholds or scrubs, across every cell, the metadata
  and the file name:
  - the sender and the subject;
  - attachment and source file names;
  - the named insured and policy numbers;
  - claimant names and loss descriptions.

  Submission and attachment ids and SHA-256 hashes are kept for the audit
  trail. The workbook name is the submission id.

## Known limitations

- **Forwarded emails:** an attached email is rejected, not opened. Save its
  PDFs and upload them.
- **Accounts:** grouping uses each document's named insured.
  - Documents that name the insured differently form separate accounts until
    reviewed.
  - A document that names no insured is shown as "Insured not named" and
    keeps the submission from reading as complete.
- **Encryption:** password-protected PDFs are rejected; there is no password
  prompt.
- **Setting aside:** the reviewer's decision lives in session memory with the
  submission. It is not written to the document review log.
- **Page limit:** it applies to the email path only. Adding it to direct
  uploads is a separate change.
