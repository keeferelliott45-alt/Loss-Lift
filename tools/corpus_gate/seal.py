"""The trust boundary between a revision's collector and the gate.

A collector runs the revision's own code in its own process, so everything it
writes is candidate-controlled. That process never holds the salt: it writes
each digest slot as the canonical text to be digested (``r:`` + base64url),
and this module -- running in the gate's process, which holds the salt and
runs no revision code -- reads the output under a strict, allowlisted schema:

* exact JSON only: no NaN or Infinity, no fractions, integers of at most
  seven digits, no duplicate keys, bounded line and file size, bounded depth;
* every object has exactly the keys the collector writes, and nothing else;
* every integer sits in its field's range, every list and map within its
  cardinality limit, every boolean is a real boolean;
* strings are allowed only where the collector writes them: enumeration
  tokens of a fixed shape, schema field names, page numbers as keys, and
  ``r:`` slots, which are keyed here and become ``h:`` digests. A ready-made
  ``h:`` from a collector is refused: it would be 128 bits of free text;
* an exception is named only if it is a Python builtin; anything else is
  ``UnlistedError``, because a revision chooses its own class names.

Any violation rejects the whole run, which then fails with exit 4.

What leaves the runner is smaller still: ``public_result`` keeps, per
document, its state, *which* schema fields changed (map keys collapsed to
``*``), and the before and after of only the claim count and the status.
No other measured value, no timing and no raw record is published. See
``docs/cloud-corpus-gate.md`` for the bound this leaves.
"""

from __future__ import annotations

import base64
import builtins
import os
import stat
import hashlib
import hmac
import json
import re
from pathlib import Path
from typing import Any, Callable

from tools.corpus_gate import collect
from tools.corpus_gate import compare as comparison
from tools.corpus_gate.manifest import DOCUMENT_ID, Manifest

MAX_INT = 1_000_000
MAX_PAGE = 20_000
MAX_LIST = 20_000
MAX_NAMED_KEYS = 256
MAX_PERIODS = 1_000
MAX_RAW_TEXT = 64 << 20
MAX_LINE = 256 << 20
MAX_FILE = 1 << 30
MAX_DIGITS = 7

RAW = re.compile(r"r:[A-Za-z0-9_-]*")
PAGE_KEY = re.compile(r"0|[1-9][0-9]{0,4}")
GROUPS = (
    "claim_count", "status", "extraction_method", "pages", "unplaced", "printed",
    "findings", "summary", "claims", "metadata", "warnings",
)
BUILTIN_ERRORS = frozenset(
    name for name in dir(builtins)
    if isinstance(getattr(builtins, name), type) and issubclass(getattr(builtins, name), BaseException)
) | {"IsolationError", "UnnamedError"}
UNLISTED_ERROR = "UnlistedError"
REJECTED = "wrote output outside the allowed schema"
STATUS_VOCAB = frozenset({"CLEAN", "NEEDS_REVIEW"})
MAP_PATHS = (
    "pages.unresolved_reasons", "pages.rows_seen_per_page", "unplaced.by_page",
    "printed.totals", "findings.by_rule", "findings.by_rule_severity",
    "findings.by_rule_category", "findings.by_rule_scope", "findings.identity",
    "findings.detail", "claims.fields", "claims.null_counts",
)


class Rejected(Exception):
    """The collector's output is outside the schema."""


def error_name(value: Any) -> str:
    if isinstance(value, str) and value in BUILTIN_ERRORS:
        return value
    return UNLISTED_ERROR


# --------------------------------------------------------------------------
# Strict JSON
# --------------------------------------------------------------------------


def _no_constant(name: str) -> Any:
    raise Rejected("a non-finite number")


def _no_fraction(text: str) -> Any:
    raise Rejected("a fraction")


def _bounded_int(text: str) -> int:
    if len(text.lstrip("-")) > MAX_DIGITS:
        raise Rejected("an integer out of range")
    return int(text)


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise Rejected("a repeated key")
    return dict(pairs)


