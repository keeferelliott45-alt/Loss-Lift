"""Local, privacy-safe operational telemetry.

LossLift has to be able to say how often a document can be trusted without a
person, how often it needs one, and how often something may not be accounted
for at all -- and for which kinds of document. This module turns a processed
document, a reviewer's decision or an export into one JSON line of counts,
flags and enumeration tokens, appended to a local file. Nothing leaves the
machine and nothing is sent anywhere.

**Nothing from the document is ever written.** No claim numbers, names,
descriptions, amounts, dates, carrier or insured names, file names, file
hashes or finding messages. Every event is checked before it is written:
each value must be a number, a boolean, null, or a string of a fixed safe
shape (a rule id, a lower-case token, a canonical field name, a timestamp, a
random id), and an event that is not is refused rather than written. The
document id is the random id LossLift gave the upload, not anything derived
from the file.

The measured names follow the corpus gate's (``claim_count``, ``status``,
``review_status``, ``runs``, ``refused``, ``unplaced``, ``findings.by_rule``
...), so the two can be read side by side. The file lives at
``LOSSLIFT_TELEMETRY_PATH`` (default ``data/telemetry/events.jsonl``, which is
git-ignored); ``LOSSLIFT_TELEMETRY=off`` turns it off.

``python -m core.telemetry summarize [path]`` prints the auto-safe, needs-review
and unresolved rates by document class.
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from core.review import canonical_run_status, canonical_status, trust_class
from core.schema import (
    CANONICAL_FIELDS,
    DocumentStatus,
    ExtractionMethod,
    FindingCategory,
    Resolution,
    ReviewAction,
    RunConfidence,
    Severity,
    SourceMethod,
)

SCHEMA = 1
DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data" / "telemetry" / "events.jsonl"

_RULE = re.compile(r"R-\d{1,3}[a-z]?")
_KEY = re.compile(r"[a-z][a-z0-9_]{0,31}")
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z")
_ID = re.compile(r"[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_FIELDS = frozenset(CANONICAL_FIELDS)

#: Every word a value may be. A lower-case word off a document has the same
#: shape as a token, so shape alone cannot be the test: a string value must
#: be one of these (or a rule id, a timestamp or a random id).
VOCABULARY = frozenset({
    *(member.value for enum in (ExtractionMethod, SourceMethod, RunConfidence, ReviewAction,
                                FindingCategory, DocumentStatus) for member in enum),
    *(member.value for member in Severity), *(member.value.lower() for member in Severity),
    "auto_safe", "needs_review", "unresolved", "tie", "mismatch", "not_printed",
    "document_processed", "claim_edited", "finding_resolved", "exported",
    "profile", "heuristic", "llm", "manual", "default", "evidence", "override",
    "xlsx", "json", "zip", "packet", "single", "unknown", "other",
    "classify", "digital", "vision", "mapping", "assemble", "reconcile", "total", "mixed",
})


def _safe_string(value: str) -> bool:
    if value in VOCABULARY or value in _FIELDS:
        return True
    if _RULE.fullmatch(value) or _STAMP.fullmatch(value) or _ID.fullmatch(value):
        return True
    if value.count("/") == 1:       # rule/severity, rule/category
        rule, token = value.split("/")
        return bool(_RULE.fullmatch(rule)) and token in VOCABULARY
    if value.count(":") == 1:       # document class
        return all(part in VOCABULARY for part in value.split(":"))
    return False


class UnsafeEvent(ValueError):
    """An event carried a value outside the safe shapes; it was not written."""


def _check(value: Any, where: str = "event") -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        return
    if isinstance(value, str):
        if _safe_string(value):
            return
        raise UnsafeEvent(f"{where} holds a string outside the vocabulary")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or not (_KEY.fullmatch(key) or _safe_string(key)):
                raise UnsafeEvent(f"{where} holds a key outside the vocabulary")
            _check(item, f"{where}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _check(item, f"{where}[{index}]")
        return
    raise UnsafeEvent(f"{where} holds a {type(value).__name__}")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def new_session() -> str:
    """A random, meaningless id tying one sitting's events together."""
    return uuid.uuid4().hex


def _token(value: Any) -> str | None:
    text = getattr(value, "value", value)
    if text is None:
        return None
    text = str(text)
    return text if text in VOCABULARY else "other"


