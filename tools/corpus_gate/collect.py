"""Measure one revision of LossLift over the local corpus -- privately.

The corpus gate runs this file as a standalone script inside an isolated
worktree of the revision under test::

    python -P collect.py --root WORKTREE --documents FILE --out FILE  < salt

It imports that worktree's ``core`` and nothing from the checkout it was
started from; it proves so before measuring anything. It writes one JSON line
per document, then a line saying it finished. A file without that last line is
incomplete, and the gate treats it as a failure rather than as a shorter
corpus.

Nothing written here may carry text from a document. Every value is one of:

* an integer, a boolean or null, or a list or mapping of those;
* a string matching the strict shape of an enumeration -- a rule id such as
  ``R-22``, a status such as ``NEEDS_REVIEW`` -- checked against a pattern
  before it is written, and digested instead when it does not match;
* a keyed digest: HMAC-SHA256 under the corpus's own secret salt, which lives
  only in the local manifest. A digest says whether a value changed between
  two revisions. It never says what the value was, and without the salt it
  cannot be reversed even for a short value such as one printed total.

Claimant names, claim numbers, descriptions, amounts, dates, finding messages
and warning text are therefore only ever digested. So is anything the
pipeline says in words, because a message can quote the document it is about.
Exceptions are recorded by type name alone: a message can quote a cell.

This file must stay self-contained -- standard library only, and ``core`` from
the revision -- because it is executed against revisions that predate it.
"""

from __future__ import annotations

import argparse
import dataclasses
import enum
import hashlib
import hmac
import json
import platform
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

SCHEMA = 1

#: Shapes a raw string may take in the output. Anything else is digested.
RULE_ID = re.compile(r"R-\d{1,3}[a-z]?")
UPPER_TOKEN = re.compile(r"[A-Z][A-Z_]{1,23}")
LOWER_TOKEN = re.compile(r"[a-z][a-z_]{1,23}")
FIELD_NAME = re.compile(r"[a-z][a-z0-9_]{0,47}")
ERROR_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")

DIGEST_PREFIX = "h:"

#: Document-level fields compared by digest. Absent in an older revision is a
#: value of its own, so a field appearing or disappearing is seen as a change.
METADATA_FIELDS = (
    "carrier",
    "named_insured",
    "policy_number",
    "policy_period_start",
    "policy_period_end",
    "line_of_business",
    "valuation_date",
    "currency",
    "locale_hint",
    "locale_confident",
    "date_order",
    "date_order_confident",
    "recovery_convention",
    "currencies_seen",
    "document_issues",
    "policy_periods",
    "column_mapping",
    "profile_fingerprint",
    "profile_name",
)

PAGE_SETS = (
    "processed_pages",
    "failed_pages",
    "skipped_pages",
    "unresolved_pages",
    "scanned_pages",
)


class Digest:
    """Keyed digests under the corpus salt."""

    def __init__(self, salt: bytes) -> None:
        if len(salt) < 16:
            raise ValueError("the digest salt is too short")
        self._salt = salt

    def __call__(self, value: Any) -> str:
        text = json.dumps(
            plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        mac = hmac.new(self._salt, text.encode("utf-8"), hashlib.sha256)
        return DIGEST_PREFIX + mac.hexdigest()[:32]


def plain(value: Any) -> Any:
    """``value`` as JSON-native data, the same way in every process.

    Enumerations first: the schema's are ``str`` enums, which pass for plain
    strings but format as ``Class.MEMBER`` -- and how they format has changed
    between Python versions.
    """
    if isinstance(value, enum.Enum):
        return plain(value.value)
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, str):
        return str.__str__(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, dict):
        return {str(plain(key)): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(
            (plain(item) for item in value),
            key=lambda item: json.dumps(item, sort_keys=True),
        )
    if hasattr(value, "model_dump"):
        return plain(value.model_dump(mode="json"))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return plain(dataclasses.asdict(value))
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "__dict__") and not isinstance(value, type):
        return plain(vars(value))
    return str(value)


def token(value: Any, pattern: re.Pattern[str], digest: Digest) -> str:
    """An enumeration value as itself when it has the expected shape."""
    text = plain(value)
    if isinstance(text, str) and pattern.fullmatch(text):
        return text
    return digest(text)


def field_key(name: Any, digest: Digest) -> str:
    """A schema field name, safe to use as a key; otherwise its digest."""
    if isinstance(name, str) and FIELD_NAME.fullmatch(name):
        return name
    return digest(name)


def count(value: Any, digest: Digest) -> Any:
    """An integer or null as itself; anything else digested."""
    if value is None or (isinstance(value, int) and not isinstance(value, bool)):
        return value
    return digest(value)


def flag(value: Any) -> bool | None:
    return None if value is None else bool(value)


def error_name(error: BaseException) -> str:
    name = type(error).__name__
    return name if ERROR_NAME.fullmatch(name) else "UnnamedError"


