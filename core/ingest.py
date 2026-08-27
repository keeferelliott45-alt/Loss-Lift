"""Stage 0 — ingest (spec section 5).

Hash the file, look for a prior extraction of the same bytes, and hold the
bytes in temporary storage.

Retention: nothing is written outside a temporary directory, and
:func:`discard` removes it.  The dedupe cache is in-process and dies with the
session — persistent storage of claim data is out of scope (spec section 13).
"""

from __future__ import annotations

import hashlib
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


@dataclass
class IngestedFile:
    """One uploaded document, on disk in a temporary directory."""

    document_id: str
    source_filename: str
    sha256: str
    path: Path
    size_bytes: int
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

    directory = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="losslift-"))
    directory.mkdir(parents=True, exist_ok=True)

    digest = sha256_bytes(data)
    safe_name = Path(filename).name or "upload.pdf"
    target = directory / f"{digest[:12]}-{safe_name}"
    target.write_bytes(data)

    return IngestedFile(
        document_id=str(uuid4()),
        source_filename=safe_name,
        sha256=digest,
        path=target,
        size_bytes=len(data),
    )


def ingest_path(path: str | Path, workdir: str | Path | None = None) -> IngestedFile:
    """Ingest a file already on disk (used by the golden-file harness)."""
    source = Path(path)
    return ingest(source.read_bytes(), source.name, workdir)


def discard(ingested: IngestedFile, remove_directory: bool = True) -> None:
    """Delete the staged bytes.  Called after export (spec section 9)."""
    try:
        if ingested.path.exists():
            ingested.path.unlink()
        parent = ingested.path.parent
        if remove_directory and parent.name.startswith("losslift-"):
            shutil.rmtree(parent, ignore_errors=True)
    except OSError:  # pragma: no cover - best effort cleanup
        pass
