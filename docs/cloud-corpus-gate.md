# Cloud corpus gate and corpus updates

Everything in [corpus-gate.md](corpus-gate.md) still decides the verdict. This
page is how it runs without a local terminal: from GitHub's browser, on a
private corpus kept as releases of the private repository
`keeferelliott45-alt/LossLift-Corpus`.

Two workflows, both manually dispatched, both run from `main` only:

| Workflow | What it does |
| --- | --- |
| **Corpus gate** (`corpus-gate.yml`) | Runs `python -m tools.corpus_gate run` between two exact commits on one corpus release |
| **Corpus update** (`corpus-update.yml`) | Proposes, then (after approval) publishes, a new corpus release |

Neither ever commits a document, file name, manifest or salt to LossLift.

## One-time browser configuration

1. **Corpus repository.** Create the private repository
   `keeferelliott45-alt/LossLift-Corpus` with a README (a release needs at
   least one commit).
2. **The first corpus release.** Zip the existing corpus as
   `manifest.json` plus `pdfs/` (the manifest `init-manifest` made, whose
   paths are relative to `pdfs/`). In the corpus repository: *Releases → Draft
   a new release*, tag `corpus-v1`, attach the ZIP as the release's only
   `.zip`, *Publish release*. One wrapping folder around both is accepted.
3. **Tokens.** Create two fine-grained personal access tokens (*Settings →
   Developer settings → Fine-grained tokens*), each limited to the one
   repository `LossLift-Corpus`:
   * read: *Contents: Read-only*;
   * write: *Contents: Read and write*.
4. **Environments.** In LossLift: *Settings → Environments*:
   * `corpus-gate`: *Deployment branches and tags → Selected branches* →
     `main` only. Secret `LOSSLIFT_CORPUS_TOKEN` = the read token.
   * `corpus-publish`: `main` only, **Required reviewers** = you. Secret
     `LOSSLIFT_CORPUS_WRITE_TOKEN` = the write token.

   Secrets go in these environments, never in repository secrets: that is what
   keeps a copy of a workflow on another branch from reading them.
5. **Active corpus.** *Settings → Secrets and variables → Actions → Variables*:
   `LOSSLIFT_ACTIVE_CORPUS` = `corpus-v1`.
6. **Actions settings.** *Settings → Actions → General*: keep *Require
   approval for all outside collaborators* on, and workflow permissions at
   *Read repository contents*.

## Running the gate

*Actions → Corpus gate → Run workflow* (branch `main`), with:

* `baseline_sha`, `candidate_sha`: full 40-character SHAs;
* `corpus_tag`: blank for the active corpus, or an exact release tag;
* `pr_number`: optional; when given, the candidate must be that pull
  request's live head, checked before the run and again after it.

The job summary names both SHAs, the corpus release, the release archive's
and the manifest's SHA-256, the number of documents verified, the verdict and
the gate's exit code. Only `report.txt` and `result.json` are uploaded. The
job fails for every gate exit code but 0.

What happens, in order:

1. The definition is confirmed to be `main`'s; `main` is checked out without
   stored credentials; the SHAs and the pull request head are checked.
2. Both commits are fetched with the job token passed for that one command.
3. The sandbox image is built from `main`'s `requirements.txt`, before any
   corpus or corpus token is on the runner.
4. The release is downloaded with the read token, the only step that sees it.
   The token is dropped when the download redirects to another host.
5. The ZIP is validated (traversal, links, special files, encryption, sizes,
   compression ratio, name collisions), extracted to runner temporary storage,
   and verified: every listed document present with its size and SHA-256, no
   extra file, and a manifest naming a release must name this one. The ZIP is
   deleted.
6. Stored git credentials are scrubbed and checked for.
7. The gate runs in an empty environment (`env -i`). Each commit's collector
   runs in its own container: `--network none`, no variable from the host
   (only eight it is given by name), a non-root user, all capabilities dropped,
   `no-new-privileges`, read-only root, process and memory limits, no container
   log, and only its worktree, the collector, the snapshot and the document
   list mounted read-only, plus its own output and scratch directories. The
   gate's hour-per-run timeout, and a cancelled job's SIGTERM, stop and remove
   every container.