# --------------------------------------------------------------------------
# What is measured
# --------------------------------------------------------------------------


def _pages(document: Any, digest: Digest) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in PAGE_SETS:
        values = getattr(document, name, None)
        out[name] = None if values is None else sorted(int(page) for page in values)
    reasons = getattr(document, "unresolved_reasons", None)
    out["unresolved_reasons"] = (
        None
        if reasons is None
        else {
            str(int(page)): digest(reason)
            for page, reason in sorted(reasons.items(), key=lambda item: int(item[0]))
        }
    )
    out["page_count"] = count(getattr(document, "page_count", None), digest)
    splits = getattr(document, "column_split_pages", None)
    out["column_split_pages"] = (
        None if splits is None else [[int(page), int(split)] for page, split in splits]
    )
    rows = getattr(document, "rows_seen_per_page", None)
    out["rows_seen_per_page"] = (
        None
        if rows is None
        else {str(int(page)): int(seen) for page, seen in sorted(rows.items())}
    )
    return out


def _unplaced(document: Any, digest: Digest) -> dict[str, Any] | None:
    rows = getattr(document, "unplaced_rows", None)
    if rows is None:
        return None
    by_page = Counter(int(getattr(row, "page", 0) or 0) for row in rows)
    return {
        "count": len(rows),
        "by_page": {str(page): seen for page, seen in sorted(by_page.items())},
        "digest": digest(list(rows)),
    }


def _printed(document: Any, digest: Digest) -> dict[str, Any]:
    totals = getattr(document, "printed_totals", None) or {}
    unreadable = getattr(document, "unreadable_totals", None)
    evidence = getattr(document, "printed_count_evidence", None)
    sections = getattr(document, "printed_sections", None)
    return {
        "claim_count": count(getattr(document, "printed_claim_count", None), digest),
        "totals": {
            field_key(name, digest): digest(value)
            for name, value in sorted(totals.items(), key=lambda item: str(item[0]))
        },
        "unreadable_totals": (
            None
            if unreadable is None
            else {"count": len(unreadable), "digest": digest(unreadable)}
        ),
        "unreadable_totals_page": count(
            getattr(document, "unreadable_totals_page", None), digest
        ),
        "count_evidence": (
            None
            if evidence is None
            else {"count": len(evidence), "digest": digest(evidence)}
        ),
        "sections": (
            None
            if sections is None
            else {
                "count": len(sections),
                "claim_counts": [
                    count(getattr(section, "printed_claim_count", None), digest)
                    for section in sections
                ],
                "digest": digest(list(sections)),
            }
        ),
    }


_IDENTITY = ("rule_id", "scope", "subject", "condition", "field", "page", "claim_number", "related_rows")
_DETAIL = ("severity", "category", "message", "expected", "actual", "delta")


def _findings(reconciliation: Any, digest: Digest) -> dict[str, Any]:
    findings = list(getattr(reconciliation, "findings", None) or [])
    by_rule: Counter[str] = Counter()
    by_severity: Counter[str] = Counter()
    by_category: Counter[str] = Counter()
    by_scope: Counter[str] = Counter()
    identity: dict[str, list[str]] = defaultdict(list)
    detail: dict[str, list[str]] = defaultdict(list)
    for finding in findings:
        rule = token(getattr(finding, "rule_id", None), RULE_ID, digest)
        severity = token(getattr(finding, "severity", None), UPPER_TOKEN, digest)
        category = token(getattr(finding, "category", None), LOWER_TOKEN, digest)
        scope = token(getattr(finding, "scope", None), LOWER_TOKEN, digest)
        by_rule[rule] += 1
        by_severity[f"{rule}/{severity}"] += 1
        by_category[f"{rule}/{category}"] += 1
        by_scope[f"{rule}/{scope}"] += 1
        # Which things were flagged, and what was said about them, apart:
        # the same number of findings on different claims is a change too.
        identity[rule].append(digest([getattr(finding, name, None) for name in _IDENTITY]))
        detail[rule].append(digest([getattr(finding, name, None) for name in _DETAIL]))
    return {
        "total": len(findings),
        "by_rule": dict(sorted(by_rule.items())),
        "by_rule_severity": dict(sorted(by_severity.items())),
        "by_rule_category": dict(sorted(by_category.items())),
        "by_rule_scope": dict(sorted(by_scope.items())),
        "identity": {rule: digest(sorted(items)) for rule, items in sorted(identity.items())},
        "detail": {rule: digest(sorted(items)) for rule, items in sorted(detail.items())},
    }


