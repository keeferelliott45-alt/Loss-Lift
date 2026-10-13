> **Draft for counsel/broker review — not legal advice.**

# Pre-sale compliance pack

Five drafts a prospective customer's vendor review (or its carrier's) usually
asks for before a pilot. Every statement describes what the code on `main`
does and names where it lives, or says plainly that something is not in place.
Nothing here is a marketing claim, and none of it has been reviewed by counsel.

| File | Answers |
|---|---|
| [`system-description.md`](system-description.md) | What LossLift is, where AI is and is not used, the data flow, the human-review posture. Arranged to answer NAIC AI Model Bulletin §3.2 / §4.1 style questions. |
| [`validation-summary.md`](validation-summary.md) | The test and accuracy evidence as it stands, including what has **not** been measured. |
| [`security-questionnaire.md`](security-questionnaire.md) | SIG-Lite / CAIQ-style answers about the actual security posture. "No" is given where the answer is no. |
| [`law25-pia-support-pack.md`](law25-pia-support-pack.md) | Data-flow inventory, hosting options, a DPA checklist and an incident flow, for a client's Law 25 / PIPEDA / GDPR assessment. |
| [`continuity-plan.md`](continuity-plan.md) | Key-person risk: where everything lives, how to restore it, and what an escrow deposit would contain. |

The factual base for the data handling statements is
`docs/security/data-handling.md` (PR #42). Where the two differ, the code wins:
re-check against it before sending either document to a customer.

## Open owner decisions that change these drafts

1. **Model provider terms.** Which Gemini tier, if any, is used with customer
   documents, or whether the LLM and vision paths stay off for pilots.
   This decides the sub-processor list and the "zero retention" answers.
2. **Hosting posture.** Local-first on the customer's machine, or a
   region-pinned deployment (for example AWS `ca-central-1` for Canadian
   clients). Streamlit Community Cloud is not suitable for customer data
   (no DPA, no region choice).
3. **Redaction default.** One default for every export (the recommendation is
   redacted unless the user opts in).
4. **Repository visibility.** The source repository is public on GitHub
   today. That affects the escrow answer and what a competitor can read; it
   does not expose customer data, because none is in it.
5. **Insurance and entity.** Tech E&O and cyber cover, and the contracting
   entity, are not in place. Several answers below depend on them.
