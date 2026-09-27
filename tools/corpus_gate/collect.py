"""Measure one revision of LossLift over the local corpus -- privately.

The corpus gate runs this file as a standalone script inside an isolated
worktree of the revision under test::

    python -P collect.py --root WORKTREE --documents FILE --out FILE  < salt

The gate itself always sends ``raw`` instead of a salt. The revision's code
runs in this process, so nothing here may hold the salt: in raw mode every
digest slot carries the canonical text it would have digested, marked
``r:`` and base64url-encoded, and the gate -- a separate, trusted process --
checks the whole output against a strict schema and keys each one itself
(``seal.py``). A salt on stdin still works, for measuring by hand.

It imports that worktree's ``core`` and nothing from the checkout it was
started from; it proves so before measuring anything. It writes one JSON line
per document, then a line saying it finished. A file without that last line is
incomplete, and the gate treats it as a failure rather than as a shorter
corpus. A document is measured completely or not at all: if any measurement
raises, the document is recorded as unmeasured, which fails the gate like a
crash does.

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
import base64
import dataclasses
import enum
import hashlib
import hmac
import json
import platform
import re
import sys
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
RUN_ID = re.compile(r"run-[1-9][0-9]{0,3}")

DIGEST_PREFIX = "h:"
RAW_PREFIX = "r:"
RAW_MODE = "raw"

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


class RawDigest:
    """A digest slot's canonical text, for the gate to key outside this process.

    Holds no secret: what it writes is exactly the bytes ``Digest`` would have
    keyed, so the gate's digest of it is identical to the one made here with
    the salt.
    """

    def __call__(self, value: Any) -> str:
        text = json.dumps(
            plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        encoded = base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")
        return RAW_PREFIX + encoded.rstrip("=")


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


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


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


class Unmeasured(Exception):
    """A measurement group raised, so the document was not measured."""

    def __init__(self, group: str, error_type: str) -> None:
        super().__init__(group, error_type)
        self.group = group
        self.error_type = error_type


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


#: ``run_id`` is part of a finding's identity: the same rule on the same claim
#: in two logical runs of a packet is two findings. A revision without runs
#: reads None for every finding, the same on both sides of a comparison.
_IDENTITY = ("rule_id", "scope", "subject", "condition", "field", "page", "claim_number",
             "related_rows", "run_id")
_DETAIL = ("severity", "category", "message", "expected", "actual", "delta")


def _findings(reconciliation: Any, digest: Digest) -> dict[str, Any]:
    findings = list(getattr(reconciliation, "findings", None) or [])
    by_rule: Counter[str] = Counter()
    by_severity: Counter[str] = Counter()
    by_category: Counter[str] = Counter()
    by_scope: Counter[str] = Counter()
    identity: dict[str, list[Any]] = defaultdict(list)
    detail: dict[str, list[Any]] = defaultdict(list)
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
        # One digest per rule over every finding's plain values, in a fixed
        # order: a digest is never nested inside another, so the gate can key
        # it outside this process and get the same answer.
        identity[rule].append(plain([getattr(finding, name, None) for name in _IDENTITY]))
        detail[rule].append(plain([getattr(finding, name, None) for name in _DETAIL]))
    return {
        "total": len(findings),
        "by_rule": dict(sorted(by_rule.items())),
        "by_rule_severity": dict(sorted(by_severity.items())),
        "by_rule_category": dict(sorted(by_category.items())),
        "by_rule_scope": dict(sorted(by_scope.items())),
        "identity": {rule: digest(sorted(items, key=_canonical)) for rule, items in sorted(identity.items())},
        "detail": {rule: digest(sorted(items, key=_canonical)) for rule, items in sorted(detail.items())},
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


# -- trust status and logical runs -----------------------------------------
#
# A revision that predates logical runs, one whose document has ``runs=[]``,
# and one whose single report is a whole-document run all measure alike: one
# run, not a packet, no items. Refused rows a revision does not record are
# measured as none. Only a change in what a document is or holds shows up --
# never a change in how a revision represents the same thing.


#: ``core.review.UNACCOUNTED_RULES``, mirrored for a revision that predates it.
_UNACCOUNTED_RULES = frozenset({"R-19", "R-20", "R-22", "R-23", "R-28", "R-29"})


def _blocks_trust(finding: Any) -> bool:
    """``core.review.blocks_trust``, for a revision that predates it."""
    return (plain(getattr(finding, "category", None)) != "underwriting"
            or plain(getattr(finding, "severity", None)) == "ERROR"
            or getattr(finding, "rule_id", None) in _UNACCOUNTED_RULES)


def _canonical_status(result: Any, reconciliation: Any) -> Any:
    """The one status policy (``core.review.canonical_status``).

    A revision that predates the function is measured under the same policy,
    so the comparison shows where a document's trust changed, not where the
    code learnt to state it.
    """
    needs_mapping = bool(getattr(result, "needs_mapping", False))
    try:
        from core.review import canonical_status
    except ImportError:
        canonical_status = None
    if canonical_status is not None:
        return canonical_status(reconciliation, needs_mapping=needs_mapping)
    if reconciliation is None or needs_mapping:
        return "NEEDS_REVIEW"
    if plain(getattr(reconciliation, "status", None)) != "CLEAN":
        return "NEEDS_REVIEW"
    run_status = getattr(reconciliation, "run_status", None) or {}
    if any(plain(status) != "CLEAN" for status in run_status.values()):
        return "NEEDS_REVIEW"
    if any(_blocks_trust(f) for f in getattr(reconciliation, "findings", None) or []):
        return "NEEDS_REVIEW"
    return "CLEAN"


def _canonical_run_status(reconciliation: Any, run_id: Any) -> Any:
    try:
        from core.review import canonical_run_status
    except ImportError:
        canonical_run_status = None
    if canonical_run_status is not None:
        return canonical_run_status(reconciliation, run_id)
    if reconciliation is None:
        return "NEEDS_REVIEW"
    run_status = getattr(reconciliation, "run_status", None) or {}
    if plain(run_status.get(run_id)) != "CLEAN":
        return "NEEDS_REVIEW"
    if any(_blocks_trust(f) for f in getattr(reconciliation, "findings", None) or []
           if getattr(f, "run_id", None) in (None, run_id)):
        return "NEEDS_REVIEW"
    return "CLEAN"


def _on(pages: set[int], item: Any) -> bool:
    return int(getattr(item, "page", 0) or 0) in pages


def _refused(document: Any, digest: Digest) -> dict[str, Any]:
    rows = list(getattr(document, "refused_claim_rows", None) or [])
    by_page = Counter(int(getattr(row, "page", 0) or 0) for row in rows)
    return {
        "count": len(rows),
        "reported": sum(1 for row in rows if getattr(row, "report", False)),
        "by_page": {str(page): seen for page, seen in sorted(by_page.items())},
        "digest": digest(rows),
    }


_RUN_FACTS = ("carrier", "named_insured", "policy_number", "policy_period_start",
              "policy_period_end", "line_of_business", "valuation_date")


def _run_item(document: Any, reconciliation: Any, run: Any, digest: Digest) -> dict[str, Any]:
    pages = {int(page) for page in getattr(run, "pages", None) or []}
    run_id = getattr(run, "run_id", None)
    claims = [claim for claim in getattr(document, "claims", None) or []
              if int(getattr(claim, "source_page", 0) or 0) in pages]
    findings = [f for f in getattr(reconciliation, "findings", None) or []
                if getattr(f, "run_id", None) == run_id]
    engine = (getattr(reconciliation, "run_status", None) or {}).get(run_id)
    printed = getattr(run, "printed_totals", None) or {}
    return {
        "run_id": token(run_id, RUN_ID, digest),
        "pages": sorted(pages),
        "confidence": token(getattr(run, "confidence", None), LOWER_TOKEN, digest),
        "ambiguous": bool(getattr(run, "ambiguous", False)),
        "incomplete": bool(getattr(run, "incomplete", None)),
        "claims": len(claims),
        "claims_digest": digest(claims),
        "refused": sum(1 for row in getattr(document, "refused_claim_rows", None) or []
                       if _on(pages, row)),
        "unplaced": sum(1 for row in getattr(document, "unplaced_rows", None) or []
                        if _on(pages, row)),
        "printed_claim_count": count(getattr(run, "printed_claim_count", None), digest),
        "printed_totals": sorted(field_key(name, digest)
                                 for name, value in printed.items() if value is not None),
        "engine_status": None if engine is None else token(engine, UPPER_TOKEN, digest),
        "status": token(_canonical_run_status(reconciliation, run_id), UPPER_TOKEN, digest),
        "r04": sum(1 for f in findings if getattr(f, "rule_id", None) == "R-04"),
        "r05": sum(1 for f in findings if getattr(f, "rule_id", None) == "R-05"),
        "evidence": digest(list(getattr(run, "evidence", None) or [])),
        "facts": digest([getattr(run, name, None) for name in _RUN_FACTS]),
    }


def _runs(document: Any, reconciliation: Any, digest: Digest) -> dict[str, Any]:
    runs = list(getattr(document, "runs", None) or [])
    packet = len(runs) > 1
    return {
        "count": max(len(runs), 1),
        "packet": packet,
        "unsettled": sum(1 for run in runs if getattr(run, "ambiguous", False)),
        "incomplete": sum(1 for run in runs if getattr(run, "incomplete", None)),
        "items": {
            str(index): _run_item(document, reconciliation, run, digest)
            for index, run in enumerate(runs, start=1)
        } if packet else {},
    }


def measure(result: Any, digest: Digest) -> dict[str, Any]:
    """Every privacy-safe measurement the gate compares, for one document.

    Every group is measured, or the document is not: a group that raises stops
    the measurement with :class:`Unmeasured`, naming the group and the type of
    the exception. An exception is never recorded as a value, because two
    revisions failing the same way have measured nothing, not the same thing.
    Attributes an older revision may lack are read with a default and
    recorded as absent; their absence does not raise.
    """
    document = getattr(result, "document", None)
    reconciliation = getattr(result, "reconciliation", None)
    claims: list[Any] = []
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
        "review_status": lambda: token(
            _canonical_status(result, reconciliation), UPPER_TOKEN, digest
        ),
        "runs": lambda: _runs(document, reconciliation, digest),
        "refused": lambda: _refused(document, digest),
    }
    try:
        claims.extend(getattr(document, "claims", None) or [])
    except Exception as error:  # noqa: BLE001 - the document is unmeasured
        raise Unmeasured("claims", error_name(error)) from None
    out: dict[str, Any] = {}
    for name, measure_group in groups.items():
        try:
            out[name] = measure_group()
        except Exception as error:  # noqa: BLE001 - the document is unmeasured
            raise Unmeasured(name, error_name(error)) from None
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
    parser.add_argument(
        "--vision-replay", type=Path, default=None,
        help="replay recorded vision answers from this directory instead of "
             "skipping scanned pages; never calls a live model",
    )
    args = parser.parse_args(argv)

    first = sys.stdin.readline().strip()
    digest: Any = RawDigest() if first == RAW_MODE else Digest(bytes.fromhex(first))
    del first
    documents = json.loads(args.documents.read_text(encoding="utf-8"))

    with args.out.open("w", encoding="utf-8") as handle:
        try:
            if not _isolated(args.root):
                _write(handle, {"kind": "fatal", "error_type": "IsolationError"})
                return 3
            from core.pipeline import run_pipeline

            vision: dict[str, Any] = {"use_vision": False}
            if args.vision_replay is not None:
                try:
                    from core.extract_vision import replay_extractor
                except ImportError:
                    # A revision that cannot replay would be measured without
                    # its scanned pages while the other is measured with them.
                    raise NotImplementedError("vision replay") from None
                vision = {"use_vision": True,
                          "vision_extractor": replay_extractor(args.vision_replay)}
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
            record: dict[str, Any] = {"kind": "document", "id": entry["id"]}
            try:
                result = run_pipeline(Path(entry["path"]), **vision)
            except Exception as error:  # noqa: BLE001 - recorded as a failure
                record.update(ok=False, error_type=error_name(error))
            else:
                try:
                    record.update(ok=True, metrics=measure(result, digest))
                except Unmeasured as failure:
                    record.update(
                        ok=False, error_type=failure.error_type, unmeasured=failure.group
                    )
            _write(handle, record)
        _write(handle, {"kind": "complete", "documents": len(documents)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
