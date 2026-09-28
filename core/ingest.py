"""Stage 0 — ingest (spec section 5).

Hash the file, look for a prior extraction of the same bytes, and hold the
bytes in temporary storage.

Retention: nothing is written outside a temporary directory, and
:func:`discard` removes it.  The dedupe cache is in-process and dies with the
session — persistent storage of claim data is out of scope (spec section 13).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

MAX_UPLOAD_BYTES = 64 * 1024 * 1024
PDF_MAGIC = b"%PDF-"

#: Written into every directory that holds staged bytes. It records, per staged
#: file, the document that owns it and the hash of its contents. ``discard``
#: deletes nothing it cannot match against it — an upload is the only thing
#: LossLift may remove, and only from a directory it can prove it made.
OWNER_MARKER_NAME = ".losslift-owner.json"

#: Staged directories alone are swept; a caller's own directory is never named
#: this way, and never removed.
STAGING_PREFIX = "losslift-"

#: Orphans older than this are swept at startup. Best effort only.
SWEEP_MAX_AGE_HOURS = 24


class IngestError(ValueError):
    """The upload is not a PDF this app can work with."""


class DiscardOutcome(str, Enum):
    """What happened when a staged file was discarded."""

    #: The file was there and is now gone, verified.
    DELETED = "deleted"
    #: There was nothing left to delete; the guarantee already holds.
    ALREADY_GONE = "already_gone"
    #: The path did not meet the ownership rules, so it was left alone.
    REFUSED = "refused"
    #: It should have gone and did not.
    FAILED = "failed"

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.value


@dataclass(frozen=True)
class DiscardResult:
    """The verified outcome of discarding one staged upload."""

    outcome: DiscardOutcome
    path: Path | None = None
    reason: str = ""

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.outcome.value

    @property
    def gone(self) -> bool:
        """Whether the caller may tell the user the file is deleted."""
        return self.outcome in (DiscardOutcome.DELETED, DiscardOutcome.ALREADY_GONE)


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


def _read_owner_marker(directory: Path) -> dict[str, dict[str, str]]:
    """The staged files this directory is known to own, by basename.

    A missing or unreadable marker means "not owned", never "delete anyway":
    the safe answer when the code cannot tell is to leave the file alone.
    """
    marker = directory / OWNER_MARKER_NAME
    try:
        raw = marker.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict):
        return {}
    owned: dict[str, dict[str, str]] = {}
    for name, entry in files.items():
        if isinstance(name, str) and isinstance(entry, dict):
            owned[name] = {
                "document_id": str(entry.get("document_id", "")),
                "sha256": str(entry.get("sha256", "")),
            }
    return owned


def _write_owner_marker(
    directory: Path, staged: Path, document_id: str, sha256: str
) -> None:
    """Record one staged file in its directory's ownership marker."""
    owned = _read_owner_marker(directory)
    owned[staged.name] = {"document_id": document_id, "sha256": sha256}
    marker = directory / OWNER_MARKER_NAME
    payload = json.dumps({"files": owned}, indent=2, sort_keys=True)
    temporary = marker.with_suffix(".json.tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, marker)


def _forget_owner_marker(directory: Path, staged_name: str) -> None:
    """Best-effort removal of one entry a failed stage left behind."""
    try:
        owned = _read_owner_marker(directory)
        if staged_name not in owned:
            return
        del owned[staged_name]
        marker = directory / OWNER_MARKER_NAME
        if owned:
            payload = json.dumps({"files": owned}, indent=2, sort_keys=True)
        else:
            payload = json.dumps({"files": {}}, indent=2, sort_keys=True)
        temporary = marker.with_suffix(".json.tmp")
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, marker)
    except OSError:  # pragma: no cover - best effort
        pass


def _inside_tempdir(path: Path) -> bool:
    """Whether a path resolves inside the system temporary directory."""
    try:
        root = Path(tempfile.gettempdir()).resolve()
        return path.resolve().is_relative_to(root)
    except (OSError, ValueError):
        return False


def _owned_for_discard(ingested: IngestedFile) -> DiscardResult | None:
    """Verify ownership of one staged path; a refusal is returned, not raised."""
    path = ingested.path
    if not path.exists():
        return None
    if path.is_symlink():
        return DiscardResult(
            DiscardOutcome.REFUSED, path, "the staged path is a symlink"
        )
    if not _inside_tempdir(path):
        return DiscardResult(
            DiscardOutcome.REFUSED, path, "the staged path is outside the temporary directory"
        )
    entry = _read_owner_marker(path.parent).get(path.name)
    if entry is None:
        return DiscardResult(
            DiscardOutcome.REFUSED, path, "the directory carries no ownership marker"
        )
    if entry.get("sha256") != ingested.sha256:
        return DiscardResult(
            DiscardOutcome.REFUSED, path, "the marker does not match this file's hash"
        )
    return None


