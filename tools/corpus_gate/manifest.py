"""The local manifest: which real documents the gate expects, byte for byte.

The manifest lives beside the corpus, outside the repository, and never in
Git. It holds, per document, an opaque id, the file's path relative to the
corpus directory, its size and its SHA-256 -- and, once for the corpus, the
secret salt every digest the gate writes is keyed with. The salt is why a
digest of one short value, a single printed total, cannot be reversed by
guessing: whoever holds the results but not the manifest holds nothing they
can check a guess against.

A manifest establishes the expected set. A document it lists that is missing
from the corpus, or whose bytes have changed, stops the gate before anything
runs; a corpus that quietly shrinks is how a comparison starts lying.

Verifying the corpus proves what the files held when they were hashed, not
what a revision reads later. So the revisions never read the corpus at all:
each run copies every listed document into a private snapshot, hashing the
bytes as they are copied, and both revisions read that snapshot and nothing
else.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

VERSION = 1
DOCUMENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
SHA256 = re.compile(r"[0-9a-f]{64}")


class SetupError(Exception):
    """The corpus, manifest, allowlist or revisions cannot be used as given."""


@dataclass(frozen=True)
class Entry:
    id: str
    path: str  # POSIX, relative to the corpus directory
    sha256: str
    bytes: int


@dataclass(frozen=True)
class Manifest:
    salt: bytes
    entries: tuple[Entry, ...]
    sha256: str  # of the manifest file itself, to say which one was used


@dataclass(frozen=True)
class Verification:
    missing: tuple[str, ...]
    mismatched: tuple[str, ...]
    unlisted: int

    @property
    def ok(self) -> bool:
        return not self.missing and not self.mismatched


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def ensure_outside(path: Path, repo_root: Path | None, what: str) -> None:
    """Refuse a corpus or manifest inside the repository's working tree.

    Real documents and the salt stay out of Git by staying out of the tree
    Git looks at. ``*.pdf`` is ignored there, but a manifest is not, and an
    ignore rule is one edit away from a commit.
    """
    if repo_root is None:
        return
    if path.resolve().is_relative_to(repo_root.resolve()):
        raise SetupError(
            f"the {what} must live outside the repository, not inside its working tree"
        )


def _pdfs(corpus: Path) -> list[Path]:
    return sorted(
        path
        for path in corpus.rglob("*")
        if path.is_file() and path.suffix.lower() == ".pdf"
    )


def _relative(path: Path, corpus: Path) -> str:
    return PurePosixPath(path.relative_to(corpus).as_posix()).as_posix()


def create(corpus: Path, manifest: Path, *, force: bool = False) -> Manifest:
    """Write a manifest for every PDF under ``corpus``.

    Ids are derived from the file's hash, so the default report names no file.
    A document present twice under two names keeps both entries, told apart
    by a suffix.
    """
    if not corpus.is_dir():
        raise SetupError("the corpus directory does not exist")
    if manifest.exists() and not force:
        raise SetupError("the manifest already exists; pass --force to replace it")
    files = _pdfs(corpus)
    if not files:
        raise SetupError("the corpus directory holds no PDF files")

    entries: list[dict[str, object]] = []
    used: set[str] = set()
    for path in files:
        digest = file_sha256(path)
        base = f"doc-{digest[:12]}"
        doc_id, suffix = base, 2
        while doc_id in used:
            doc_id, suffix = f"{base}-{suffix}", suffix + 1
        used.add(doc_id)
        entries.append(
            {
                "id": doc_id,
                "path": _relative(path, corpus),
                "sha256": digest,
                "bytes": path.stat().st_size,
            }
        )
    payload = {
        "version": VERSION,
        "digest_salt": secrets.token_hex(32),
        "documents": entries,
    }
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return load(manifest)


def load(manifest: Path) -> Manifest:
    """Read and validate a manifest. Every defect is a stop, never a skip."""
    if not manifest.is_file():
        raise SetupError("the manifest file does not exist")
    raw = manifest.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SetupError("the manifest is not valid JSON") from error
    if not isinstance(payload, dict) or payload.get("version") != VERSION:
        raise SetupError(f"the manifest must be a version {VERSION} object")

    salt_hex = payload.get("digest_salt")
    if not isinstance(salt_hex, str) or not re.fullmatch(r"[0-9a-f]{64}", salt_hex):
        raise SetupError("the manifest's digest_salt must be 64 lowercase hex characters")

    documents = payload.get("documents")
    if not isinstance(documents, list) or not documents:
        raise SetupError("the manifest lists no documents")

    entries: list[Entry] = []
    ids: set[str] = set()
    paths: set[str] = set()
    for position, item in enumerate(documents, start=1):
        if not isinstance(item, dict):
            raise SetupError(f"manifest entry {position} is not an object")
        doc_id, rel, digest, size = (
            item.get("id"),
            item.get("path"),
            item.get("sha256"),
            item.get("bytes"),
        )
        if not isinstance(doc_id, str) or not DOCUMENT_ID.fullmatch(doc_id):
            raise SetupError(f"manifest entry {position} has an unusable id")
        if doc_id in ids:
            raise SetupError(f"manifest id {doc_id} appears twice")
        if not isinstance(rel, str) or not rel:
            raise SetupError(f"manifest entry {doc_id} has no path")
        posix = PurePosixPath(rel)
        if posix.is_absolute() or ".." in posix.parts or ":" in rel or "\\" in rel:
            raise SetupError(f"manifest entry {doc_id} must use a plain relative path")
        if rel in paths:
            raise SetupError(f"manifest entry {doc_id} repeats another entry's path")
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise SetupError(f"manifest entry {doc_id} has an unusable sha256")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise SetupError(f"manifest entry {doc_id} has an unusable size")
        ids.add(doc_id)
        paths.add(rel)
        entries.append(Entry(doc_id, rel, digest, size))

    return Manifest(
        salt=bytes.fromhex(salt_hex),
        entries=tuple(entries),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def locate(entry: Entry, corpus: Path) -> Path:
    return corpus.joinpath(*PurePosixPath(entry.path).parts)


def verify(manifest: Manifest, corpus: Path) -> Verification:
    """Check every listed document is present and unchanged, by hash.

    Files under the corpus the manifest does not list are counted, not named,
    and not run: adding a document means regenerating the manifest, which is
    a change a reviewer sees.
    """
    if not corpus.is_dir():
        raise SetupError("the corpus directory does not exist")
    missing: list[str] = []
    mismatched: list[str] = []
    for entry in manifest.entries:
        path = locate(entry, corpus)
        if not path.is_file():
            missing.append(entry.id)
        elif path.stat().st_size != entry.bytes or file_sha256(path) != entry.sha256:
            mismatched.append(entry.id)
    listed = {entry.path for entry in manifest.entries}
    unlisted = sum(1 for path in _pdfs(corpus) if _relative(path, corpus) not in listed)
    return Verification(tuple(missing), tuple(mismatched), unlisted)


def snapshot(manifest: Manifest, corpus: Path, target: Path) -> dict[str, Path]:
    """Copy every listed document into ``target``; return each copy by id.

    Each copy's hash is taken from the bytes as they are written, so what the
    revisions read is exactly what was checked against the manifest. A
    document that no longer matches its entry -- replaced, edited or removed
    since it was verified -- stops the gate here, before anything runs.

    Copies are named by their position in the manifest (``000001.pdf``),
    never by id or file name, and made read-only. An id is a valid identity
    and a poor file name: two ids differing only in case are one file on a
    case-insensitive filesystem, and ``CON`` cannot be a file on Windows at
    all. A position is unique, portable, and says nothing about the document.
    """
    target.mkdir(parents=True)
    copies: dict[str, Path] = {}
    changed: list[str] = []
    for position, entry in enumerate(manifest.entries, start=1):
        try:
            source = locate(entry, corpus).open("rb")
        except OSError:
            changed.append(entry.id)
            continue
        copy = target / f"{position:06d}.pdf"
        digest = hashlib.sha256()
        size = 0
        with source, copy.open("xb") as sink:
            for block in iter(lambda: source.read(1 << 20), b""):
                digest.update(block)
                size += len(block)
                sink.write(block)
        os.chmod(copy, stat.S_IREAD)
        if size != entry.bytes or digest.hexdigest() != entry.sha256:
            changed.append(entry.id)
        else:
            copies[entry.id] = copy
    if changed:
        raise SetupError(
            f"the corpus changed after it was verified: {len(changed)} document(s) no longer "
            f"match the manifest ({', '.join(changed)})"
        )
    return copies


def changed_copies(manifest: Manifest, copies: dict[str, Path]) -> tuple[str, ...]:
    """Ids whose snapshot copy no longer holds the verified bytes.

    Both revisions read the same copies, so a revision that wrote to one could
    change what the other read. The copies are checked again once both have
    finished: a run whose inputs moved under it has compared nothing.
    """
    changed: list[str] = []
    for entry in manifest.entries:
        copy = copies.get(entry.id)
        try:
            intact = (
                copy is not None
                and copy.stat().st_size == entry.bytes
                and file_sha256(copy) == entry.sha256
            )
        except OSError:
            intact = False
        if not intact:
            changed.append(entry.id)
    return tuple(changed)
