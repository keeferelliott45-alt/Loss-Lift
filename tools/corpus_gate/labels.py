"""Manual labels for corpus documents, kept apart from the manifest.

The manifest says *which* documents the gate measures and proves they are the
same bytes; it changes only when the corpus does. Labels say what a person
judged each document to be -- its carrier, template family, whether it is a
scan, a packet, how many runs it should read as -- and they are refined far
more often. Keeping them in their own file, keyed by the manifest's document
id, means relabelling never touches the manifest's hashes or salt, and the gate
itself stays a correctness check: it does not read labels at all.

A labels file names carriers and templates, which are facts about real
documents, so it lives outside the repository beside the manifest, under the
same protections.

What a person assigns lives here. What the collector measures -- detected
runs, extraction method, final status, reconciliation state, refused and
unplaced counts -- is never copied in: an analysis joins the two by id, so a
label can be compared with what LossLift actually did (expected run count
against detected runs, say) rather than overwritten by it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from tools.corpus_gate.manifest import DOCUMENT_ID, Manifest, SetupError

LABELS_VERSION = 1

#: Each label, and the values it may take. ``str`` is free text a person
#: writes; a tuple is an enumeration; ``int`` and ``bool`` are what they say.
#: Every label is optional -- unknown is null, never a guess.
LABELS: dict[str, Any] = {
    "carrier": str,
    "template_family": str,
    "line_of_business": ("WC", "GL", "AUTO", "PROP", "UMB", "OTHER", "MIXED"),
    "source": ("digital", "scanned", "mixed"),
    "scan_quality": ("good", "fair", "poor", "not_scanned"),
    "shape": ("single", "packet"),
    "expected_runs": int,
    "printed_totals": bool,
    "printed_claim_count": bool,
    "claim_count_band": ("0", "1-9", "10-49", "50-199", "200+"),
}


@dataclass(frozen=True)
class Labels:
    labels: dict[str, dict[str, Any]]


def _check_value(doc_id: str, name: str, value: Any) -> Any:
    kind = LABELS.get(name)
    if kind is None:
        raise SetupError(f"labels for {doc_id}: unknown label {name!r}")
    if value is None:
        return None
    if kind is str:
        ok = isinstance(value, str) and 0 < len(value) <= 120
    elif kind is int:
        ok = type(value) is int and 0 <= value <= 1000
    elif kind is bool:
        ok = type(value) is bool
    else:
        ok = value in kind
    if not ok:
        raise SetupError(f"labels for {doc_id}: {name} has a value it cannot take")
    return value


def validate(data: Any, manifest: Manifest | None = None) -> Labels:
    """A labels file's content, checked; every id must be a manifest document."""
    if not isinstance(data, Mapping) or set(data) != {"version", "labels"} \
            or data["version"] != LABELS_VERSION or not isinstance(data["labels"], Mapping):
        raise SetupError("a labels file must be {\"version\": 1, \"labels\": {...}}")
    listed = {entry.id for entry in manifest.entries} if manifest is not None else None
    out: dict[str, dict[str, Any]] = {}
    for doc_id, labels in data["labels"].items():
        if not isinstance(doc_id, str) or not DOCUMENT_ID.fullmatch(doc_id):
            raise SetupError("labels for a document id of the wrong shape")
        if listed is not None and doc_id not in listed:
            raise SetupError(f"labels for {doc_id}, which the manifest does not list")
        if not isinstance(labels, Mapping):
            raise SetupError(f"labels for {doc_id} must be an object")
        out[doc_id] = {name: _check_value(doc_id, name, value) for name, value in labels.items()}
    return Labels(out)


def load(path: Path, manifest: Manifest | None = None) -> Labels:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SetupError(f"no labels file at {path}") from None
    except ValueError:
        raise SetupError("the labels file is not JSON") from None
    return validate(data, manifest)


def template(manifest: Manifest) -> dict[str, Any]:
    """A labels file with every document listed and every label unknown."""
    return {"version": LABELS_VERSION,
            "labels": {entry.id: {name: None for name in LABELS} for entry in manifest.entries}}


def write_template(manifest: Manifest, path: Path, *, force: bool = False) -> None:
    if path.exists() and not force:
        raise SetupError(f"{path} already exists; pass --force to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(template(manifest), indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
