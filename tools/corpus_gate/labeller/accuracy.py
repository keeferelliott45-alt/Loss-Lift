"""Field-level accuracy of the extraction the owner adjudicated.

The metric definitions mirror the golden harness exactly, so a
real-corpus number and a synthetic one mean the same thing: correct
non-null values over expected non-null values, money and text reported
separately and never averaged together. A truth of ``EMPTY`` plays the
golden harness's "the document holds no value here": it is scored outside
the expected-non-null denominator, with a value the extraction nonetheless
printed counted as an extra value -- and as a null-as-zero when the value
was a printed zero, the lie the spec forbids.

Nothing is imputed. A field whose truth is blank is excluded and counted,
in every group it would otherwise have joined. Accuracy over zero
adjudicated fields is null, not 100%: an unmeasured group is never
allowed to look perfect.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from tools.corpus_gate import labels as corpus_labels
from tools.corpus_gate import manifest as manifests
from tools.corpus_gate.manifest import SetupError
from tools.corpus_gate.labeller import worksheet as sheets
from tools.corpus_gate.labeller.adjudicated import AGREE, EMPTY
from tools.corpus_gate.labeller.worksheet import (
    BOOL,
    DATE,
    DOC_STATUS,
    DOCUMENT_STATUS_FIELD,
    MONEY,
    STATUS,
    TEXT,
)

UNLABELLED = "(unlabelled)"


def _value_bucket() -> dict[str, int]:
    return {
        "expected": 0,
        "correct": 0,
        "missing": 0,
        "should_be_null": 0,
        "null_ok": 0,
        "nulls_as_zeros": 0,
        "extra_values": 0,
    }


def _empty_group() -> dict[str, Any]:
    return {
        "documents": 0,
        "money": _value_bucket(),
        "text": _value_bucket(),
        "claim_status": {"adjudicated": 0, "agree": 0, "disagree": 0},
        "document_status": {
            "flagged_agrees_right": 0,
            "flagged_agrees_wrong": 0,
            "clean_extraction_wrong": 0,
            "clean_agrees_right": 0,
            "unadjudicated": 0,
        },
        "excluded_fields": 0,
    }


def _values_match(kind: str, extracted: str, truth: str) -> bool:
    """The golden harness's comparison: Decimals for money, text otherwise."""
    if kind == MONEY:
        try:
            return Decimal(extracted) == Decimal(truth)
        except (InvalidOperation, ValueError):
            return False
    return extracted.strip() == truth.strip()


def _add_value_row(bucket: dict[str, int], kind: str, extracted: str, truth: str) -> None:
    if truth == EMPTY:
        bucket["should_be_null"] += 1
        if not extracted:
            bucket["null_ok"] += 1
        else:
            bucket["extra_values"] += 1
            if kind == MONEY:
                try:
                    if Decimal(extracted) == 0:
                        bucket["nulls_as_zeros"] += 1
                except (InvalidOperation, ValueError):
                    pass
        return
    # An expected value. AGREE on an empty extraction is a stop, not a pass:
    # "should be null" has its own word, and a blank is never agreement.
    bucket["expected"] += 1
    if not extracted:
        bucket["missing"] += 1
    elif truth == AGREE or _values_match(kind, extracted, truth):
        bucket["correct"] += 1


def _add_status_row(group: dict[str, Any], extracted: str, truth: str) -> None:
    bucket = group["claim_status"]
    bucket["adjudicated"] += 1
    if truth == AGREE or extracted == truth:
        bucket["agree"] += 1
    else:
        bucket["disagree"] += 1


def _add_document_status_row(group: dict[str, Any], extracted: str, truth: str) -> None:
    bucket = group["document_status"]
    if truth == AGREE:
        truth = extracted
    flagged = extracted == "NEEDS_REVIEW"
    if flagged:
        if truth == "NEEDS_REVIEW":
            bucket["flagged_agrees_right"] += 1
        elif truth == "CLEAN":
            bucket["flagged_agrees_wrong"] += 1
        else:
            bucket["unadjudicated"] += 1
    else:
        if truth == "NEEDS_REVIEW":
            bucket["clean_extraction_wrong"] += 1
        elif truth == "CLEAN":
            bucket["clean_agrees_right"] += 1
        else:
            bucket["unadjudicated"] += 1