def _field(name: Any) -> str | None:
    return name if isinstance(name, str) and name in _FIELDS else None


def document_class(result: Any) -> str:
    """How a document is broken down in the rates: reader and shape.

    ``digital:single``, ``vision:packet``, ``mixed:single`` ... -- the two
    distinctions most likely to move how often a document can be trusted.
    """
    document = result.document
    method = _token(document.extraction_method) or "unknown"
    return f"{method}:{'packet' if document.is_packet else 'single'}"


def _tie(present: bool, findings: int) -> str:
    if not present:
        return "not_printed"
    return "mismatch" if findings else "tie"


def document_facts(result: Any) -> dict[str, Any]:
    """Everything measured about one processed document. Counts and tokens only."""
    document = result.document
    reconciliation = result.reconciliation
    findings = list(reconciliation.findings)
    by_rule = Counter(f.rule_id for f in findings)
    runs = list(document.runs)
    packet = document.is_packet
    totals_present = (any(run.printed_totals for run in runs) if packet
                      else bool(document.printed_totals))
    count_present = (any(run.printed_claim_count is not None for run in runs) if packet
                     else document.printed_claim_count is not None)
    classification = getattr(result, "classification", None)
    return {
        "document_class": document_class(result),
        "extraction_method": _token(document.extraction_method),
        "pages": {
            "page_count": document.page_count,
            "digital": len(getattr(classification, "digital_pages", []) or []),
            "scanned": len(document.scanned_pages),
            "failed": len(document.failed_pages),
            "unresolved": len(document.unresolved_pages),
            "skipped": len(document.skipped_pages),
        },
        "profile_matched": bool(getattr(result, "profile", None)),
        "mapping_source": _token(getattr(getattr(result, "mapping", None), "source", None)),
        "needs_mapping": _needs_mapping(result),
        "vision_pages": int(getattr(result, "vision_pages", 0) or 0),
        "timings": {
            stage: float(seconds) for stage, seconds in (getattr(result, "timings", {}) or {}).items()
            if stage in VOCABULARY
        },
        "status": _token(reconciliation.status),
        "review_status": _token(canonical_status(
            reconciliation, needs_mapping=_needs_mapping(result))),
        "trust": trust_class(reconciliation, needs_mapping=_needs_mapping(result)),
        "runs": {
            "count": max(len(runs), 1),
            "packet": packet,
            "unsettled": sum(1 for run in runs if run.ambiguous),
            "incomplete": sum(1 for run in runs if run.incomplete),
            "confidence": dict(Counter(_token(run.confidence) for run in runs)) if packet else {},
            "items": [
                {
                    "index": index,
                    "confidence": _token(run.confidence),
                    "ambiguous": run.ambiguous,
                    "incomplete": bool(run.incomplete),
                    "claims": len(document.run_claims(run)),
                    "printed_claim_count": run.printed_claim_count,
                    "status": _token(canonical_run_status(
                        reconciliation, run.run_id, needs_mapping=_needs_mapping(result))),
                }
                for index, run in enumerate(runs, start=1)
            ] if packet else [],
        },
        "claims": {
            "claim_count": len(document.claims),
            "printed_claim_count": document.printed_claim_count if not packet else None,
            "refused": len(document.refused_claim_rows),
            "refused_reported": sum(1 for row in document.refused_claim_rows if row.report),
            "unplaced": len(document.unplaced_rows),
            "printed_totals_present": totals_present,
            "printed_count_present": count_present,
            "r04": _tie(totals_present, by_rule.get("R-04", 0)),
            "r05": _tie(count_present, by_rule.get("R-05", 0)),
        },
        "findings": {
            "total": len(findings),
            "by_rule": dict(sorted(by_rule.items())),
            "by_rule_severity": dict(sorted(Counter(
                f"{f.rule_id}/{f.severity.value}" for f in findings).items())),
            "by_rule_category": dict(sorted(Counter(
                f"{f.rule_id}/{f.category.value}" for f in findings).items())),
        },
    }


def _event(kind: str, session: str | None, document_id: str | None, **body: Any) -> dict[str, Any]:
    event = {"schema": SCHEMA, "event": kind, "at": _now(),
             "session": session, "document": document_id, **body}
    _check(event)
    return event


