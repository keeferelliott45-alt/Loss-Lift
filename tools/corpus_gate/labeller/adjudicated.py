"""Adjudicated truth: reading worksheets back into a private truth file.

The truth file lives beside the manifest, outside the repository, under the
same convention: document ids, hashes and field-level truth values are
acceptable locally, and the file never enters Git. Each adjudicated field
records what the extraction held at adjudication time alongside the truth,
so ``measure`` scores the extraction that was actually judged -- and a
worksheet regenerated after a pipeline change cannot silently re-point old
truth at new values.

The truth vocabulary is deliberately tiny. A blank cell is "not
adjudicated" and is written as a null, never as agreement. ``AGREE`` says
the extracted value is right as it stands. ``EMPTY`` says the field should
hold nothing -- a value there is a fabrication. Anything else is a literal
value in the field's own vocabulary: plain Decimal strings for money, ISO
dates, the canonical status and boolean words. Anything the schema cannot
accept is a stop, never a guess.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from core.schema import ClaimStatus, DocumentStatus
from tools.corpus_gate import manifest as manifests
from tools.corpus_gate.manifest import SetupError
from tools.corpus_gate.labeller import worksheet as sheets
from tools.corpus_gate.labeller.worksheet import (
    BOOL,
    DATE,
    DOC_STATUS,
    DOCUMENT_STATUS_FIELD,
    MONEY,
    STATUS,
    TEXT,
)

TRUTH_VERSION = 1

#: The extracted value is right as it stands.
AGREE = "AGREE"
#: The field should hold nothing; any value there is wrong.
EMPTY = "EMPTY"

KEY_SEPARATOR = ":"

_STATUS_VALUES = frozenset(status.value for status in ClaimStatus)
_DOC_STATUS_VALUES = frozenset(status.value for status in DocumentStatus)
_BOOL_VALUES = frozenset({"Y", "N"})

#: The fields a worksheet may adjudicate: the worksheet writer's own field
#: list plus the single document-status row. An owner-added row naming
#: anything else is refused, never silently read as text.
_KNOWN_FIELDS = frozenset(sheets.CLAIM_FIELDS) | {sheets.DOCUMENT_STATUS_FIELD}


@dataclass(frozen=True)
class ImportSummary:
    """Counts only: adjudication sessions report no document content."""

    documents_read: int
    documents_without_worksheet: tuple[str, ...]
    adjudicated: int
    unadjudicated: int


def field_key(claim_index: int | None, field_name: str) -> str:
    """A worksheet row's identity in the truth file."""
    if claim_index is None:
        return field_name
    return f"c{claim_index}{KEY_SEPARATOR}{field_name}"


def _validate_truth(doc_id: str, field_name: str, kind: str, raw: str) -> str | None:
    """One TRUTH cell, checked. Blank means not adjudicated."""
    truth = raw.strip()
    if not truth:
        return None
    if truth in (AGREE, EMPTY):
        return truth
    try:
        if kind == MONEY:
            # A non-finite Decimal (NaN, Infinity, -inf) is a spelling the
            # schema cannot mean: it is not a value the document can hold.
            if not Decimal(truth).is_finite():
                raise InvalidOperation(truth)
            return truth
        if kind == DATE:
            date.fromisoformat(truth)
            return truth
        if kind == STATUS:
            if truth in _STATUS_VALUES:
                return truth
        elif kind == DOC_STATUS:
            if truth in _DOC_STATUS_VALUES:
                return truth
        elif kind == BOOL:
            if truth in _BOOL_VALUES:
                return truth
        elif kind == TEXT:
            if len(truth) <= 4000:
                return truth
    except (InvalidOperation, ValueError):
        pass
    raise SetupError(
        f"worksheet for {doc_id}: field {field_name} has a truth value "
        f"its schema does not accept"
    )


def _row_key(
    doc_id: str, row: dict[str, str]
) -> tuple[str, str, str, str | None, str]:
    """Validate one worksheet row.

    Returns the truth-file key, the field name, its kind, the checked truth
    (None when the cell is blank), and the extracted value to record beside
    it. Anything the schema cannot accept is a stop.
    """
    if row["doc_id"] != doc_id:
        raise SetupError(f"worksheet for {doc_id}: a row names document {row['doc_id']}")
    field_name = row["field"].strip()
    if field_name not in _KNOWN_FIELDS:
        raise SetupError(
            f"worksheet for {doc_id}: field {field_name} is not a known field"
        )
    kind = sheets.field_kind(field_name)
    if kind == DOC_STATUS:
        claim_index = None
    else:
        raw_index = row["claim_index"].strip()
        if not raw_index.isdigit():
            raise SetupError(
                f"worksheet for {doc_id}: field {field_name} has no claim index"
            )
        claim_index = int(raw_index)
    truth = _validate_truth(doc_id, field_name, kind, row["TRUTH"])
    return field_key(claim_index, field_name), field_name, kind, truth, row["extracted_value"]