def _add_entry(group: dict[str, Any], entry: dict[str, Any]) -> None:
    kind, extracted, truth = entry["kind"], entry["extracted"], entry["truth"]
    if kind in (MONEY, TEXT):
        bucket = group[kind]
        if truth is None:
            group["excluded_fields"] += 1
        else:
            _add_value_row(bucket, kind, extracted, truth)
        return
    if truth is None:
        # Excluded globally, and named in the cross-tab it would have joined.
        group["excluded_fields"] += 1
        if kind == DOC_STATUS:
            _add_document_status_row(group, extracted, truth)
        return
    if kind == STATUS:
        _add_status_row(group, extracted, truth)
    elif kind == DOC_STATUS:
        _add_document_status_row(group, extracted, truth)
    elif kind in (DATE, BOOL):
        # Dates and booleans are compared as text, exactly as the golden
        # harness compares their ISO / Y-N forms.
        _add_value_row(group["text"], TEXT, extracted, truth)


def _merge(into: dict[str, Any], other: dict[str, Any]) -> None:
    into["documents"] += other["documents"]
    into["excluded_fields"] += other["excluded_fields"]
    for kind in (MONEY, TEXT):
        for key, value in other[kind].items():
            into[kind][key] += value
    for key in ("claim_status", "document_status"):
        for key_, value in other[key].items():
            into[key][key_] += value


def _finalise(group: dict[str, Any]) -> dict[str, Any]:
    for kind in (MONEY, TEXT):
        bucket = group[kind]
        expected = bucket["expected"]
        bucket["wrong"] = expected - bucket["correct"]
        bucket["accuracy"] = (
            round(bucket["correct"] / expected, 6) if expected else None
        )
    status = group["claim_status"]
    status["agreement"] = (
        round(status["agree"] / status["adjudicated"], 6)
        if status["adjudicated"]
        else None
    )
    return group


def _bucket_for(groups: dict[str, dict[str, Any]], name: str) -> dict[str, Any]:
    if name not in groups:
        groups[name] = _empty_group()
    return groups[name]


def measure(
    truth: dict[str, Any],
    manifest: manifests.Manifest,
    labels: corpus_labels.Labels | None,
) -> dict[str, Any]:
    """Field-level accuracy over an adjudicated truth file."""
    documents = truth["documents"]
    overall = _empty_group()
    per_document: dict[str, dict[str, Any]] = {}
    per_carrier: dict[str, dict[str, Any]] = {}
    per_family: dict[str, dict[str, Any]] = {}
    unlabelled = 0

    for entry in manifest.entries:
        doc = documents.get(entry.id)
        if doc is None:
            continue
        group = _empty_group()
        group["documents"] = 1
        for record in doc["fields"].values():
            _add_entry(group, record)
        per_document[entry.id] = group
        _merge(overall, group)
        doc_labels = labels.labels.get(entry.id, {}) if labels else {}
        if not doc_labels.get("carrier") and not doc_labels.get("template_family"):
            unlabelled += 1
        if doc_labels.get("carrier"):
            _merge(_bucket_for(per_carrier, doc_labels["carrier"]), group)
        if doc_labels.get("template_family"):
            _merge(_bucket_for(per_family, doc_labels["template_family"]), group)

    for group in per_document.values():
        _finalise(group)
    for groups in (per_carrier, per_family):
        for group in groups.values():
            _finalise(group)
    _finalise(overall)

    return {
        "documents_listed": len(manifest.entries),
        "documents_adjudicated": len(per_document),
        "documents_unlabelled": unlabelled,
        "overall": overall,
        "per_document": per_document,
        "per_carrier": dict(sorted(per_carrier.items())),
        "per_template_family": dict(sorted(per_family.items())),
    }


def _rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2%}"


def _bucket_line(name: str, bucket: dict[str, Any], width: int) -> str:
    return (
        f"{name:<{width}}  {_rate(bucket['accuracy']):>8}  "
        f"({bucket['correct']}/{bucket['expected']})  "
        f"nulls-as-zeros {bucket['nulls_as_zeros']}  "
        f"should-be-null {bucket['should_be_null']} "
        f"(ok {bucket['null_ok']}, extra {bucket['extra_values']})"
    )