def _needs_mapping(result: Any) -> bool:
    """Whether a column mapping is still to be confirmed; every event says so."""
    return bool(getattr(result, "needs_mapping", False))


def processed_event(result: Any, *, session: str | None = None) -> dict[str, Any]:
    return _event("document_processed", session, result.document.document_id,
                  **document_facts(result))


def review_events(
    result: Any, entries: Sequence[Resolution], *, session: str | None = None
) -> list[dict[str, Any]]:
    """One event per review-log entry: an edit or a decision about a finding.

    Only the field's canonical name and whether the value was / is empty --
    never the value itself.
    """
    after = canonical_status(result.reconciliation, needs_mapping=_needs_mapping(result))
    events = []
    for entry in entries:
        rule = entry.rule_id if _RULE.fullmatch(entry.rule_id or "") else None
        events.append(_event(
            "claim_edited" if entry.action.value in ("edited", "deleted") else "finding_resolved",
            session, result.document.document_id,
            action=_token(entry.action),
            rule_id=rule,
            severity=_token(entry.severity.lower()) if entry.severity else None,
            field=_field(entry.field),
            was_null=None if not entry.changed_a_value else entry.before in (None, ""),
            now_null=None if not entry.changed_a_value else entry.after in (None, ""),
            review_status_after=_token(after),
        ))
    return events


def export_event(
    result: Any, *, fmt: str, redacted: bool, seconds_since_processed: float | None = None,
    session: str | None = None,
) -> dict[str, Any]:
    return _event(
        "exported", session, result.document.document_id,
        format=_token(fmt),
        redacted=bool(redacted),
        review_status=_token(canonical_status(
            result.reconciliation, needs_mapping=_needs_mapping(result))),
        trust=trust_class(result.reconciliation, needs_mapping=_needs_mapping(result)),
        document_class=document_class(result),
        seconds_since_processed=seconds_since_processed,
    )


def telemetry_path() -> Path | None:
    """Where events go, or None when telemetry is off."""
    if os.getenv("LOSSLIFT_TELEMETRY", "").strip().lower() in ("off", "0", "false", "no"):
        return None
    configured = os.getenv("LOSSLIFT_TELEMETRY_PATH")
    return Path(configured) if configured else DEFAULT_PATH


def emit(events: dict[str, Any] | Iterable[dict[str, Any]], path: Path | None = None) -> bool:
    """Append events to the local file. Never raises into the caller.

    Telemetry is never allowed to break processing: an unwritable file or a
    refused event is dropped, and False says so.
    """
    batch = [events] if isinstance(events, dict) else list(events)
    target = path if path is not None else telemetry_path()
    if target is None or not batch:
        return False
    try:
        for event in batch:
            _check(event)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            for event in batch:
                handle.write(json.dumps(event, sort_keys=True) + "\n")
        return True
    except (OSError, UnsafeEvent):
        return False


def summarize(path: Path | None = None) -> dict[str, Any]:
    """Auto-safe, needs-review and unresolved rates, overall and by document class.

    The latest ``document_processed`` event per document decides its outcome.
    """
    source = path or telemetry_path() or DEFAULT_PATH
    latest: dict[str, dict[str, Any]] = {}
    if source.exists():
        for line in source.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("event") == "document_processed" and event.get("document"):
                latest[event["document"]] = event
    by_class: dict[str, Counter] = defaultdict(Counter)
    overall: Counter = Counter()
    for event in latest.values():
        by_class[event.get("document_class", "unknown")][event.get("trust", "unknown")] += 1
        overall[event.get("trust", "unknown")] += 1

    def rates(counts: Counter) -> dict[str, Any]:
        total = sum(counts.values())
        return {"documents": total, **{
            outcome: round(counts[outcome] / total, 4) if total else None
            for outcome in ("auto_safe", "needs_review", "unresolved")
        }}

    return {"overall": rates(overall),
            "by_class": {name: rates(counts) for name, counts in sorted(by_class.items())}}


def _main(argv: Sequence[str]) -> int:
    if not argv or argv[0] != "summarize":
        print("usage: python -m core.telemetry summarize [events.jsonl]", file=sys.stderr)
        return 2
    path = Path(argv[1]) if len(argv) > 1 else None
    print(json.dumps(summarize(path), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