def _summary(document: Any, digest: Digest) -> dict[str, Any] | None:
    try:
        from core.summary import summarise_by_period
    except ImportError:
        return None
    periods = list(summarise_by_period(document))
    claims = [count(getattr(period, "claims", None), digest) for period in periods]
    return {
        "periods": len(periods),
        "claim_counts": claims,
        "total_claims": sum(value for value in claims if isinstance(value, int)),
        "open_claims": [count(getattr(period, "open_claims", None), digest) for period in periods],
        "closed_claims": [count(getattr(period, "closed_claims", None), digest) for period in periods],
        "printed_claims": [count(getattr(period, "printed_claims", None), digest) for period in periods],
        "ties": [flag(period.ties()) if hasattr(period, "ties") else None for period in periods],
        "digest": digest(periods),
    }


def _claims(claims: list[Any], digest: Digest) -> dict[str, Any]:
    rows = [plain(claim) for claim in claims]
    names = sorted({name for row in rows if isinstance(row, dict) for name in row})
    columns = {
        name: [row.get(name) if isinstance(row, dict) else None for row in rows]
        for name in names
    }
    return {
        "digest": digest(rows),
        "fields": {field_key(name, digest): digest(values) for name, values in columns.items()},
        "null_counts": {
            field_key(name, digest): sum(1 for value in values if value is None)
            for name, values in columns.items()
        },
    }


def _metadata(document: Any, digest: Digest) -> dict[str, Any]:
    return {
        name: digest(getattr(document, name)) if hasattr(document, name) else "absent"
        for name in METADATA_FIELDS
    }


def _warnings(result: Any, digest: Digest) -> dict[str, Any] | None:
    warnings = getattr(result, "warnings", None)
    if warnings is None:
        return None
    return {"count": len(warnings), "digest": digest(list(warnings))}


def measure(result: Any, digest: Digest) -> dict[str, Any]:
    """Every privacy-safe measurement the gate compares, for one document.

    Each group is measured on its own. A group the revision cannot produce --
    an attribute an older revision lacks -- is recorded by the name of the
    exception that stopped it, which the comparison then sees as a value like
    any other.
    """
    document = getattr(result, "document", None)
    reconciliation = getattr(result, "reconciliation", None)
    claims = list(getattr(document, "claims", None) or [])
    groups: dict[str, Callable[[], Any]] = {
        "claim_count": lambda: len(claims),
        "status": lambda: token(getattr(reconciliation, "status"), UPPER_TOKEN, digest),
        "extraction_method": lambda: token(
            getattr(document, "extraction_method"), LOWER_TOKEN, digest
        ),
        "pages": lambda: _pages(document, digest),
        "unplaced": lambda: _unplaced(document, digest),
        "printed": lambda: _printed(document, digest),
        "findings": lambda: _findings(reconciliation, digest),
        "summary": lambda: _summary(document, digest),
        "claims": lambda: _claims(claims, digest),
        "metadata": lambda: _metadata(document, digest),
        "warnings": lambda: _warnings(result, digest),
    }
    out: dict[str, Any] = {}
    for name, measure_group in groups.items():
        try:
            out[name] = measure_group()
        except Exception as error:  # noqa: BLE001 - recorded, never raised
            out[name] = {"unmeasured": error_name(error)}
    return out


# --------------------------------------------------------------------------
# Running it
# --------------------------------------------------------------------------


def _write(handle: Any, record: dict[str, Any]) -> None:
    handle.write(json.dumps(record, sort_keys=True, ensure_ascii=True) + "\n")
    handle.flush()


def _isolated(root: Path) -> bool:
    """Whether ``core`` comes from the revision under test and nowhere else."""
    import core

    location = Path(getattr(core, "__file__", "") or "").resolve()
    return location.is_relative_to(root.resolve())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--documents", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    salt = bytes.fromhex(sys.stdin.readline().strip())
    digest = Digest(salt)
    documents = json.loads(args.documents.read_text(encoding="utf-8"))

    with args.out.open("w", encoding="utf-8") as handle:
        try:
            if not _isolated(args.root):
                _write(handle, {"kind": "fatal", "error_type": "IsolationError"})
                return 3
            from core.pipeline import run_pipeline
        except Exception as error:  # noqa: BLE001 - the revision cannot start
            _write(handle, {"kind": "fatal", "error_type": error_name(error)})
            return 3

        _write(
            handle,
            {
                "kind": "header",
                "schema": SCHEMA,
                "isolated": True,
                "python": platform.python_version(),
                "platform": sys.platform,
            },
        )
        for entry in documents:
            started = time.perf_counter()
            record: dict[str, Any] = {"kind": "document", "id": entry["id"]}
            try:
                result = run_pipeline(Path(entry["path"]), use_vision=False)
            except Exception as error:  # noqa: BLE001 - recorded as a failure
                record.update(ok=False, error_type=error_name(error))
            else:
                record.update(ok=True, metrics=measure(result, digest))
            record["seconds"] = round(time.perf_counter() - started, 3)
            _write(handle, record)
        _write(handle, {"kind": "complete", "documents": len(documents)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