def strict_loads(line: str) -> Any:
    if len(line) > MAX_LINE:
        raise Rejected("a line too long")
    try:
        return json.loads(
            line,
            parse_constant=_no_constant,
            parse_float=_no_fraction,
            parse_int=_bounded_int,
            object_pairs_hook=_unique,
        )
    except RecursionError:
        raise Rejected("nesting too deep") from None


# --------------------------------------------------------------------------
# The schema
# --------------------------------------------------------------------------


class Sealer:
    """Validates one collector's measurements and keys their digest slots."""

    def __init__(self, salt: bytes) -> None:
        self._salt = salt

    # -- leaves ------------------------------------------------------------

    def digest(self, value: Any) -> str:
        if not isinstance(value, str) or not RAW.fullmatch(value):
            raise Rejected("a digest slot without canonical text")
        body = value[2:]
        if len(body) * 3 // 4 > MAX_RAW_TEXT:
            raise Rejected("a digest slot too long")
        try:
            data = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        except (ValueError, TypeError):
            raise Rejected("a digest slot that is not base64url") from None
        mac = hmac.new(self._salt, data, hashlib.sha256)
        return collect.DIGEST_PREFIX + mac.hexdigest()[:32]

    @staticmethod
    def integer(value: Any, low: int = 0, high: int = MAX_INT) -> int:
        if type(value) is not int or not low <= value <= high:
            raise Rejected("an integer of the wrong type or range")
        return value

    @staticmethod
    def boolean(value: Any) -> bool:
        if type(value) is not bool:
            raise Rejected("a boolean of the wrong type")
        return value

    def countish(self, value: Any) -> Any:
        """``collect.count``: an integer, null, or a digest."""
        if value is None:
            return None
        if isinstance(value, str):
            return self.digest(value)
        return self.integer(value)

    def token(self, value: Any, pattern: re.Pattern[str]) -> str:
        """``collect.token``: a token of the expected shape, or a digest."""
        if isinstance(value, str) and pattern.fullmatch(value):
            return value
        return self.digest(value)

    def name_key(self, key: str) -> str:
        """``collect.field_key``: a schema field name, or a digest."""
        return key if collect.FIELD_NAME.fullmatch(key) else self.digest(key)

    def rule_key(self, key: str) -> str:
        return self.token(key, collect.RULE_ID)

    def pair_key(self, pattern: re.Pattern[str]) -> Callable[[str], str]:
        def key(value: str) -> str:
            parts = value.split("/")
            if len(parts) != 2:
                raise Rejected("a composite key of the wrong shape")
            return f"{self.rule_key(parts[0])}/{self.token(parts[1], pattern)}"

        return key

    @staticmethod
    def page_key(key: str) -> str:
        if not PAGE_KEY.fullmatch(key) or int(key) > MAX_PAGE:
            raise Rejected("a page key out of range")
        return key

    # -- containers --------------------------------------------------------

    @staticmethod
    def exact(value: Any, keys: tuple[str, ...]) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != set(keys):
            raise Rejected("an object with missing or unknown keys")
        return value

    @staticmethod
    def listed(value: Any, limit: int, item: Callable[[Any], Any]) -> list[Any]:
        if not isinstance(value, list) or len(value) > limit:
            raise Rejected("a list of the wrong type or length")
        return [item(element) for element in value]

    @staticmethod
    def mapped(value: Any, limit: int, key: Callable[[str], str], item: Callable[[Any], Any]) -> dict[str, Any]:
        if not isinstance(value, dict) or len(value) > limit:
            raise Rejected("a map of the wrong type or size")
        out: dict[str, Any] = {}
        for name, element in value.items():
            sealed = key(name)
            if sealed in out:
                raise Rejected("two keys that seal to one")
            out[sealed] = item(element)
        return out

    def nullable(self, value: Any, item: Callable[[Any], Any]) -> Any:
        return None if value is None else item(value)

    def counted(self, value: Any) -> dict[str, Any]:
        value = self.exact(value, ("count", "digest"))
        return {"count": self.integer(value["count"]), "digest": self.digest(value["digest"])}

    def pages_list(self, value: Any) -> list[int]:
        pages = self.listed(value, MAX_LIST, lambda page: self.integer(page, 0, MAX_PAGE))
        if pages != sorted(pages):
            raise Rejected("a page list out of order")
        return pages

    # -- groups ------------------------------------------------------------

    def pages(self, value: Any) -> dict[str, Any]:
        keys = (*collect.PAGE_SETS, "unresolved_reasons", "page_count",
                "column_split_pages", "rows_seen_per_page")
        value = self.exact(value, keys)
        out: dict[str, Any] = {
            name: self.nullable(value[name], self.pages_list) for name in collect.PAGE_SETS
        }
        out["unresolved_reasons"] = self.nullable(
            value["unresolved_reasons"],
            lambda m: self.mapped(m, MAX_LIST, self.page_key, self.digest),
        )
        out["page_count"] = self.countish(value["page_count"])

        def split(pair: Any) -> list[int]:
            pair = self.listed(pair, 2, self.integer)
            if len(pair) != 2 or pair[0] > MAX_PAGE:
                raise Rejected("a column split of the wrong shape")
            return pair

        out["column_split_pages"] = self.nullable(
            value["column_split_pages"], lambda v: self.listed(v, MAX_LIST, split)
        )
        out["rows_seen_per_page"] = self.nullable(
            value["rows_seen_per_page"],
            lambda m: self.mapped(m, MAX_LIST, self.page_key, self.integer),
        )
        return out

    def unplaced(self, value: Any) -> dict[str, Any]:
        value = self.exact(value, ("count", "by_page", "digest"))
        return {
            "count": self.integer(value["count"]),
            "by_page": self.mapped(value["by_page"], MAX_LIST, self.page_key, self.integer),
            "digest": self.digest(value["digest"]),
        }

    def printed(self, value: Any) -> dict[str, Any]:
        value = self.exact(value, ("claim_count", "totals", "unreadable_totals",
                                   "unreadable_totals_page", "count_evidence", "sections"))

        def sections(v: Any) -> dict[str, Any]:
            v = self.exact(v, ("count", "claim_counts", "digest"))
            return {
                "count": self.integer(v["count"]),
                "claim_counts": self.listed(v["claim_counts"], MAX_LIST, self.countish),
                "digest": self.digest(v["digest"]),
            }

        return {
            "claim_count": self.countish(value["claim_count"]),
            "totals": self.mapped(value["totals"], MAX_NAMED_KEYS, self.name_key, self.digest),
            "unreadable_totals": self.nullable(value["unreadable_totals"], self.counted),
            "unreadable_totals_page": self.countish(value["unreadable_totals_page"]),
            "count_evidence": self.nullable(value["count_evidence"], self.counted),
            "sections": self.nullable(value["sections"], sections),
        }

    def findings(self, value: Any) -> dict[str, Any]:
        value = self.exact(value, ("total", "by_rule", "by_rule_severity", "by_rule_category",
                                   "by_rule_scope", "identity", "detail"))
        n = MAX_NAMED_KEYS
        return {
            "total": self.integer(value["total"]),
            "by_rule": self.mapped(value["by_rule"], n, self.rule_key, self.integer),
            "by_rule_severity": self.mapped(
                value["by_rule_severity"], n, self.pair_key(collect.UPPER_TOKEN), self.integer),
            "by_rule_category": self.mapped(
                value["by_rule_category"], n, self.pair_key(collect.LOWER_TOKEN), self.integer),
            "by_rule_scope": self.mapped(
                value["by_rule_scope"], n, self.pair_key(collect.LOWER_TOKEN), self.integer),
            "identity": self.mapped(value["identity"], n, self.rule_key, self.digest),
            "detail": self.mapped(value["detail"], n, self.rule_key, self.digest),
        }

    def summary(self, value: Any) -> dict[str, Any]:
        value = self.exact(value, ("periods", "claim_counts", "total_claims", "open_claims",
                                   "closed_claims", "printed_claims", "ties", "digest"))
        periods = self.integer(value["periods"], 0, MAX_PERIODS)
        out: dict[str, Any] = {"periods": periods, "total_claims": self.integer(value["total_claims"])}
        for name in ("claim_counts", "open_claims", "closed_claims", "printed_claims"):
            out[name] = self.listed(value[name], periods, self.countish)
        out["ties"] = self.listed(value["ties"], periods, lambda v: self.nullable(v, self.boolean))
        out["digest"] = self.digest(value["digest"])
        return out

    def claims(self, value: Any) -> dict[str, Any]:
        value = self.exact(value, ("digest", "fields", "null_counts"))
        return {
            "digest": self.digest(value["digest"]),
            "fields": self.mapped(value["fields"], MAX_NAMED_KEYS, self.name_key, self.digest),
            "null_counts": self.mapped(
                value["null_counts"], MAX_NAMED_KEYS, self.name_key, self.integer),
        }

    def metadata(self, value: Any) -> dict[str, Any]:
        value = self.exact(value, collect.METADATA_FIELDS)
        return {
            name: "absent" if value[name] == "absent" else self.digest(value[name])
            for name in collect.METADATA_FIELDS
        }

    def metrics(self, value: Any) -> dict[str, Any]:
        value = self.exact(value, GROUPS)
        return {
            "claim_count": self.integer(value["claim_count"]),
            "status": self.token(value["status"], collect.UPPER_TOKEN),
            "extraction_method": self.token(value["extraction_method"], collect.LOWER_TOKEN),
            "pages": self.pages(value["pages"]),
            "unplaced": self.nullable(value["unplaced"], self.unplaced),
            "printed": self.printed(value["printed"]),
            "findings": self.findings(value["findings"]),
            "summary": self.nullable(value["summary"], self.summary),
            "claims": self.claims(value["claims"]),
            "metadata": self.metadata(value["metadata"]),
            "warnings": self.nullable(value["warnings"], self.counted),
        }

    def document(self, record: dict[str, Any]) -> dict[str, Any]:
        ok = record.get("ok")
        if ok is True:
            self.exact(record, ("kind", "id", "ok", "metrics"))
            return {"kind": "document", "id": record["id"], "ok": True,
                    "metrics": self.metrics(record["metrics"])}
        if ok is False:
            if "unmeasured" in record:
                self.exact(record, ("kind", "id", "ok", "error_type", "unmeasured"))
                if record["unmeasured"] not in GROUPS:
                    raise Rejected("an unmeasured group that does not exist")
                return {"kind": "document", "id": record["id"], "ok": False,
                        "error_type": error_name(record["error_type"]),
                        "unmeasured": record["unmeasured"]}
            self.exact(record, ("kind", "id", "ok", "error_type"))
            return {"kind": "document", "id": record["id"], "ok": False,
                    "error_type": error_name(record["error_type"])}
        raise Rejected("a document record without a boolean ok")


