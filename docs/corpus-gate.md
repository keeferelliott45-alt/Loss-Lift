# Real-corpus A/B gate

Synthetic fixtures prove the engine still does what it did yesterday on
documents it was built for. The real corpus is where a change shows what it
does to documents nobody designed for it. This gate runs every locally held
real loss run through two commits of LossLift, compares what each one made of
every document, and fails unless the two agree, or unless every difference is
named in a reviewed allowlist.

It replaces the hand-run corpus comparison used during reviews. It runs the
same way every time, it checks its own inputs, and it produces nothing that
cannot be pasted into a pull request.

## The command

Run from any checkout of this repository. The commits under comparison are
checked out into temporary worktrees of their own, so the current working
tree is neither read nor changed, and it does not matter which branch it is
on.

```
git fetch origin
python -m tools.corpus_gate run \
    --baseline  origin/main \
    --candidate <commit under review> \
    --corpus    <corpus directory> \
    --manifest  <manifest file>
```

**For Claude**, in a cloud session where the corpus sits in the session
scratchpad:

```
python -m tools.corpus_gate run \
    --baseline  <parent commit> \
    --candidate HEAD \
    --corpus    "$CORPUS_DIR" \
    --manifest  "$CORPUS_MANIFEST"
```

**For the overnight coordinator**, once per pull request head, after
fetching:

```
python -m tools.corpus_gate run --baseline origin/main --candidate <PR head sha> ^
    --corpus D:\losslift-corpus\pdfs --manifest D:\losslift-corpus\manifest.json
```

Pass the PR head as a full commit sha rather than a branch name, so the
result is about exactly what was reviewed.

The exit code decides. **0 is the only pass.** Any other code blocks the
change and its report says why. The report goes to stdout and nothing else
does, so stdout can be posted as it stands. Where the result files went is
printed to stderr, because that is a local path.

| Exit | Meaning | What to do |
| --- | --- | --- |
| 0 | Every document behaved identically, or every difference was approved | Proceed |
| 1 | Behavior changed and the allowlist does not approve it, or an allowlist entry matched nothing | Review each change; approve only what was intended |
| 2 | The command line was wrong | Fix the arguments |
| 3 | The corpus, manifest, allowlist or a revision cannot be trusted; **nothing was run** | Restore the missing or changed document, or fix the input |
| 4 | A revision could not start, crashed, timed out, or left documents unmeasured | Treat as a failure of the candidate until shown otherwise |

A pre-existing crash in the baseline still fails with exit 4. A gate that
measured part of the corpus has not passed.

## One-time setup

Keep the real documents **outside the repository**, anywhere on local disk.
The gate refuses a corpus directory or a manifest inside the working tree.

Record the corpus once:

```
python -m tools.corpus_gate init-manifest --corpus <corpus directory> --manifest <manifest file>
```

This lists every PDF under the directory, with its size and SHA-256, under an
id derived from its hash (`doc-3f9a1c2b7e44`), and generates a random secret
salt. **The manifest must stay out of Git**, because it holds the salt. It
will not overwrite an existing manifest unless given `--force`.

The manifest establishes the expected set:

* a listed document that is missing stops the gate (exit 3);
* a listed document whose bytes changed stops the gate (exit 3);
* a PDF in the directory that the manifest does not list is counted in the
  report and not run. To include it, regenerate the manifest; a manifest
  that grows is a change a reviewer sees.

## What is compared

For every document, both commits' results are compared field by field:

| Group | Compared as |
| --- | --- |
| `claim_count` | integer (**critical**) |
| `status` | `CLEAN` / `NEEDS_REVIEW` (**critical**) |
| `pages` | processed, failed, skipped, unresolved and scanned page lists; page count; column-split pages; rows seen per page; a digest of each unresolved page's reason |
| `unplaced` | count of rows whose money could not be placed, per page and in total; digest of their content |
| `printed` | printed claim count; a digest per printed total column; unreadable totals, count evidence and printed sections, as counts and digests |
| `findings` | total; counts by rule, by rule and severity, by rule and category, by rule and scope; per rule, a digest of *which* things were flagged and a digest of *what was said* |
| `summary` | per-term claim counts, open and closed counts, printed claim counts and whether each term ties, plus a digest of the whole |
| `claims` | a digest of every extracted claim; a digest per field, so a report can say `claims.fields.incurred_total` changed; null counts per field |
| `metadata` | a digest per document-level field (carrier, insured, policy, period, valuation date, locale, mapping, profile) |
| `warnings` | count and digest |
| per document | whether the pipeline raised, and the exception's type name |

