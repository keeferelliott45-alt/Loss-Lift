> **Draft for counsel/broker review — not legal advice.**

# Continuity plan (key-person risk)

LossLift is built and run by one founder. A customer's procurement will ask
what happens if that person is unavailable. This plan says where everything
lives, how a competent Python developer restores a working system from it,
and what a source-code escrow deposit would contain. Locations are described
by kind. Account names and credentials are kept in the founder's credential
store, never in this document.

## 1. Where everything lives

| Asset | Location | Backed up by | Sensitive? |
|---|---|---|---|
| Source code, tests, synthetic fixtures, docs | GitHub repository `Loss-Lift` (public today) | GitHub; any clone | No |
| Marketing / front-end site | A separate private GitHub repository | GitHub | No customer data |
| Real-document test corpus | Releases of a separate **private** GitHub repository, plus the founder's local copy | GitHub releases | **Yes**: real loss runs. Never in the source repository. |
| Corpus manifest and salt | The founder's machine, outside any repository | *Owner to confirm* an encrypted backup | **Yes** (the salt keys the gate's digests) |
| Recorded vision answers for the corpus | Beside the corpus, outside the repository | *Owner to confirm* | **Yes** |
| Carrier profiles library | `data/profiles/` on each running instance (git-ignored) | Not backed up today | No (structure only, whitelisted) |
| Telemetry | `data/telemetry/` on each running instance (git-ignored) | Not backed up | No (counts only) |
| API keys (Gemini) | `.env` locally or the host's secret store | Re-issuable from the provider console | **Yes** |
| CI secrets (corpus read/write tokens) | GitHub Actions repository secrets | Re-issuable | **Yes** |
| Customer documents | Never stored beyond a session (deleted after export) | Not applicable | Not applicable |

## 2. Restoring a working system

From a clean machine with Python 3.11 or newer:

```bash
git clone https://github.com/keeferelliott45-alt/Loss-Lift.git
cd Loss-Lift
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q                      # the suite must pass
python -m tests.golden.baseline          # exit 0: synthetic accuracy unchanged
streamlit run app.py                     # http://localhost:8501
```

- No key is needed for digital PDFs. For scanned pages, set `GEMINI_API_KEY`
  in `.env` (see `.env.example`).
- Carrier profiles rebuild themselves: the first document of an unknown
  format goes to the mapping screen, and confirming it saves the profile.
- The corpus gate needs the private corpus, its manifest and its salt
  (`docs/corpus-gate.md`). Without them, development can continue on the
  synthetic suite. Changes to extraction or reconciliation then cannot be
  checked against real documents and must not be released to customers.

Expected time for a competent Python developer: one to two hours to a
running system; longer to be productive on the extraction code. The
orientation path is `AGENTS.md`, then `docs/agent/REPO_MAP.md`, then
`docs/agent/CURRENT_STATE.md`.

## 3. If the founder is unavailable during a pilot

- Under the **local-first** posture, the client runs LossLift on its own
  machine. The tool keeps working without the founder. The client holds its
  data and its exports.
- Under a **hosted** posture, the client must be told who holds the hosting
  account and how the service is shut down and the client's data confirmed
  deleted. *Owner to confirm* a named backup contact before any hosted pilot.
- Nothing in LossLift holds client data beyond a session, so no client data
  is stranded.

## 4. Escrow readiness

A source-code escrow deposit would contain:

- a tagged snapshot of the source repository (code, tests, synthetic
  fixtures, prompts, docs);
- `requirements.txt` with the exact versions installed at deposit time
  (`pip freeze`), since the repository declares minimum versions only;
- this restore procedure and the orientation documents named in §2;
- the deposit's commit hash and the test and golden results at that commit.

It would **not** contain the real-document corpus, its manifest or salt,
vision recordings, API keys or CI secrets. Those hold third-party or secret
material and are re-creatable or re-issuable.

With the repository public, escrow mainly guarantees a **specific, verified
snapshot**, kept even if the public repository moves or is withdrawn.
*Decision pending:* whether the repository stays public, which changes how
much escrow adds.

## 5. Gaps to close before a paid pilot

- Encrypted off-machine backup of the corpus manifest, salt and recordings.
- A named backup contact and a sealed credentials handover (for example a
  password manager's emergency access).
- An escrow agent, if a client requires one.