8. Both output files are checked by trusted code (printable ASCII, nothing
   shaped like a path, no corpus file name, path or salt) before being staged.
9. Every document, the manifest, worktrees, containers and the image are
   removed, whether the run passed, failed or was cancelled.

A candidate that needs a package `main` does not install cannot fetch it:
it fails with exit 4. Land the requirement on `main` first.

**What the sandbox does not stop.** The candidate's code runs beside the
documents and can write whatever it likes into its own measurements, and a
deliberately malicious candidate could encode text there as numbers. The
output check catches text, paths and names, not a covert encoding. What keeps
that out is who can dispatch the workflow (write access to LossLift) and that
the artifact stays in a private repository.

## Adding loss runs from the browser

The intake folder on the Windows machine
(`C:\Users\keefe\Downloads\Real test loss run reports`) is never read by
anything here. After the one ZIP is made, every step is in the browser.

1. **Zip the new files.** In Explorer, select the files (or the folder) →
   *Send to → Compressed (zipped) folder*. Any layout; non-PDFs may be
   included and are reported, not admitted.
2. **Upload them as an intake release.** In `LossLift-Corpus`: *Releases →
   Draft a new release*, a new tag such as `intake-2026-09-26`, attach the ZIP
   as the only `.zip`, tick *Set as a pre-release*, *Publish release*.
3. **Propose.** In LossLift: *Actions → Corpus update → Run workflow*,
   action `propose`, `intake_tag` = the intake tag (`active_tag` blank = the
   active corpus). The job summary, also uploaded as `proposal.md`, lists
   every intake file by position, class, candidate id, size and SHA-256, and
   gives a **review digest**. File names are never shown: match candidates to
   files by size (Explorer → *Properties* shows the exact byte count).

   | Class | Meaning |
   | --- | --- |
   | `candidate` | a new PDF; may be approved |
   | `already-approved` | byte-identical to a corpus document |
   | `duplicate` | byte-identical to an earlier intake file; never admitted twice |
   | `unsupported` | not `.pdf`; excluded from the PDF gate, named by extension |
   | `not-a-pdf` | named `.pdf`, does not start `%PDF-` |
   | `changed-approved` | same place as a corpus document, different bytes: **blocks** the update |

4. **Approve and publish.** *Run workflow* again: action `publish`, the same
   `active_tag` and `intake_tag`, `new_tag` (the next version, e.g.
   `corpus-v2`), `approve_ids` (comma-separated candidate ids) and the
   `review_digest`. The `corpus-publish` environment then waits for a
   reviewer's approval in the browser. The job recomputes the proposal and
   stops unless the digest matches, so if the active corpus or the intake
   changed since review, nothing is published. It keeps the salt and every
   existing entry byte-for-byte and in order, appends the approved documents
   as `pdfs/added/<id>.pdf` under `doc-<first 12 hex of SHA-256>`, binds the
   manifest to the new tag (`release`, with `previous_release` and
   `previous_manifest_sha256`), re-verifies the archive, and creates the
   release. Existing releases are never replaced.
5. **Activate.** Only now, and only by hand: set `LOSSLIFT_ACTIVE_CORPUS` to
   `corpus-v2`. Until then every gate run keeps using the old release. The
   intake release can be deleted.

Nothing is admitted by being uploaded: an intake file becomes a corpus
document only when its id is typed into `approve_ids` and the publish job is
approved.

### Current intake rules

* **University of Maine.** Approve the original complete PDF. The four Maine
  page-range PDFs are transport copies of the same packet, and must not also
  be admitted while the complete packet is present: leave their ids out.
  They are distinct bytes, so they appear as candidates; the complete packet
  is the largest of the five.
* **Buffalo.** Approve only once its expected ground truth is recorded in
  `benchmark/` (claim count and reconciliation outcome), so the corpus gate
  and the benchmark both have something to hold it to.
* **San Leandro `.xlsx`.** Reported as `unsupported` and excluded. It stays
  outside the PDF gate until spreadsheet ingestion exists.
