"""Stage 0 — ingest (spec section 5).

Hash the file, look for a prior extraction of the same bytes, and hold the
bytes in temporary storage.

Retention: nothing is written outside a temporary directory, and
:func:`discard` removes it.  The dedupe cache is in-process and dies with the
session — persistent storage of claim data is out of scope (spec section 13).
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

MAX_UPLOAD_BYTES = 64 * 1024 * 1024
PDF_MAGIC = b"%PDF-"
XLSX_MAGIC = b"PK\x03\x04"
#: Every OOXML workbook carries this part; a zip without it is not a workbook.
XLSX_CONTENT_TYPES = "[Content_Types].xml"

#: Upload kinds, in the suffix each is staged under.
KIND_SUFFIX = {"pdf": ".pdf", "xlsx": ".xlsx", "csv": ".csv"}
SUFFIX_KIND = {suffix: kind for kind, suffix in KIND_SUFFIX.items()}
#: Legacy Excel containers are refused outright: the BIFF/OLE formats are a
#: different parser with its own failure modes, and .xlsm carries macros that
#: must never be opened. Fail closed rather than guess.
UNSUPPORTED_SUFFIXES = {".xls", ".xlsm", ".xlt", ".xltm", ".xlsb"}


class IngestError(ValueError):
    """The upload is not a document this app can work with."""


def sniff_kind(data: bytes) -> str:
    """Name the upload kind from its bytes: ``pdf``, ``xlsx`` or ``csv``.

    The magic decides, never the filename. A zip counts as a workbook only
    when it carries the OOXML content-types part; anything else -- a legacy
    .xls container, a macro workbook saved as zip, arbitrary binary -- is
    refused rather than parsed as text. Delimited text must decode as UTF-8
    and carry no NUL byte, which binary formats always do.
    """
    if data.startswith(PDF_MAGIC):
        return "pdf"
    if data.startswith(XLSX_MAGIC):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = archive.namelist()
        except (zipfile.BadZipFile, OSError) as error:
            raise IngestError(
                "The upload looks like a zip archive but is not a readable "
                "Excel workbook. Save it as .xlsx (or export a CSV) and "
                "upload it again."
            ) from error
        if XLSX_CONTENT_TYPES in names:
            return "xlsx"
        raise IngestError(
            "The upload is a zip archive but not an Excel workbook. "
            "Save it as .xlsx (or export a CSV) and upload it again."
        )
    if b"\x00" in data:
        raise IngestError(
            "The upload is not a supported loss-run file. Upload a PDF, "
            "XLSX or CSV loss run."
        )
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise IngestError(
            "The upload is not a supported loss-run file. Upload a PDF, "
            "XLSX or CSV loss run."
        ) from error
    return "csv"


def _kind_for(filename: str, sniffed: str) -> str:
    """Check the filename agrees with the bytes, and stage under the right kind.

    A name that contradicts the magic means the file was renamed or saved
    wrong; neither reading is safe, so the upload is refused. Legacy Excel
    formats are refused before anything else about them is asked.
    """
    suffix = Path(filename).suffix.lower()
    if suffix in UNSUPPORTED_SUFFIXES:
        raise IngestError(
            f"{Path(filename).name} is a legacy Excel format, which is not "
            "supported. Save it as .xlsx (or export a CSV) and upload it again."
        )
    declared = SUFFIX_KIND.get(suffix)
    if declared is None:
        raise IngestError(
            f"{Path(filename).name} does not have a .pdf, .xlsx or .csv "
            "extension. Rename the file with its true extension and upload "
            "it again."
        )
    if declared != sniffed:
        raise IngestError(
            f"{Path(filename).name} is named {suffix} but its contents are "
            "not that kind of file. Upload the file under its true format."
        )
    return declared


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
    #: What kind of document the bytes are: "pdf", "xlsx" or "csv". Decided
    #: from the bytes at ingest, never from the filename.
    kind: str = "pdf"
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
    """Validate, hash and stage an uploaded PDF, XLSX or CSV loss run."""
    if not data:
        raise IngestError(f"{filename} is empty. Upload the document again.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise IngestError(
            f"{filename} is {len(data) / 1e6:.0f} MB. The limit is "
            f"{MAX_UPLOAD_BYTES / 1e6:.0f} MB — split the document and retry."
        )
    kind = _kind_for(filename, sniff_kind(data))

    owns_directory = not bool(workdir)
    directory = (
        Path(tempfile.mkdtemp(prefix="losslift-"))
        if owns_directory
        else Path(workdir)
    )
    directory.mkdir(parents=True, exist_ok=True)

    digest = sha256_bytes(data)
    safe_name = Path(filename).name or "upload.pdf"
    target = directory / f".upload-{uuid4().hex}{KIND_SUFFIX[kind]}"
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
        kind=kind,
        owns_directory=owns_directory,
    )


def ingest_path(path: str | Path, workdir: str | Path | None = None) -> IngestedFile:
    """Snapshot an existing PDF, XLSX or CSV without trusting a changing pathname."""
    source = Path(path).resolve()
    directory: Path | None = None
    temporary_target: Path | None = None
    temporary_target_created = False
    owns_directory = False
    try:
        with source.open("rb") as source_handle:
            identity = _identity(os.fstat(source_handle.fileno()))
            if identity.size == 0:
                raise IngestError(f"{source.name} is empty. Upload the document again.")
            if identity.size > MAX_UPLOAD_BYTES:
                raise IngestError(
                    f"{source.name} is {identity.size / 1e6:.0f} MB. The limit is "
                    f"{MAX_UPLOAD_BYTES / 1e6:.0f} MB — split the document and retry."
                )
            data = source_handle.read(MAX_UPLOAD_BYTES + 1)
            kind = _kind_for(source.name, sniff_kind(data))
            source_handle.seek(0)

            owns_directory = not bool(workdir)
            directory = (
                Path(tempfile.mkdtemp(prefix="losslift-"))
                if owns_directory
                else Path(workdir)
            )
            directory.mkdir(parents=True, exist_ok=True)
            temporary_target = directory / f".snapshot-{uuid4().hex}{KIND_SUFFIX[kind]}"
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
            kind=kind,
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