def read_run(path: Path, run: comparison.RevisionRun, manifest: Manifest) -> comparison.RevisionRun:
    """Fill ``run`` from a collector's output, sealed; reject it whole on any violation.

    A final line cut off without its newline is a collector stopped mid-write:
    the output is incomplete, as ``compare.read_run`` treats it. Anything else
    malformed is a rejection.
    """
    try:
        _read(path, run, manifest)
    except Rejected:
        run.records = {}
        run.complete = False
        run.fatal = None
        run.process = REJECTED
    return run


def _read(path: Path, run: comparison.RevisionRun, manifest: Manifest) -> None:
    # The collector's directory is writable from inside the sandbox, so what
    # sits there may be a link to a host file, a FIFO or a device. Only a
    # regular file is read, without following links, and never past the limit.
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode):
        raise Rejected("an output that is not a regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise Rejected("an output that cannot be opened safely") from None
    with os.fdopen(descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise Rejected("an output that is not a regular file")
        data = handle.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise Rejected("an output file too large")
    sealer = Sealer(manifest.salt)
    listed = {entry.id for entry in manifest.entries}
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise Rejected("output that is not UTF-8") from None
    lines = text.split("\n")
    torn = lines[-1] != ""
    lines = lines if torn else lines[:-1]
    seen_header = False
    for number, line in enumerate(lines, start=1):
        if run.complete:
            raise Rejected("output after the completion record")
        try:
            record = strict_loads(line)
        except json.JSONDecodeError:
            if torn and number == len(lines):
                run.complete = False
                return
            raise Rejected("a line that is not JSON") from None
        if not isinstance(record, dict):
            raise Rejected("a record that is not an object")
        kind = record.get("kind")
        if kind == "header":
            Sealer.exact(record, ("kind", "schema", "isolated", "python", "platform"))
            if seen_header or number != 1 or record["isolated"] is not True:
                raise Rejected("a header out of place")
            seen_header = True
        elif kind == "fatal":
            Sealer.exact(record, ("kind", "error_type"))
            run.fatal = error_name(record["error_type"])
        elif kind == "document":
            if not seen_header:
                raise Rejected("a document before the header")
            doc_id = record.get("id")
            if not isinstance(doc_id, str) or doc_id not in listed or doc_id in run.records:
                raise Rejected("a document the manifest does not list, or listed twice")
            run.records[doc_id] = sealer.document(record)
        elif kind == "complete":
            Sealer.exact(record, ("kind", "documents"))
            if Sealer.integer(record["documents"]) != len(manifest.entries):
                raise Rejected("a completion record for another corpus")
            run.complete = True
        else:
            raise Rejected("a record of unknown kind")
    if torn:
        raise Rejected("a final line without its newline")


# --------------------------------------------------------------------------
# What is published
# --------------------------------------------------------------------------


def _paths() -> tuple[str, ...]:
    paths = ["claim_count", "status", "extraction_method", "pages", "unplaced", "printed",
             "findings", "summary", "claims", "metadata", "warnings"]
    paths += [f"pages.{name}" for name in (*collect.PAGE_SETS, "unresolved_reasons", "page_count",
                                            "column_split_pages", "rows_seen_per_page")]
    paths += [f"unplaced.{name}" for name in ("count", "by_page", "digest")]
    paths += [f"printed.{name}" for name in ("claim_count", "totals", "unreadable_totals",
                                              "unreadable_totals_page", "count_evidence", "sections")]
    paths += [f"printed.{group}.{name}" for group in ("unreadable_totals", "count_evidence")
              for name in ("count", "digest")]
    paths += [f"printed.sections.{name}" for name in ("count", "claim_counts", "digest")]
    paths += [f"findings.{name}" for name in ("total", "by_rule", "by_rule_severity",
                                               "by_rule_category", "by_rule_scope", "identity", "detail")]
    paths += [f"summary.{name}" for name in ("periods", "claim_counts", "total_claims", "open_claims",
                                              "closed_claims", "printed_claims", "ties", "digest")]
    paths += [f"claims.{name}" for name in ("digest", "fields", "null_counts")]
    paths += [f"metadata.{name}" for name in collect.METADATA_FIELDS]
    paths += [f"warnings.{name}" for name in ("count", "digest")]
    paths += [f"{name}.*" for name in MAP_PATHS]
    return tuple(sorted(set(paths)))


KNOWN_PATHS = _paths()
OTHER_PATH = "other"


def schema_path(path: str) -> str:
    """A change's path with every free map key collapsed to ``*``."""
    parts = path.split(".")
    for name in MAP_PATHS:
        prefix = name.split(".")
        if parts[: len(prefix)] == prefix and len(parts) > len(prefix):
            parts = [*prefix, "*"]
            break
    collapsed = ".".join(parts)
    return collapsed if collapsed in KNOWN_PATHS else OTHER_PATH


def _claim_count(value: Any) -> Any:
    if value == comparison.ABSENT:
        return "absent"
    return value if type(value) is int and 0 <= value <= MAX_INT else None


def _status(value: Any) -> Any:
    if value == comparison.ABSENT:
        return "absent"
    return value if value in STATUS_VOCAB else "unlisted"


def public_result(outcome: comparison.Outcome, manifest: Manifest, verified: int, unlisted: int) -> dict[str, Any]:
    """The whole published result: states, changed schema fields, two critical values."""
    documents: dict[str, Any] = {}
    by_id = {entry.id: entry for entry in manifest.entries}
    for doc_id, state in outcome.documents.items():
        changes = [change for change in outcome.changes if change.document == doc_id]
        entry: dict[str, Any] = {
            "sha256": by_id[doc_id].sha256,
            "state": state,
            "changed": sorted({schema_path(change.path) for change in changes}),
        }
        for change in changes:
            if change.path == "claim_count":
                entry["claim_count"] = {"baseline": _claim_count(change.baseline),
                                        "candidate": _claim_count(change.candidate)}
            elif change.path == "status":
                entry["status"] = {"baseline": _status(change.baseline),
                                   "candidate": _status(change.candidate)}
        documents[doc_id] = entry
    return {
        "gate": {"schema": 2, "verdict": outcome.verdict, "exit_code": outcome.exit_code,
                 "problems": list(outcome.problems)},
        "revisions": {
            run.label: {
                "commit": run.commit,
                "complete": run.complete,
                "fatal": None if run.fatal is None else error_name(run.fatal),
                "process": run.process,
                "measured": sum(1 for record in run.records.values() if record.get("ok")),
            }
            for run in (outcome.baseline, outcome.candidate)
        },
        "corpus": {"manifest_sha256": manifest.sha256, "documents": len(manifest.entries),
                   "verified": verified, "unlisted": unlisted},
        "allowlist": {
            "approved": sum(1 for change in outcome.changes if change.allowlisted_by is not None),
            "unused": [entry.position for entry in outcome.unused],
        },
        "documents": documents,
    }


# --------------------------------------------------------------------------
# Checking and rendering what is published
# --------------------------------------------------------------------------

PROCESS = re.compile(
    r"ok|timed out after [0-9]{1,7} s|exited with code -?[0-9]{1,3}|" + re.escape(REJECTED)
)
PROBLEM = re.compile(r"[A-Za-z0-9 _.,:;()/=<>#'-]{1,240}")
STATES = ("unchanged", "changed", "failed")


class Unpublishable(Exception):
    """A published result outside its schema."""


def _need(condition: bool, what: str) -> None:
    if not condition:
        raise Unpublishable(what)


def _int(value: Any, high: int = MAX_INT) -> int:
    _need(type(value) is int and 0 <= value <= high, "an integer of the wrong type or range")
    return value


def _keys(value: Any, keys: tuple[str, ...], optional: tuple[str, ...] = ()) -> dict[str, Any]:
    _need(isinstance(value, dict), "an object expected")
    _need(set(keys) <= set(value) <= set(keys) | set(optional), "missing or unknown keys")
    return value


def validate_public(result: Any) -> dict[str, Any]:
    """Refuse anything but ``public_result``'s exact shape, types and ranges."""
    _keys(result, ("gate", "revisions", "corpus", "allowlist", "documents"))
    gate = _keys(result["gate"], ("schema", "verdict", "exit_code", "problems"))
    _need(gate["schema"] == 2 and type(gate["schema"]) is int, "an unknown schema")
    _need(gate["verdict"] in ("pass", "fail"), "an unknown verdict")
    _need(type(gate["exit_code"]) is int and gate["exit_code"] in (0, 1, 3, 4), "an unknown exit code")
    _need((gate["verdict"] == "pass") == (gate["exit_code"] == 0), "a verdict that contradicts its exit code")
    corpus = _keys(result["corpus"], ("manifest_sha256", "documents", "verified", "unlisted"))
    _need(isinstance(corpus["manifest_sha256"], str)
          and re.fullmatch(r"[0-9a-f]{64}", corpus["manifest_sha256"]) is not None, "a bad manifest hash")
    total = _int(corpus["documents"])
    _int(corpus["verified"], total)
    _int(corpus["unlisted"])
    problems = gate["problems"]
    _need(isinstance(problems, list) and len(problems) <= 16 + 4 * total, "too many problems")
    for problem in problems:
        _need(isinstance(problem, str) and PROBLEM.fullmatch(problem) is not None, "a problem of the wrong shape")
    revisions = _keys(result["revisions"], ("baseline", "candidate"))
    for run in revisions.values():
        run = _keys(run, ("commit", "complete", "fatal", "process", "measured"))
        _need(isinstance(run["commit"], str) and re.fullmatch(r"[0-9a-f]{40}", run["commit"]) is not None,
              "a bad commit")
        _need(type(run["complete"]) is bool, "a bad completeness flag")
        _need(run["fatal"] is None or run["fatal"] in BUILTIN_ERRORS | {UNLISTED_ERROR}, "a bad fatal error")
        _need(isinstance(run["process"], str) and PROCESS.fullmatch(run["process"]) is not None,
              "a bad process state")
        _int(run["measured"], total)
    allowlist = _keys(result["allowlist"], ("approved", "unused"))
    _int(allowlist["approved"])
    _need(isinstance(allowlist["unused"], list) and len(allowlist["unused"]) <= 1000, "a bad allowlist")
    for position in allowlist["unused"]:
        _need(_int(position, 1000) >= 1, "a bad allowlist position")
    documents = result["documents"]
    _need(isinstance(documents, dict) and len(documents) == total, "a document count mismatch")
    for doc_id, document in documents.items():
        _need(DOCUMENT_ID.fullmatch(doc_id) is not None, "a bad document id")
        document = _keys(document, ("sha256", "state", "changed"), ("claim_count", "status"))
        _need(isinstance(document["sha256"], str)
              and re.fullmatch(r"[0-9a-f]{64}", document["sha256"]) is not None, "a bad document hash")
        _need(document["state"] in STATES, "a bad document state")
        changed = document["changed"]
        _need(isinstance(changed, list) and all(isinstance(path, str) for path in changed)
              and changed == sorted(set(changed))
              and all(path in KNOWN_PATHS or path == OTHER_PATH for path in changed), "a bad change list")
        if "claim_count" in document:
            pair = _keys(document["claim_count"], ("baseline", "candidate"))
            for value in pair.values():
                _need(value in ("absent", None) or (type(value) is int and 0 <= value <= MAX_INT),
                      "a bad claim count")
        if "status" in document:
            pair = _keys(document["status"], ("baseline", "candidate"))
            for value in pair.values():
                _need(value in STATUS_VOCAB | {"absent", "unlisted"}, "a bad status")
    return result


def render_public(result: dict[str, Any]) -> str:
    """``report.txt``, made from the validated public result and nothing else."""
    gate = result["gate"]
    reason = comparison._REASONS.get(gate["exit_code"], "")
    lines = [f"LossLift real-corpus gate: {gate['verdict'].upper()} (exit {gate['exit_code']}: {reason})", ""]
    total = result["corpus"]["documents"]
    for label in ("baseline", "candidate"):
        run = result["revisions"][label]
        lines.append(f"{label:<10} {run['commit'][:12]}  ({run['measured']} of {total} documents measured)")
    lines.append(f"{'corpus':<10} {result['corpus']['verified']} of {total} documents verified "
                 "against the manifest by SHA-256")
    states = [document["state"] for document in result["documents"].values()]
    lines += ["", ", ".join(f"{states.count(state)} {state}" for state in STATES)]
    if gate["problems"]:
        lines += ["", "Execution problems:"] + [f"  {problem}" for problem in gate["problems"]]
    for doc_id, document in sorted(result["documents"].items()):
        if not document["changed"]:
            continue
        lines += ["", f"{doc_id} ({document['state']})"]
        for name in ("claim_count", "status"):
            if name in document:
                pair = document[name]
                lines.append(f"  critical  {name}: {pair['baseline']} -> {pair['candidate']}")
        lines.append("  changed   " + ", ".join(document["changed"]))
    return "\n".join(lines) + "\n"
