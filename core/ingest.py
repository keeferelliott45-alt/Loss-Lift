"""Stage 0 — ingest (spec section 5).

Hash the file, look for a prior extraction of the same bytes, and hold the
bytes in temporary storage.

Retention: nothing is written outside a temporary directory, and
:func:`discard` removes it.  The dedupe cache is in-process and dies with the
session — persistent storage of claim data is out of scope (spec section 13).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

MAX_UPLOAD_BYTES = 64 * 1024 * 1024
PDF_MAGIC = b"%PDF-"


class IngestError(ValueError):
    """The upload is not a PDF this app can work with."""


@dataclass(frozen=True)
class FileIdentity:
    """The on-disk identity observed while an existing file was opened."""

    device: int
    inode: int
    size: int
    modified_ns: int


def _identity(stat: os.stat_result) -> FileIdentity:
    return FileIdentity(
        device=stat.st_dev,
        inode=stat.st_ino,
        size=stat.st_size,
        modified_ns=stat.st_mtime_ns,
    )


@dataclass
class IngestedFile:
    """One uploaded document, on disk in a temporary directory."""

    document_id: str
    source_filename: str
    sha256: str
    path: Path
    size_bytes: int
    owns_directory: bool = False
    source_path: Path | None = None
    source_identity: FileIdentity | None = None
    ingested_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def exists(self) -> bool:
        return self.path.exists()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_handle(handle: Any) -> str:
    digest = hashlib.sha256()
    handle.seek(0)
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _copy_and_hash(source: Any, target: Any) -> str:
    digest = hashlib.sha256()
    source.seek(0)
    for chunk in iter(lambda: source.read(1024 * 1024), b""):
        target.write(chunk)
        digest.update(chunk)
    return digest.hexdigest()


class ExtractionCache:
    """Session-scoped memory of documents already extracted, keyed by hash."""

    def __init__(self) -> None:
        self._entries: dict[str, Any] = {}

    def get(self, sha256: str) -> Any | None:
        return self._entries.get(sha256)

    def put(self, sha256: str, value: Any) -> None:
        self._entries[sha256] = value

    def __contains__(self, sha256: object) -> bool:
        return sha256 in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        self._entries.clear()


#: The default cache.  Streamlit keeps one of these per session.
CACHE = ExtractionCache()


def ingest(
    data: bytes,
    filename: str,
    workdir: str | Path | None = None,
) -> IngestedFile:
    """Validate, hash and stage an uploaded PDF."""
    if not data:
        raise IngestError(f"{filename} is empty. Upload the PDF again.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise IngestError(
            f"{filename} is {len(data) / 1e6:.0f} MB. The limit is "
            f"{MAX_UPLOAD_BYTES / 1e6:.0f} MB — split the document and retry."
        )
    if not data.startswith(PDF_MAGIC):
        raise IngestError(
            f"{filename} is not a PDF. Loss runs must be uploaded as PDF files."
        )

    owns_directory = not bool(workdir)
    directory = (
        Path(tempfile.mkdtemp(prefix="losslift-"))
        if owns_directory
        else Path(workdir)
    )
    directory.mkdir(parents=True, exist_ok=True)

    digest = sha256_bytes(data)
    safe_name = Path(filename).name or "upload.pdf"
    target = directory / f".upload-{uuid4().hex}.pdf"
    created = False
    try:
        with target.open("xb") as handle:
            created = True
            handle.write(data)
    except BaseException:
        if created:
            target.unlink(missing_ok=True)
        if owns_directory:
            shutil.rmtree(directory, ignore_errors=True)
        raise

    return IngestedFile(
        document_id=str(uuid4()),
        source_filename=safe_name,
        sha256=digest,
        path=target,
        size_bytes=len(data),
        owns_directory=owns_directory,
    )


def ingest_path(path: str | Path, workdir: str | Path | None = None) -> IngestedFile:
    """Snapshot an existing PDF without trusting a changing pathname."""
    source = Path(path).resolve()
    directory: Path | None = None
    temporary_target: Path | None = None
    temporary_target_created = False
    owns_directory = False
    try:
        with source.open("rb") as source_handle:
            identity = _identity(os.fstat(source_handle.fileno()))
            if identity.size == 0:
                raise IngestError(f"{source.name} is empty. Upload the PDF again.")
            if identity.size > MAX_UPLOAD_BYTES:
                raise IngestError(
                    f"{source.name} is {identity.size / 1e6:.0f} MB. The limit is "
                    f"{MAX_UPLOAD_BYTES / 1e6:.0f} MB — split the document and retry."
                )
            if source_handle.read(len(PDF_MAGIC)) != PDF_MAGIC:
                raise IngestError(
                    f"{source.name} is not a PDF. Loss runs must be uploaded as PDF files."
                )

            owns_directory = not bool(workdir)
            directory = (
                Path(tempfile.mkdtemp(prefix="losslift-"))
                if owns_directory
                else Path(workdir)
            )
            directory.mkdir(parents=True, exist_ok=True)
            temporary_target = directory / f".snapshot-{uuid4().hex}.pdf"
            with temporary_target.open("xb") as target_handle:
                temporary_target_created = True
                digest = _copy_and_hash(source_handle, target_handle)

            after_copy = _identity(os.fstat(source_handle.fileno()))
            try:
                path_after_copy = _identity(source.stat())
            except OSError as error:
                raise IngestError(
                    f"{source.name} changed or disappeared while it was being read. "
                    "Run the extraction again."
                ) from error
            if after_copy != identity or path_after_copy != identity:
                raise IngestError(
                    f"{source.name} changed while it was being read. "
                    "Run the extraction again."
                )

        target = temporary_target
        temporary_target = None
        return IngestedFile(
            document_id=str(uuid4()),
            source_filename=source.name,
            sha256=digest,
            path=target,
            size_bytes=identity.size,
            owns_directory=owns_directory,
            source_path=source,
            source_identity=identity,
        )
    except BaseException:
        if temporary_target is not None and temporary_target_created:
            temporary_target.unlink(missing_ok=True)
        if owns_directory and directory is not None:
            shutil.rmtree(directory, ignore_errors=True)
        raise


def verify_source_unchanged(ingested: IngestedFile) -> None:
    """Reject a path input whose identity or bytes changed after snapshotting."""
    source = ingested.source_path
    expected = ingested.source_identity
    if source is None or expected is None:
        return
    try:
        with source.open("rb") as handle:
            before = _identity(os.fstat(handle.fileno()))
            digest = _hash_handle(handle)
            after = _identity(os.fstat(handle.fileno()))
        path_after = _identity(source.stat())
    except OSError as error:
        raise IngestError(
            f"{source.name} changed or disappeared while it was being read. "
            "Run the extraction again."
        ) from error
    if before != expected or after != expected or path_after != expected:
        raise IngestError(
            f"{source.name} changed while it was being read. Run the extraction again."
        )
    if digest != ingested.sha256:
        raise IngestError(
            f"{source.name} changed while it was being read. Run the extraction again."
        )


def discard(ingested: IngestedFile, remove_directory: bool = True) -> None:
    """Delete the staged bytes.  Called after export (spec section 9)."""
    try:
        if ingested.path.exists():
            ingested.path.unlink()
        parent = ingested.path.parent
        if remove_directory and ingested.owns_directory:
            shutil.rmtree(parent, ignore_errors=True)
    except OSError:  # pragma: no cover - best effort cleanup
        pass