def _group_section(title: str, group: dict[str, Any]) -> list[str]:
    lines = [f"## {title}", ""]
    lines.append(
        f"money {_rate(group['money']['accuracy'])} "
        f"({group['money']['correct']}/{group['money']['expected']})"
    )
    lines.append(
        f"text  {_rate(group['text']['accuracy'])} "
        f"({group['text']['correct']}/{group['text']['expected']})"
    )
    lines.append("")
    cross = group["document_status"]
    lines.append(
        f"claim_status agreement: {_rate(group['claim_status']['agreement'])} "
        f"({group['claim_status']['agree']}/{group['claim_status']['adjudicated']})"
    )
    lines.append(
        "document-status cross-tab: extraction flagged NEEDS_REVIEW and truth "
        f"agrees right {cross['flagged_agrees_right']}, agrees wrong "
        f"{cross['flagged_agrees_wrong']}; extraction CLEAN and truth disagrees "
        f"{cross['clean_extraction_wrong']}, agrees {cross['clean_agrees_right']}; "
        f"unadjudicated {cross['unadjudicated']}"
    )
    lines.append(f"excluded (un-adjudicated) fields: {group['excluded_fields']}")
    return lines


def render_markdown(report: dict[str, Any]) -> str:
    """The full local report: everything, including per-document rows."""
    lines = [
        "# Real-document accuracy (labeller)",
        "",
        f"Corpus: {report['documents_listed']} document(s) listed, "
        f"{report['documents_adjudicated']} adjudicated. "
        f"{report['documents_unlabelled']} have no carrier/template label.",
        "",
    ]
    lines.extend(_group_section("Overall", report["overall"]))
    lines.append("")
    for title, key in (("Per carrier", "per_carrier"), ("Per template family", "per_template_family")):
        groups = report[key]
        lines.append(f"## {title}")
        lines.append("")
        if not groups:
            lines.append("_No labels supplied for this split._")
        width = max((len(name) for name in groups), default=4)
        for name, group in groups.items():
            lines.append(_bucket_line(name, group["money"], width))
            lines.append(
                f"{name:<{width}}  text {_rate(group['text']['accuracy'])} "
                f"({group['text']['correct']}/{group['text']['expected']})"
            )
        lines.append("")
    lines.append("## Per document")
    lines.append("")
    width = max((len(doc_id) for doc_id in report["per_document"]), default=4)
    for doc_id, group in report["per_document"].items():
        lines.append(_bucket_line(doc_id, group["money"], width))
        lines.append(
            f"{doc_id:<{width}}  text {_rate(group['text']['accuracy'])} "
            f"({group['text']['correct']}/{group['text']['expected']})"
        )
    lines.append("")
    lines.append("## What may be published")
    lines.append("")
    lines.append(
        "Aggregate and per-carrier/per-family rates and counts above contain "
        "no document content and no document ids: they may be published. The "
        "per-document table is ids only, but it belongs to the private corpus "
        "and stays local. Nothing from a document's contents appears anywhere "
        "in this report."
    )
    return "\n".join(lines) + "\n"


def publishable_summary(report: dict[str, Any]) -> str:
    """The stdout summary: rates and counts only, no document ids."""
    overall = report["overall"]
    lines = [
        "Real-document accuracy (labeller)",
        f"  documents: {report['documents_adjudicated']} of "
        f"{report['documents_listed']} adjudicated",
        f"  money: {_rate(overall['money']['accuracy'])} "
        f"({overall['money']['correct']}/{overall['money']['expected']})",
        f"  text:  {_rate(overall['text']['accuracy'])} "
        f"({overall['text']['correct']}/{overall['text']['expected']})",
        f"  claim_status agreement: {_rate(overall['claim_status']['agreement'])} "
        f"({overall['claim_status']['agree']}/{overall['claim_status']['adjudicated']})",
        f"  excluded (un-adjudicated) fields: {overall['excluded_fields']}",
        "Publishable: these counts and rates (and the per-carrier and "
        "per-family rates in the local report) name no document and carry no "
        "document content.",
    ]
    return "\n".join(lines)


def measure_to_files(
    truth: dict[str, Any],
    manifest: manifests.Manifest,
    labels: corpus_labels.Labels | None,
    out_dir: Path,
    *,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Compute and write measure.json and measure.md outside the repo."""
    manifests.ensure_outside(out_dir, repo_root, "report directory")
    report = measure(truth, manifest, labels)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    tmp = out_dir / "measure.json.tmp"
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, out_dir / "measure.json")
    tmp_md = out_dir / "measure.md.tmp"
    tmp_md.write_text(render_markdown(report), encoding="utf-8")
    os.replace(tmp_md, out_dir / "measure.md")
    return report
