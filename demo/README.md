# Demo pack and live-demo runbook

For discovery calls and live demos with MGA operations leads. Everything here
is synthetic, rendered from the generators the test suite uses. No real loss
run, name or amount is involved.

## Before the call (5 minutes)

```bash
python -m demo.build --check     # writes demo/out/, re-reads every file
streamlit run app.py
```

- `--check` must end with `check: OK`. If it does not, the app no longer
  behaves the way this runbook says: fix the runbook or the app before
  presenting. `tests/test_demo_pack.py` pins the same expectations.
- Leave `GEMINI_API_KEY` unset. The demo needs no model, and it shows that
  numbers are never read by one. See `docs/security/data-handling.md` (PR #42).
- The PDFs rebuild byte for byte from the same command. They are git-ignored,
  so build them on the machine you present from.

## The pack

| File | What it shows | Expected badge |
|---|---|---|
| `01-clean-single-report.pdf` | A clean digital loss run. Every row ties to the printed totals. | Reconciled to carrier |
| `02-two-carrier-packet.pdf` | Two carriers in one PDF, each judged on its own policy, term and valuation | Reconciled to carrier, 2 runs |
| `03-mainframe-credits.pdf` | Trailing-minus and `CR` credits read as negatives, exactly | Reconciled to carrier |
| `04-arithmetic-error.pdf` | The carrier's own row arithmetic does not add up (R-01) | Not reconciled |
| `05-foreign-page-packet.pdf` | Another carrier's unnumbered page bound inside a 3-page report: read as its own run, the report still ties | Reconciled to carrier, 2 runs |
| `06-scanned.pdf` | A scanned page with vision off: named as unread (R-22) | Not reconciled |
| `07-broker-submission.eml` | A broker's email: two valuations of one account, a duplicate, a non-loss-run | Incomplete, then Complete and reconciled |
| `08-hostile-packet.pdf` | A two-carrier packet with a third carrier's unnumbered page slipped in (fuzz seed 5, `add_unnumbered_run`): its money named (R-23), the boundary flagged (R-28) | Not reconciled, 3 runs |
| `09-scanned-replay.pdf` | The same scanned page as `06`, read through a **recorded** vision answer (`recordings/`): every value marked `vision`, the count left to a person (R-27) | Not reconciled |

Every PDF and recording rebuilds byte for byte from the same command
(`tests/test_demo_pack.py`). `08` is a seeded case from the fuzz harness;
reproduce it with `python -m tests.fuzz.run --case 5:add_unnumbered_run`.
`09` has the same bytes as `06`: the point is the reading path, not the page.
If a change to LossLift makes `--check` report a mismatch, update the table
above and `DEMO` in `demo/build.py` in the same change. The R-27 work on
scanned claim counts is expected to move `09` to *Reconciled to carrier*.

## The demo beats (market plan §13, "The Live Demo")

**1. Upload.** Queue screen (*Loss run queue*): drag `01`–`06` in at once and
click **Add to queue**. Each file gets a status. *Say:* "A hundred at once
works the same; nothing leaves this machine."

**2. A clean document.** On `01`, click **Open**. The badge reads ✓
**Reconciled to carrier**. Point at *Loss snapshot* and *Loss summary by
policy term*: the *Ties to carrier* column says whether the figures match the
subtotal the carrier printed. *Say:* "Green means every number tied back to
what the carrier printed, not that a model felt confident."

**3. Provenance (the moat).** Same document, stage **Review** → expand
**Source evidence** → *Show me where this came from* → pick a claim. The page
appears with that row marked. Set *Field (optional)* to *Incurred* to narrow
it to the cell. *Say:* "Every number on every sheet traces back to a page.
That is what an auditor or the NAIC bulletin asks for."

**4. Messy numbers.** Open `03`. In the **Claims** table, credits are
negative and a blank stays blank, never `0.00`. *Say:* "A blank and a zero
are different facts; we never confuse them."

**5. Packets.** Open `02`. *Loss runs in this PDF (2)* lists each carrier
with its own policy, term and valuation. Under stage **Export**, the Source
Info sheet carries a per-run facts block. *Say:* "Board packets bind several
carriers; each one is judged on its own facts, never page 1's." Then open
`05`: another carrier's unnumbered page sits between pages 1 and 2 of a
three-page report. *Loss runs in this PDF (2)* shows the report on pages
1, 3 and 4 and the foreign page as its own run, and the report's printed
total still ties. *Say:* "The page numbers the carrier printed say which pages
are its report. The odd page out is read on its own, never folded in."

**6. Uncertainty flags. This is the demo working, not failing.** Open `04`:
✗ **Not reconciled**. Expand **Review findings**: R-01 shows expected,
actual and the delta. Pick that finding in **Source evidence** to see the
row. Then open `08` and `06`:

- On `08`, a third carrier's page with no page numbers was slipped into a
  two-carrier packet. *Loss runs in this PDF (3)* shows that page as a run
  whose boundary is not settled. *What LossLift accounted for* shows the
  money on it as unplaced (R-23), and R-28 names the boundary. LossLift does
  not guess which report the page belongs to.
- On `06`, **Review findings** has R-22 naming the scanned page that was not
  read.

*Narration:* "This is the product doing its job. A generic AI tool would
have folded that other carrier's claim into this report, or skipped the
scanned page, and shown you a confident total. We refuse to call a document
reconciled until every page and every claim is accounted for, and we show you
exactly where to look. Your underwriter spends 30 seconds on the flag instead
of 30 minutes re-keying."

**6b. A scan, read by vision (optional).** The live app reads scans only
with a Gemini key, which the demo leaves unset. To show what a vision reading
looks like, build with `python -m demo.build --check --scan-workbook` and open
`demo/out/09-scanned-replay.xlsx` in Excel. It is `09` read through its
recorded answer, with no model call. Every claim's method is `vision`, and the
document is *Needs review* (R-27): the model's claim count is not taken on
trust. *Say:* "A scan is the least trustworthy input, so we mark every number
read off one and still ask a person to confirm the count."

**7. Account view (saved email).** Back on the queue, upload
`07-broker-submission.eml` and click **Add to queue**. Under *Submissions
from saved emails*, click **Open**. Then:

- *What arrived* lists all four attachments: two read, one duplicate (read
  once), and one rejected spreadsheet.
- *Outstanding* names the rejected spreadsheet. Tick *Attachment 4 is not
  needed*, and the status becomes Complete and reconciled, qualified by one
  set aside.
- *Loss summary* shows one insured across both valuations, with open claims
  and total incurred at the latest valuation.
- *Large claims* lists anything ≥ $25,000. Click **Evidence** to open the
  exact page.

**8. Export.** Stage **Export** on any document. Turn on *Remove claimant
names and loss descriptions* (off by default; see #42), then **Download
Excel**. It has six sheets: Claim Detail, Loss Summary, Large Loss,
Exceptions, Review History and Source Info (a packet adds Runs). Finish with **Delete the uploaded file now**.
*Say:* "Files are deleted after export; nothing is kept by default."

**9. Underwriting triage.** *Placeholder:* Kilo's triage panel (appetite
flags, per-term frequency and severity) is held on a separate branch
(`agent/kilo/triage-intel`). When it merges, add its click path here: where it
appears on the accounts screen, which demo document drives it, and what the
narration says. Until then, skip this beat.

## Narrating *Needs review*

Most real loss runs will come back *Needs review* at first. Say so before the
prospect does:

- "Amber is the product working. Green is a promise, so we only give it when
  every claim and every printed total is accounted for."
- "Each flag says what is wrong and where. Most take seconds to confirm."
- "Confirming a flag records that a person looked at it. It never turns a
  failing check green. Your carrier can see both."
- Never call a flag a bug in front of a prospect. If it really is an
  over-flag (correct data flagged), say "that one is conservative" and note
  the document for follow-up.

## "Send us your messiest packet"

The market plan's live-demo move uses the prospect's own document. Before you
accept one:

- Run locally, with `GEMINI_API_KEY` unset. Vision is then off, so scanned
  pages are named as unread rather than read.
- Delete the file after the call (**Delete the uploaded file now**).
- Do not add it to any corpus, and do not commit it.
- Expect *Not reconciled* on hard documents, and narrate it as in beat 6.
