# Discovery — what LossLift needs to become an account loss-history tool

Desk discovery, 2026-11-01. No customer calls yet. Every market statement is
marked **known** (sourced), **believed** (unverified) or **investigate**.

## Product hypothesis

LossLift turns whatever loss runs arrive for one account (an email, several
PDFs, a spreadsheet) into a **verified, consolidated 3–5 year loss history in
Excel**, plus a list of what is missing or does not tie. It replaces the job of
assembling a loss history. It does not replace the underwriter's workflow.

- **Problem owned:** is this account's loss history complete, current and
  correct?
- **Target user (believed):** the person who builds the loss summary, such as
  an underwriting assistant at a wholesale broker or small MGA, or a retail
  account manager. The underwriter reads what they build.
- **Not the product:** a single-document extractor, an intake platform or a
  pricing engine.

## Gap matrix: target versus what is built

Target: one account, from intake to export, in under 10 minutes including
review.

| # | Capability | Status | Where / what is missing |
|---|---|---|---|
| 1 | Intake of PDF, xlsx, csv and saved email | **Built** | `core/ingest.py`, `core/eml_intake.py` |
| 2 | Reading the target segment's top carriers | **Unknown** | 6 real documents, all NEEDS_REVIEW, money accuracy never measured. The target carrier list does not exist yet |
| 3 | Account assembly (carrier + policy + claim identity, no double count) | **Built** | `core/account.py`. Weakened by carrier detection (plan gap 5) |
| 4 | Development between valuations | **Built** | `AccountRollup.claim_lines[].valuations`, T-03 |
| 5 | Claims that stop appearing | **Built** | T-05 |
| 6 | Coverage gaps between policy terms | **Built** | `intelligence._period_gaps` |
| 7 | **Lookback check**: are the requested N years (default 5) covered? | **Missing** | No configurable lookback |
| 8 | **Currency check**: is each run valued within N days (default 90)? | **Missing** | Valuation dates are shown but not judged |
| 9 | **Account loss summary by policy year across carriers** (claims, open, paid, reserve, incurred, large losses) | **Partial** | `summarise_by_period` works per document. The Accounts sheet has one row per account, with no per-year table |
| 10 | **Loss ratio** | **Missing** | Schema has no premium. Loss runs rarely print it, so it is a manual entry per term carrying `manual` provenance |
| 11 | **Request list for the broker** ("send 2022 GL, valued after 2026-08-01") | **Missing** | Reasons exist (`account.reasons`), but nothing phrases them as an exportable request list |
| 12 | Account-first entry | **Partial** | The account panel appears only when an insured has more than one named run. The home screen is the document queue |
| 13 | Output in the customer's loss-summary layout | **Partial** | Claim-detail column templates exist. There are no loss-summary layouts, and customer layouts are unknown |
| 14 | One-page printable account summary | **Missing** | |
| 15 | Reviewer decisions | **Missing** | Plan Phase 3. Every real document reads amber today |
| 16 | Comparison with last year's renewal | **Blocked** | §13 rules out persistence. It works only when all runs are uploaded in one session |
| 17 | Try-it access | **Missing** | Local only. Hosting is constrained by §8 and §2.6/§9 |

Items 7–11 and 14 are additive. They sit after extraction and reconciliation,
so they do not need the corpus gate. They do touch `core/account.py`, which is
on the top-tier routing list in the plan's §5.

## Desk findings

- **Lookback and currency (known, varies by carrier):** 3–5 years of history.
  Valuation within 30, 60 or 90 days of the application is common. Make both
  configurable, defaulting to 5 years and 90 days.
  ([Foresight](https://getforesight.com/blog/loss-runs/),
  [Racklify](https://racklify.com/encyclopedia/what-is-a-loss-run-definition-purpose-and-how-underwriters-use-it/))
- **Underwriters weigh loss ratio over claim count (believed, one source).**
  That makes gap 10 (premium entry) high value.
- **ACORD 125 shape (known):**
  - The prior-carrier section asks for carrier, policy number, premium, and
    effective and expiration dates per year.
  - The loss-history section asks for date of occurrence, line of business,
    description, paid, reserved, subrogation, and open or closed.

  The account output maps almost one to one onto those sections. An
  "ACORD 125-shaped loss history table" is a strong output, but it comes close
  to §13's ban on ACORD auto-fill: a table, not a filled form.
  ([Merchants Group](https://www.merchantsgroup.com/edu/university/acord-forms/acord-125/),
  [TotalCSR](https://www.totalcsr.com/how-to-complete-the-acord-125/))
- **Competition (known):**
  - Extraction vendors are many (FurtherAI, Sensible, Heron, Outmarket, V7,
    Nomad, Sonant).
  - Nomad markets cross-carrier prior-loss consolidation.
  - Vantage Agora launched a loss-analysis tool for brokers and wholesalers in
    2015 (**investigate** whether it is still active).
  - No vendor found advertises development between valuations, or a
    completeness and currency check with a request list (**believed** gap).
  ([Nomad](https://www.nomad-data.com/doc-chat/unlocking-cross-carrier-prior-losses-in-submission-documents-for-property-homeowners-commercial-auto-and-general-liability-underwriter-guide),
  [Insurance Journal](https://amp.insurancejournal.com/news/national/2015/03/26/362211.htm))
- **Price anchors (known, thin):**
  - Loss Run Pro, a loss-run retrieval tool, costs $29/month.
  - MGA platforms run from $65/user/month to $999/month.
  - An offshore VA costs about $8/hour.

  Pricing hypothesis to test: $49–149/user/month, or per account.
  ([Loss Run Pro](https://lossrunpro.com/),
  [Capterra](https://www.capterra.com/p/10023620/MGA-underwriting-workbench/))
- **Retrieval is a separate market (known):** Loss Run Pro and FurtherAI pitch
  getting loss runs from carriers in less than the usual 7–10 days. §13 keeps
  this out. Note it as an adjacent wedge we are choosing not to take.

## What only customers can answer (interview kit)

Ask the person who builds loss summaries. Run 8–12 calls. Collect blank
templates only, never claim data (spec §9).

1. Walk me through the last account where you assembled loss history. How many
   loss runs, from how many carriers, and over how many years?
2. How long did it take, and which part took longest: reading, re-keying,
   chasing what was missing, or checking?
3. What did you produce, and where did it go? Ask for a blank template.
4. What has gone wrong: a stale run, a missing year, a duplicated claim, a
   wrong total?
5. Which 10 carriers' loss runs do you see most?
6. Who does this today (you, a VA, a vendor), and what does it cost?
7. Show the 3-minute demo. What would stop you using this on live work
   tomorrow?

**Decision gate:** at least 4 of 10 describe multi-carrier assembly as a
recurring pain, and at least 2 would try it on live work. If the gate passes,
continue to the account MVP. If it fails, reconsider: sell LossLift as a
component to a rating or AMS vendor, or keep it as a portfolio piece.
