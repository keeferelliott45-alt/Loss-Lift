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
  numbers are never read by one. See `docs/security/data-handling.md`.
- The PDFs rebuild byte for byte from the same command. They are git-ignored,
  so build them on the machine you present from.

## The pack

| File | What it shows | Expected badge |
|---|---|---|
| `01-clean-single-report.pdf` | A clean digital loss run. Every row ties to the printed totals. | Reconciled to carrier |
| `02-two-carrier-packet.pdf` | Two carriers in one PDF, each judged on its own policy, term and valuation | Reconciled to carrier, 2 runs |
| `03-mainframe-credits.pdf` | Trailing-minus and `CR` credits read as negatives, exactly | Reconciled to carrier |
| `04-arithmetic-error.pdf` | The carrier's own row arithmetic does not add up (R-01) | Not reconciled |
| `05-foreign-page-packet.pdf` | Another carrier's unnumbered page bound inside a 3-page report (R-28) | Not reconciled |
| `06-scanned.pdf` | A scanned page with vision off: named as unread (R-22) | Not reconciled |
| `07-broker-submission.eml` | A broker's email: two valuations of one account, a duplicate, a non-loss-run | Incomplete, then Complete and reconciled |

After the reliability carrier (#40) merges, `05` reads as two settled runs.
The report's own page count leaves the foreign page out, and that page names
another carrier, so its claim is read into its own run. `--check` will then
report a mismatch. Update the table above, and `DEMO` in `demo/build.py`,
in the same change.

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
carriers; each one is judged on its own facts, never page 1's."

**6. Uncertainty flags. This is the demo working, not failing.** Open `04`:
✗ **Not reconciled**. Expand **Review findings**: R-01 shows expected,
actual and the delta. Pick that finding in **Source evidence** to see the
row. Then open `05` and `06`:

- On `05`, *Loss runs in this PDF (2)* says where one run begins is not
  settled. *What LossLift accounted for* shows that run with *Boundary
  settled: no*, along with its unplaced rows.
- On `06`, **Review findings** has R-22 naming the scanned page that was not
  read.

*Narration:* "This is the product doing its job. A generic AI tool would
have folded that other carrier's claim into this report, or skipped the
scanned page, and shown you a confident total. We refuse to call a document
reconciled until every page and every claim is accounted for, and we show you
exactly where to look. Your underwriter spends 30 seconds on the flag instead
of 30 minutes re-keying."

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
Excel**. It has five sheets: Claim Detail, Loss Summary, Large Loss,
Exceptions and Source Info. Finish with **Delete the uploaded file now**.
*Say:* "Files are deleted after export; nothing is kept by default."

**9. Underwriting triage.** *Placeholder:* Kilo's triage panel (appetite
flags, per-term frequency and severity) is in progress on a separate branch.
Add its click path here when it lands.

## "Send us your messiest packet"

The market plan's live-demo move uses the prospect's own document. Before you
accept one:

- Run locally, with `GEMINI_API_KEY` unset. Vision is then off, so scanned
  pages are named as unread rather than read.
- Delete the file after the call (**Delete the uploaded file now**).
- Do not add it to any corpus, and do not commit it.
- Expect *Not reconciled* on hard documents, and narrate it as in beat 6.
