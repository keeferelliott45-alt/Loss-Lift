"""The public report: counts, rates, fixed categories and sealed ids only.

A qualification report may be shared. It says how well a commit read a set of
documents and never what the documents say: no claim number, amount, date,
file name, path or exception text. Document ids are sealed under the
manifest's salt, so a reader cannot tie a row to a manifest they do not hold.

:func:`check_public` enforces this on the finished report before it is
written: every private string the run touched is searched for in the
serialised report, and one hit stops the write.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Iterable

REPORT_VERSION = 1

#: Why a document was or was not scored. Nothing else is ever said about it.
CATEGORIES = (
    "scored",
    "missing_file",
    "bytes_changed",
    "pipeline_failed",
)


class PrivateDataInReport(RuntimeError):
    """The report would carry private data; it was not written."""


def seal(document_id: str, salt: bytes) -> str:
    mac = hmac.new(salt, document_id.encode("utf-8"), hashlib.sha256)
    return "doc:" + mac.hexdigest()[:16]


def document_record(sealed: str, category: str, *, qualified: bool,
                    metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    if category not in CATEGORIES:
        raise ValueError("unknown document category")
    record: dict[str, Any] = {"document": sealed, "category": category,
                              "qualified_truth": qualified}
    if metrics is not None:
        record["metrics"] = metrics
    return record


def build_report(*, commit: str, manifest_sha256: str, truth_sha256: str,
                 records: list[dict[str, Any]], totals: dict[str, Any],
                 vision_replay: bool) -> dict[str, Any]:
    categories = {name: sum(1 for r in records if r["category"] == name)
                  for name in CATEGORIES}
    unresolved = sum(1 for r in records if r["category"] != "scored")
    unread = totals["accounting"]["claim_pages_unread"]
    insufficient = []
    if unresolved:
        insufficient.append("documents_not_scored")
    if unread:
        insufficient.append("claim_pages_unread")
    if totals["qualified"] != totals["documents"] or not records:
        insufficient.append("truth_not_adjudicated")
    return {
        "report_version": REPORT_VERSION,
        "commit": commit,
        "manifest_sha256": manifest_sha256,
        "truth_sha256": truth_sha256,
        "vision": "replay" if vision_replay else "off",
        "documents": len(records),
        "categories": categories,
        "qualification": "measured" if not insufficient else "insufficient",
        "insufficient_because": insufficient,
        "totals": totals,
        "per_document": records,
    }


def check_public(report: dict[str, Any], private: Iterable[str]) -> None:
    """Refuse a report in which any private string appears."""
    text = json.dumps(report, sort_keys=True, ensure_ascii=False).casefold()
    for value in private:
        if value and len(value) >= 3 and value.casefold() in text:
            raise PrivateDataInReport(
                "the report would carry private data; it was not written")


def render(report: dict[str, Any]) -> str:
    """A short text summary of the report: counts and rates only."""
    totals = report["totals"]

    def pct(value: float | None) -> str:
        return "n/a" if value is None else f"{value * 100:.2f}%"

    claims = totals["overall"]["claims"]
    money = totals["overall"]["money"]
    critical = totals["overall"]["critical"]
    auto = totals["auto_accepted"]
    lines = [
        f"LossLift qualification — commit {report['commit'][:12]}",
        f"documents: {report['documents']} "
        + " ".join(f"{k}={v}" for k, v in report["categories"].items() if v),
        f"qualification: {report['qualification']}"
        + (f" ({', '.join(report['insufficient_because'])})"
           if report["insufficient_because"] else ""),
        f"claims: precision {pct(claims['precision'])} recall {pct(claims['recall'])} "
        f"(matched {claims['matched']}, missing {claims['missing']}, "
        f"invented {claims['invented']}, ambiguous {claims['ambiguous_truth']})",
        f"money fields: precision {pct(money['precision'])} recall {pct(money['recall'])}",
        f"critical fields: precision {pct(critical['precision'])} "
        f"recall {pct(critical['recall'])}",
        f"auto-accepted claims: precision {pct(auto['claims']['precision'])} "
        f"recall {pct(auto['claims']['recall'])}; money precision "
        f"{pct(auto['money']['precision'])} recall {pct(auto['money']['recall'])}",
        f"document review rate: {pct(totals['status']['document_review_rate'])}",
        "must be zero: " + ", ".join(f"{k}={v}" for k, v in totals["must_be_zero"].items()),
    ]
    return "\n".join(lines) + "\n"