def discard(ingested: IngestedFile, remove_directory: bool = True) -> DiscardResult:
    """Delete the staged bytes, and verify that it happened.

    Deletes only a path that resolves inside the temporary directory, is not a
    symlink, and is named in its directory's ownership marker with the hash the
    upload was ingested with. Anything else is refused and left on disk: an
    upload is the only thing LossLift may remove, and it says so rather than
    guessing at a path it does not own.

    Idempotent. The caller may tell the user "deleted" only on ``DELETED`` or
    ``ALREADY_GONE``.
    """
    path = ingested.path
    try:
        refusal = _owned_for_discard(ingested)
        if refusal is not None:
            return refusal

        if not path.exists():
            _remove_owned_directory(ingested, path, remove_directory)
            return DiscardResult(
                DiscardOutcome.ALREADY_GONE, path, "the staged file was already gone"
            )

        path.unlink()
        if path.exists():
            return DiscardResult(
                DiscardOutcome.FAILED, path, "the staged file is still on disk"
            )
        _remove_owned_directory(ingested, path, remove_directory)
        return DiscardResult(DiscardOutcome.DELETED, path, "deleted and verified")
    except OSError as error:  # pragma: no cover - best effort cleanup
        return DiscardResult(DiscardOutcome.FAILED, path, str(error))


def _remove_owned_directory(
    ingested: IngestedFile, path: Path, remove_directory: bool
) -> None:
    """Remove the private staging directory itself, only if LossLift made it."""
    if not (remove_directory and ingested.owns_directory):
        return
    directory = path.parent
    if directory.is_symlink() or not _inside_tempdir(directory):
        return
    if not (directory / OWNER_MARKER_NAME).is_file():
        return
    shutil.rmtree(directory, ignore_errors=True)


def sweep_orphaned_staging(
    base: str | Path | None = None,
    *,
    max_age_hours: float = SWEEP_MAX_AGE_HOURS,
    now: float | None = None,
) -> list[Path]:
    """Best-effort removal of staging directories orphaned by a crash.

    LossLift cannot delete a file when a session ends unexpectedly — Streamlit
    has no reliable hook for that — so a startup sweep clears what a previous
    run left behind. Only directories that carry the ownership marker and are
    older than ``max_age_hours`` are touched; a directory with no marker is
    left alone. It is best effort and says so: failures are swallowed.
    """
    root = Path(base) if base is not None else Path(tempfile.gettempdir())
    cutoff = (time.time() if now is None else now) - max_age_hours * 3600
    removed: list[Path] = []
    try:
        entries = list(root.iterdir())
    except OSError:
        return removed
    for entry in entries:
        try:
            if entry.is_symlink() or not entry.is_dir():
                continue
            if not entry.name.startswith(STAGING_PREFIX):
                continue
            if not (entry / OWNER_MARKER_NAME).is_file():
                continue
            if entry.stat().st_mtime >= cutoff:
                continue
            shutil.rmtree(entry, ignore_errors=True)
            if not entry.exists():
                removed.append(entry)
        except OSError:
            continue
    return removed


def stage_and_run(
    data: bytes,
    filename: str,
    run: Any,
    workdir: str | Path | None = None,
) -> tuple[IngestedFile, Any]:
    """Stage an upload, run the pipeline, and never orphan the file on failure.

    The staged copy is discarded if ``run`` raises for any reason, so a failure
    after ingest — extraction, mapping, registration — cannot leave a temporary
    file behind. On success ownership passes to the caller, which discards it
    after export, on delete, or when the document leaves the queue.
    """
    staged = ingest(data, filename, workdir)
    try:
        result = run(staged)
    except BaseException:
        discard(staged)
        raise
    return staged, result


def run_or_discard(staged: IngestedFile, run: Any) -> Any:
    """Re-run a pipeline over an already-staged file, discarding it on failure.

    Used by a mapping re-read: the bytes are the same ones already staged, so on
    failure there is nothing to retry with and the file goes.
    """
    try:
        return run(staged)
    except BaseException:
        discard(staged)
        raise


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
    document_id = str(uuid4())
    try:
        # The marker is written before the bytes: a process killed between the
        # two leaves no staged file, never an unmarked one that cleanup cannot
        # see. If the write below fails, the entry is removed again.
        _write_owner_marker(directory, target, document_id, digest)
        with target.open("xb") as handle:
            handle.write(data)
    except BaseException:
        target.unlink(missing_ok=True)
        _forget_owner_marker(directory, target.name)
        if owns_directory:
            shutil.rmtree(directory, ignore_errors=True)
        raise

    return IngestedFile(
        document_id=document_id,
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
            document_id = str(uuid4())
            # Provisional marker before the bytes, so a kill during the copy
            # leaves a marker-bearing directory the startup sweep can find
            # rather than an invisible staged file.
            _write_owner_marker(directory, temporary_target, document_id, "")
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

        _write_owner_marker(directory, temporary_target, document_id, digest)
        target = temporary_target
        temporary_target = None
        return IngestedFile(
            document_id=document_id,
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
        if temporary_target is not None and directory is not None:
            _forget_owner_marker(directory, temporary_target.name)
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