A field one commit reports and the other does not is a change. Timing is
recorded and never compared.

## Privacy

Real loss runs carry claimant names, injury descriptions and claim numbers
(spec section 9). The gate is built so that none of that can reach its
output, even by accident:

* **The documents never move.** They stay where they are. The pipeline's own
  temporary copies land in a directory the gate creates per run and deletes
  afterwards, whatever happens.
* **Nothing textual is written.** Every value in the output is an integer, a
  boolean, null, a page number, a string matching the strict shape of an
  enumeration (`R-22`, `NEEDS_REVIEW`, `financial`), checked before it is
  written, or a keyed digest. Amounts, dates, names, claim numbers, finding
  messages, warnings and the pipeline's own reasons are only ever digested,
  because a message can quote the document it is about.
* **Digests are keyed.** Every digest is HMAC-SHA256 under the salt in the
  local manifest. A digest shows *that* a value changed between two commits,
  never *what* it was. Even a short value such as one printed total cannot
  be recovered by guessing without the manifest.
* **Documents are named by id.** Reports and results name documents by
  manifest id and SHA-256, never by filename or path. Default ids are
  hash-derived. If you write your own ids, keep personal information out of
  them.
* **Errors are named by type.** A crash is recorded as `ValueError`, never
  with its message, because a message can quote the cell that caused it. The
  collectors' stdout and stderr are discarded, never shown and never stored,
  for the same reason.
* **Results stay local by default.** Without `--out`, results are written
  beside the manifest, in `gate-runs/`, outside the repository.

The test suite checks this with sentinel personal data: a fake pipeline
returns a claimant, a claim number, a description and amounts, the corpus
file names carry them too, and every byte the command prints or writes is
searched for them.

## Approving a change: the allowlist

Nothing is approved automatically. An intended behavior change is approved
by an explicit allowlist file passed with `--allowlist`. With no file, the
allowlist is empty.

```json
{
  "version": 1,
  "entries": [
    {
      "document_sha256": "3f9a1c2b7e44...all 64 hex characters...",
      "field": "findings.by_rule.R-22",
      "baseline": 9,
      "candidate": 10,
      "reason": "Page 8 now reports the recognised fragment; reviewed in PR #N.",
      "candidate_commit": "optional: the full commit this approval is for"
    }
  ]
}
```

Rules, all enforced:

* **One entry approves one change.** The match is on the document's full
  SHA-256, the exact field path, and the exact baseline *and* candidate
  values as they appear in `result.json`. There are no wildcards, patterns,
  rule-wide approvals, or tolerances.
* **Every entry must say why**, in a sentence.
* **An entry that matches nothing fails the gate** (exit 1). An allowlist
  describes the changes a reviewer expected. A stale one describes nothing
  and must be removed.
* **Execution failures cannot be allowlisted.**
* `baseline_commit` and `candidate_commit` are optional. When set, the entry
  applies only to that exact pair.

To write an entry, copy the values from the failing run's `result.json`
(`documents.<id>.changes`). Digested values are copied as the digest. A
digest is stable for a given manifest, so the same change produces the same
digest on every run.

## Outputs

* `result.json`: machine-readable. Verdict and exit code, both commits, the
  manifest's own hash, per-document state (`unchanged` / `changed` /
  `failed`), every change with both values, and both commits' full
  measurements.
* `report.txt`: the same comparison in a few lines per document, critical
  changes first. It is also what the command prints.

## Relationship to `benchmark/`

`benchmark/` measures one checkout against adjudicated ground truth: how many
claims a document really has, and whether a clean badge was earned. This gate
measures one commit against another: whether anything moved at all. A change
can pass the benchmark and fail the gate, and that is the point. An
improvement still has to be seen, and approved, before it lands.
