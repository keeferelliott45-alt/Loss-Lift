"""Grow the private corpus from the browser: propose, approve, publish.

``.github/workflows/corpus-update.yml`` runs this from ``main``. Nothing in it
executes code from the repository under review, parses a PDF, or needs a
terminal.

**propose** reads the active corpus release (``corpus-vN``: ``manifest.json``
and ``pdfs/``) and an intake release (a ZIP of new loss runs, any layout),
and classifies every intake file:

* ``candidate``          a PDF not yet in the corpus: may be approved
* ``already-approved``   byte-identical to a corpus document: nothing to do
* ``duplicate``          byte-identical to an earlier intake file: never admitted twice
* ``unsupported``        not a ``.pdf``: excluded from the PDF gate, named by extension only
* ``not-a-pdf``          a ``.pdf`` that does not start ``%PDF-``: excluded
* ``changed-approved``   same place as a corpus document, different bytes: blocks the update

It writes a privacy-safe proposal -- ids, SHA-256s, sizes, classes; never a
file name -- and a review digest binding the proposal to both archives.

**build** takes the review digest back, with the ids a person approved and
the new release tag. It recomputes the proposal and stops unless the digest
matches, so what is approved is exactly what was reviewed. The new manifest
keeps the salt, every existing entry exactly as it was and in its place, and
appends the approved documents under hash-derived ids and paths. Existing
documents are copied from the active release, never from the intake, so
they cannot disappear or change bytes.

**publish** creates the new release with the one ZIP. It does not change
which release is active: that is the ``LOSSLIFT_ACTIVE_CORPUS`` repository
variable, set by a person afterwards.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
import sys
import urllib.parse
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from tools.corpus_gate import cloud
from tools.corpus_gate import manifest as manifests
from tools.corpus_gate.cloud import CloudError

VERSIONED = re.compile(r"corpus-v([1-9][0-9]{0,5})")
PDF_MAGIC = b"%PDF-"
EXTENSION = re.compile(r"[a-z0-9]{1,8}")
APPROVABLE = "candidate"
BLOCKING = "changed-approved"


@dataclass
class Item:
    position: int  # 1-based, in archive order
    cls: str
    sha256: str | None = None
    bytes: int | None = None
    id: str | None = None
    extension: str | None = None
    same_as: str | None = None  # the id or intake position it duplicates


@dataclass
class Proposal:
    active: dict[str, Any]
    intake: dict[str, Any]
    items: list[Item] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(item.cls == BLOCKING for item in self.items)

    @property
    def candidates(self) -> dict[str, Item]:
        return {item.id: item for item in self.items if item.cls == APPROVABLE and item.id}

    def public(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "active": self.active,
            "intake": self.intake,
            "items": [
                {key: value for key, value in vars(item).items() if value is not None}
                for item in self.items
            ],
        }

    @property
    def digest(self) -> str:
        text = json.dumps(self.public(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _extension(parts: tuple[str, ...]) -> str:
    suffix = PurePosixPath(parts[-1]).suffix.lower().lstrip(".")
    return suffix if EXTENSION.fullmatch(suffix) else "other"


def _new_id(sha: str, taken: set[str]) -> str:
    for length in (12, 20, 64):
        doc_id = f"doc-{sha[:length]}"
        if doc_id not in taken:
            return doc_id
    raise CloudError("a new document's id collides with an existing one")


def propose(
    corpus: cloud.Corpus,
    active_identity: dict[str, Any],
    intake_zip: Path,
    intake_identity: dict[str, Any],
) -> Proposal:
    manifest = manifests.load(corpus.manifest)
    by_sha = {entry.sha256: entry.id for entry in manifest.entries}
    by_path = {entry.path: entry for entry in manifest.entries}
    taken = {entry.id for entry in manifest.entries}
    proposal = Proposal(
        active={
            "tag": active_identity.get("tag"),
            "archive_sha256": active_identity.get("archive_sha256"),
            "manifest_sha256": corpus.manifest_sha256,
            "documents": corpus.count,
        },
        intake={
            "tag": intake_identity.get("tag"),
            "archive_sha256": intake_identity.get("archive_sha256"),
        },
    )
    seen: dict[str, int] = {}
    position = 0
    with cloud.open_archive(intake_zip) as archive:
        for member in cloud.safe_members(archive):
            if member.is_dir:
                continue
            position += 1
            extension = _extension(member.parts)
            digest = hashlib.sha256()
            head = bytearray()

            def sink(block: bytes) -> None:
                digest.update(block)
                if len(head) < len(PDF_MAGIC):
                    head.extend(block[: len(PDF_MAGIC) - len(head)])

            size = cloud.stream_member(archive, member, sink)
            sha = digest.hexdigest()
            item = Item(position, "", sha256=sha, bytes=size)
            # Where the file sits, with and without the folder the ZIP was made from.
            places = {"/".join(member.parts), "/".join(member.parts[1:])}
            if len(member.parts) > 1 and member.parts[0] == "pdfs":
                places.add("/".join(member.parts[1:]))
            clash = next((by_path[p] for p in places if p in by_path), None)
            if extension != "pdf":
                item.cls, item.extension, item.sha256, item.bytes = "unsupported", extension, None, None
            elif clash is not None and clash.sha256 != sha:
                item.cls, item.same_as = BLOCKING, clash.id
            elif sha in by_sha:
                item.cls, item.id = "already-approved", by_sha[sha]
            elif not bytes(head).startswith(PDF_MAGIC):
                item.cls = "not-a-pdf"
            elif sha in seen:
                item.cls, item.same_as = "duplicate", f"intake #{seen[sha]}"
            else:
                item.cls = APPROVABLE
                item.id = _new_id(sha, taken)
                taken.add(item.id)
                seen[sha] = position
            proposal.items.append(item)
    if position == 0:
        raise CloudError("the intake archive holds no files")
    proposal.intake["files"] = position
    return proposal


def render(proposal: Proposal) -> str:
    counts: dict[str, int] = {}
    for item in proposal.items:
        counts[item.cls] = counts.get(item.cls, 0) + 1
    lines = [
        "## LossLift corpus update proposal",
        "",
        f"Active corpus: `{proposal.active['tag']}` ({proposal.active['documents']} documents, "
        f"manifest `{proposal.active['manifest_sha256']}`)",
        f"Intake: `{proposal.intake['tag']}` ({proposal.intake['files']} files, "
        f"archive `{proposal.intake['archive_sha256']}`)",
        "",
        f"**Review digest:** `{proposal.digest}`",
        "",
        "Counts: " + ", ".join(f"{name} {count}" for name, count in sorted(counts.items())),
        "",
    ]
    if proposal.blocked:
        lines += [
            "**BLOCKED.** An intake file sits where an approved document sits, with different "
            "bytes. Approved documents never change: rename the new file, or leave it out, and "
            "upload a new intake.",
            "",
        ]
    lines += [
        "| Intake # | Class | Id | Bytes | SHA-256 | Note |",
        "|---:|---|---|---:|---|---|",
    ]
    for item in proposal.items:
        note = ""
        if item.cls == "unsupported":
            note = f".{item.extension} is outside the PDF gate"
        elif item.same_as:
            note = f"same as {item.same_as}" if item.cls == "duplicate" else f"clashes with {item.same_as}"
        lines.append(
            f"| {item.position} | {item.cls} | {item.id or ''} | "
            f"{'' if item.bytes is None else item.bytes} | {item.sha256 or ''} | {note} |"
        )
    lines += [
        "",
        "Nothing has been admitted. To admit documents, dispatch the workflow again with "
        "action `publish`, this review digest, the new release tag, and the ids you approve.",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Approval
# --------------------------------------------------------------------------


def check_new_tag(new_tag: str, active_tag: str) -> str:
    match = VERSIONED.fullmatch(new_tag or "")
    if not match:
        raise CloudError("the new release tag must look like corpus-v2")
    if new_tag == active_tag:
        raise CloudError("the new release tag must differ from the active one")
    active = VERSIONED.fullmatch(active_tag or "")
    if active and int(match.group(1)) <= int(active.group(1)):
        raise CloudError("the new release must have a higher version than the active one")
    return new_tag


def parse_approvals(text: str) -> list[str]:
    ids = [word for word in re.split(r"[\s,]+", text or "") if word]
    if not ids:
        raise CloudError("no document was approved: name at least one candidate id")
    if len(set(ids)) != len(ids):
        raise CloudError("an approved id is listed twice")
    for doc_id in ids:
        if not manifests.DOCUMENT_ID.fullmatch(doc_id):
            raise CloudError("an approved id is not a document id")
    return ids


def check_superset(old: dict[str, Any], new: dict[str, Any]) -> None:
    """The new manifest is the old one with documents appended, and nothing else."""
    if new.get("digest_salt") != old.get("digest_salt"):
        raise CloudError("the new manifest does not keep the corpus salt")
    if new.get("version") != old.get("version"):
        raise CloudError("the new manifest changes the manifest version")
    before, after = old.get("documents") or [], new.get("documents") or []
    if len(after) <= len(before):
        raise CloudError("the new manifest adds no document")
    for position, entry in enumerate(before):
        if after[position] != entry:
            raise CloudError("the new manifest changes, reorders or removes an existing document")
    shas = [item.get("sha256") for item in before]
    ids = {item.get("id") for item in before}
    paths = {item.get("path") for item in before}
    for item in after[len(before):]:
        if item.get("sha256") in shas:
            raise CloudError("the new manifest admits a document twice")
        if item.get("id") in ids or item.get("path") in paths:
            raise CloudError("the new manifest reuses an existing id or path")
        shas.append(item.get("sha256"))
        ids.add(item.get("id"))
        paths.add(item.get("path"))


def build(
    *,
    corpus: cloud.Corpus,
    proposal: Proposal,
    intake_zip: Path,
    review_digest: str,
    approved: list[str],
    new_tag: str,
    work: Path,
) -> tuple[Path, dict[str, Any]]:
    """Assemble the new corpus archive. Returns it and the new manifest."""
    if not re.fullmatch(r"[0-9a-f]{64}", review_digest or ""):
        raise CloudError("the review digest must be the 64-character digest from the proposal")
    if proposal.digest != review_digest:
        raise CloudError(
            "the review digest does not match: the active corpus, the intake or the proposal "
            "changed since it was reviewed. Propose again"
        )
    if proposal.blocked:
        raise CloudError("the proposal is blocked: an approved document would change")
    candidates = proposal.candidates
    unknown = [doc_id for doc_id in approved if doc_id not in candidates]
    if unknown:
        raise CloudError(
            f"{len(unknown)} approved id(s) are not candidates in this proposal: "
            + ", ".join(unknown)
        )
    check_new_tag(new_tag, str(proposal.active.get("tag") or ""))

    old = corpus.payload
    new = copy.deepcopy(old)
    new["release"] = new_tag
    new["previous_release"] = proposal.active.get("tag")
    new["previous_manifest_sha256"] = corpus.manifest_sha256
    wanted = {candidates[doc_id].position: candidates[doc_id] for doc_id in approved}
    added = []
    for item in sorted(wanted.values(), key=lambda it: it.position):
        added.append({"id": item.id, "path": f"added/{item.id}.pdf", "sha256": item.sha256,
                      "bytes": item.bytes})
    new["documents"] = list(old["documents"]) + added
    check_superset(old, new)

    root = work / "corpus"
    root.mkdir(parents=True)
    shutil.copytree(corpus.documents, root / "pdfs")
    with cloud.open_archive(intake_zip) as archive:
        position = 0
        for member in cloud.safe_members(archive):
            if member.is_dir:
                continue
            position += 1
            item = wanted.get(position)
            if item is None:
                continue
            target = cloud.extract_member(archive, member, root / "pdfs", ("added", f"{item.id}.pdf"))
            if manifests.file_sha256(target) != item.sha256:
                raise CloudError("the intake archive changed while it was being read")
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(new, indent=2) + "\n", encoding="utf-8")
    try:
        loaded = manifests.load(manifest_path)
    except manifests.SetupError as error:
        raise CloudError(f"the new manifest is unusable: {error}") from None
    cloud.verify_exact(loaded, root / "pdfs")

    archive_path = work / f"{new_tag}.zip"
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as out:
        out.write(manifest_path, "manifest.json")
        for entry in loaded.entries:
            out.write(manifests.locate(entry, root / "pdfs"), f"pdfs/{entry.path}")
    cloud.remove_tree(root)
    return archive_path, new


def publish(repository: str, tag: str, archive: Path, token: str, summary: str) -> dict[str, Any]:
    """Create release ``tag`` with ``archive`` as its one asset. Never overwrites."""
    base = cloud._api_base()
    quoted = urllib.parse.quote(tag, safe="")
    try:
        cloud.api_json(f"{base}/repos/{repository}/releases/tags/{quoted}", token)
    except cloud._ApiError as error:
        if error.status != 404:
            raise CloudError(f"could not check whether release {tag} exists (HTTP {error.status})") from None
    else:
        raise CloudError(f"release {tag} already exists; corpus releases are never replaced")
    body = json.dumps({
        "tag_name": tag,
        "name": tag,
        "body": summary,
        "draft": False,
        "prerelease": False,
        "make_latest": "false",
    }).encode("utf-8")
    try:
        release = cloud.api_json(f"{base}/repos/{repository}/releases", token, method="POST",
                                 data=body, content_type="application/json")
    except cloud._ApiError as error:
        raise CloudError(f"release {tag} could not be created (HTTP {error.status})") from None
    upload = str(release.get("upload_url", "")).split("{", 1)[0]
    if not upload.startswith("https://"):
        raise CloudError("the new release has no upload address")
    try:
        asset = cloud.api_json(f"{upload}?name=corpus.zip", token, method="POST",
                               data=archive.read_bytes(), content_type="application/zip")
    except cloud._ApiError as error:
        raise CloudError(
            f"release {tag} was created but its archive could not be uploaded (HTTP {error.status}); "
            "delete the release in the browser and publish again"
        ) from None
    return {"release_id": release.get("id"), "asset_id": asset.get("id"), "tag": tag}


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.corpus_gate.intake")
    parser.add_argument("action", choices=("propose", "publish"))
    parser.add_argument("--repository", default=cloud.CORPUS_REPOSITORY)
    parser.add_argument("--active-tag", required=True)
    parser.add_argument("--intake-tag", required=True)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--token-variable", default="LOSSLIFT_CORPUS_TOKEN")
    parser.add_argument("--new-tag", default="")
    parser.add_argument("--approve", default="")
    parser.add_argument("--review-digest", default="")
    return parser


def run(args: argparse.Namespace) -> int:
    cloud.check_tag(args.active_tag, "active corpus tag")
    cloud.check_tag(args.intake_tag, "intake tag")
    if args.action == "publish":
        # Everything a person typed is checked before anything is downloaded.
        check_new_tag(args.new_tag, args.active_tag)
        approved = parse_approvals(args.approve)
        if not re.fullmatch(r"[0-9a-f]{64}", args.review_digest or ""):
            raise CloudError("the review digest must be the 64-character digest from the proposal")
    token = cloud._token(args.token_variable)
    work = args.work
    work.mkdir(parents=True, exist_ok=False)
    active_zip, intake_zip = work / "active.zip", work / "intake.zip"
    active_identity = cloud.download(args.repository, args.active_tag, token, active_zip)
    intake_identity = cloud.download(args.repository, args.intake_tag, token, intake_zip)
    corpus = cloud.unpack_corpus(active_zip, work / "active", args.active_tag)
    active_zip.unlink()
    proposal = propose(corpus, active_identity, intake_zip, intake_identity)
    report = render(proposal)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")
    if args.action == "propose":
        print(f"review_digest={proposal.digest}")
        print(f"candidates={len(proposal.candidates)}")
        return cloud.EXIT_SETUP if proposal.blocked else cloud.EXIT_PASS
    archive, new = build(
        corpus=corpus, proposal=proposal, intake_zip=intake_zip,
        review_digest=args.review_digest, approved=approved, new_tag=args.new_tag,
        work=work / "build",
    )
    published = publish(
        args.repository, args.new_tag, archive, token,
        f"LossLift corpus {args.new_tag}: {len(new['documents'])} documents "
        f"({len(approved)} added to {args.active_tag}). Review digest {proposal.digest}. "
        "Not active until LOSSLIFT_ACTIVE_CORPUS names it.",
    )
    with args.report.open("a", encoding="utf-8") as handle:
        handle.write(
            f"\nPublished `{args.new_tag}` with {len(new['documents'])} documents "
            f"({len(approved)} added). It is **not active** until the repository variable "
            f"LOSSLIFT_ACTIVE_CORPUS is set to `{args.new_tag}`.\n"
        )
    print(f"published={published['tag']}")
    return cloud.EXIT_PASS


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return run(args)
    except CloudError as error:
        print(f"corpus update: {error}", file=sys.stderr)
        return cloud.EXIT_SETUP
    except manifests.SetupError as error:
        print(f"corpus update: {error}", file=sys.stderr)
        return cloud.EXIT_SETUP


if __name__ == "__main__":
    sys.exit(main())