def _parse_worksheet(doc_id: str, path: Path) -> dict[str, dict[str, Any]]:
    """One adjudicated worksheet, validated, as truth-file fields."""
    fields: dict[str, dict[str, Any]] = {}
    for row in sheets.read_worksheet(path):
        key, field_name, kind, truth, extracted = _row_key(doc_id, row)
        if key in fields:
            raise SetupError(f"worksheet for {doc_id}: field {field_name} appears twice")
        fields[key] = {
            "kind": kind,
            "field": field_name,
            "extracted": extracted,
            "truth": truth,
        }
    return fields


def _load_existing(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise SetupError("the truth file is not valid JSON") from error
    if (
        not isinstance(payload, dict)
        or payload.get("version") != TRUTH_VERSION
        or not isinstance(payload.get("documents"), dict)
        or not isinstance(payload.get("manifest_sha256"), str)
    ):
        raise SetupError(f"the truth file must be a version {TRUTH_VERSION} object")
    return payload


def import_worksheets(
    corpus: Path,
    manifest_path: Path,
    worksheets_dir: Path,
    truth_path: Path,
    *,
    repo_root: Path | None = None,
) -> tuple[ImportSummary, dict[str, Any]]:
    """Read adjudicated worksheets and write the truth file.

    Every document is hash-verified against the manifest first; a document
    whose bytes have changed stops the import before any label is taken.
    """
    manifests.ensure_outside(corpus, repo_root, "corpus directory")
    manifests.ensure_outside(manifest_path, repo_root, "manifest")
    manifests.ensure_outside(truth_path, repo_root, "truth file")
    manifests.ensure_outside(worksheets_dir, repo_root, "worksheets directory")

    manifest = manifests.load(manifest_path)
    verification = manifests.verify(manifest, corpus)
    if not verification.ok:
        raise SetupError(
            "the corpus does not match the manifest "
            f"({len(verification.missing)} missing, {len(verification.mismatched)} changed)"
        )

    existing = _load_existing(truth_path)
    if existing is not None and existing["manifest_sha256"] != manifest.sha256:
        raise SetupError(
            "the truth file was adjudicated against a different manifest; "
            "start a new truth file rather than mixing corpus identities"
        )

    documents: dict[str, dict[str, Any]] = (
        {doc_id: doc for doc_id, doc in existing["documents"].items()}
        if existing
        else {}
    )
    adjudicated = 0
    unadjudicated = 0
    without: list[str] = []
    for entry in manifest.entries:
        path = sheets.worksheet_path(worksheets_dir, entry.id)
        if not path.is_file():
            without.append(entry.id)
            continue
        fields = dict(documents.get(entry.id, {}).get("fields", {}))
        for key, record in _parse_worksheet(entry.id, path).items():
            # A blank TRUTH cell means "not adjudicated", never "revoke": an
            # adjudicated truth is only ever replaced by another truth.
            if record["truth"] is None and key in fields \
                    and fields[key]["truth"] is not None:
                unadjudicated += 1
                continue
            fields[key] = record
            if record["truth"] is None:
                unadjudicated += 1
            else:
                adjudicated += 1
        documents[entry.id] = {"sha256": entry.sha256, "fields": fields}

    payload = {
        "version": TRUTH_VERSION,
        "manifest_sha256": manifest.sha256,
        "documents": documents,
    }
    truth_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = truth_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, truth_path)
    return (
        ImportSummary(
            documents_read=len(manifest.entries) - len(without),
            documents_without_worksheet=tuple(without),
            adjudicated=adjudicated,
            unadjudicated=unadjudicated,
        ),
        payload,
    )


def load_truth(truth_path: Path, manifest: manifests.Manifest) -> dict[str, Any]:
    """The truth file, checked against the manifest it must belong to."""
    payload = _load_existing(truth_path)
    if payload is None:
        raise SetupError(f"no truth file at {truth_path}")
    if payload["manifest_sha256"] != manifest.sha256:
        raise SetupError(
            "the truth file was adjudicated against a different manifest; "
            "it cannot be measured with this one"
        )
    return payload
