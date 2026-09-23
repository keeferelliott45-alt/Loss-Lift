"""Compare two revisions' measurements, and say plainly what moved.

Every difference fails the gate unless an allowlist entry names it exactly:
the document by its manifest id and its SHA-256, the measurement by its full
path, and both the baseline and the candidate value. Nothing is approved by
pattern, by rule, or because it looks small. An entry that matches nothing
fails too -- an allowlist describes the changes a reviewer expected, and a
stale one is a review nobody is reading any more.

Values are compared as JSON values, where the type is part of the value:
``true`` is not ``1`` and ``1`` is not ``1.0``, at any depth. That holds for
deciding whether a measurement changed and for deciding whether an entry
approves the change.

Execution failures -- a revision that cannot start, times out, crashes, or
leaves a document unmeasured -- fail with their own exit code and are never
allowlistable. A gate that ran on part of the corpus has not passed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.corpus_gate.manifest import DOCUMENT_ID, SHA256, Manifest, SetupError

EXIT_PASS = 0
EXIT_CHANGED = 1
EXIT_SETUP = 3
EXIT_EXECUTION = 4

#: A change here is called out first: it is what a reviewer must see.
CRITICAL = ("claim_count", "status")
ABSENT = "<absent>"

ALLOWLIST_VERSION = 1
_FIELD_PATH = re.compile(r"[A-Za-z0-9_.:/<>-]{1,240}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_GROUP = re.compile(r"[a-z][a-z_]{0,31}")
_ENTRY_KEYS = {
    "document_id",
    "document_sha256",
    "field",
    "baseline",
    "candidate",
    "reason",
    "baseline_commit",
    "candidate_commit",
}


@dataclass
class RevisionRun:
    """What one revision's collector left behind."""

    label: str
    commit: str
    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    complete: bool = False
    fatal: str | None = None
    process: str = "ok"
    seconds: float = 0.0


@dataclass(frozen=True)
class AllowEntry:
    position: int
    document_id: str
    document_sha256: str
    field: str
    baseline: Any
    candidate: Any
    reason: str
    baseline_commit: str | None = None
    candidate_commit: str | None = None


@dataclass
class Change:
    document: str
    sha256: str
    path: str
    baseline: Any
    candidate: Any
    critical: bool
    allowlisted_by: int | None = None


@dataclass
class Outcome:
    baseline: RevisionRun
    candidate: RevisionRun
    problems: list[str]
    changes: list[Change]
    unused: list[AllowEntry]
    documents: dict[str, str]  # id -> unchanged | changed | failed
    exit_code: int

    @property
    def verdict(self) -> str:
        return "pass" if self.exit_code == EXIT_PASS else "fail"

    @property
    def unapproved(self) -> list[Change]:
        return [change for change in self.changes if change.allowlisted_by is None]


# --------------------------------------------------------------------------
# Reading a collector's output
# --------------------------------------------------------------------------


def read_run(path: Path, run: RevisionRun) -> RevisionRun:
    """Fill ``run`` from a collector's JSON lines. A torn last line is incomplete."""
    if not path.is_file():
        return run
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            run.complete = False
            break
        kind = record.get("kind")
        if kind == "fatal":
            run.fatal = str(record.get("error_type", "UnnamedError"))
        elif kind == "document":
            run.records[str(record.get("id"))] = record
        elif kind == "complete":
            run.complete = True
    return run


# --------------------------------------------------------------------------
# The allowlist
# --------------------------------------------------------------------------


def load_allowlist(path: Path | None) -> list[AllowEntry]:
    """Read an allowlist. None means empty, which is the default."""
    if path is None:
        return []
    if not path.is_file():
        raise SetupError("the allowlist file does not exist")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SetupError("the allowlist is not valid JSON") from error
    if not isinstance(payload, dict) or payload.get("version") != ALLOWLIST_VERSION:
        raise SetupError(f"the allowlist must be a version {ALLOWLIST_VERSION} object")
    items = payload.get("entries")
    if not isinstance(items, list):
        raise SetupError("the allowlist must hold an 'entries' list")

    entries: list[AllowEntry] = []
    seen: set[str] = set()
    for position, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise SetupError(f"allowlist entry {position} is not an object")
        unknown = set(item) - _ENTRY_KEYS
        if unknown:
            raise SetupError(f"allowlist entry {position} has unknown keys: {sorted(unknown)}")
        for required in ("document_id", "document_sha256", "field", "baseline", "candidate", "reason"):
            if required not in item:
                raise SetupError(f"allowlist entry {position} lacks '{required}'")
        # The same bytes can be listed twice, under two ids: the id is what
        # makes an entry approve one document's change and not its twin's.
        doc_id = item["document_id"]
        if not isinstance(doc_id, str) or not DOCUMENT_ID.fullmatch(doc_id):
            raise SetupError(f"allowlist entry {position} must name one manifest document by id")
        sha = item["document_sha256"]
        if not isinstance(sha, str) or not SHA256.fullmatch(sha):
            raise SetupError(f"allowlist entry {position} needs a full document sha256")
        path_name = item["field"]
        if not isinstance(path_name, str) or not _FIELD_PATH.fullmatch(path_name):
            raise SetupError(f"allowlist entry {position} names no exact field")
        reason = item["reason"]
        if not isinstance(reason, str) or len(reason.strip()) < 10:
            raise SetupError(f"allowlist entry {position} must say why, in words")
        commits = {}
        for key in ("baseline_commit", "candidate_commit"):
            value = item.get(key)
            if value is not None and (not isinstance(value, str) or not _COMMIT.fullmatch(value)):
                raise SetupError(f"allowlist entry {position}'s {key} must be a full commit id")
            commits[key] = value
        identity = canonical(
            [doc_id, sha, path_name, item["baseline"], item["candidate"], commits]
        )
        if identity in seen:
            raise SetupError(f"allowlist entry {position} repeats an earlier entry")
        seen.add(identity)
        entries.append(
            AllowEntry(
                position=position,
                document_id=doc_id,
                document_sha256=sha,
                field=path_name,
                baseline=item["baseline"],
                candidate=item["candidate"],
                reason=reason.strip(),
                **commits,
            )
        )
    return entries


def canonical(value: Any) -> str:
    """``value`` as JSON text, in which its type is part of what it is.

    Python's ``==`` holds ``True == 1`` and ``1 == 1.0``, inside lists and
    mappings too. JSON does not, and neither does the gate: a measurement
    that changes type has changed, and an entry approves only the value it
    names, in the type it names it.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _matches(entry: AllowEntry, change: Change, baseline: str, candidate: str) -> bool:
    return (
        entry.document_id == change.document
        and entry.document_sha256 == change.sha256
        and entry.field == change.path
        and canonical(entry.baseline) == canonical(change.baseline)
        and canonical(entry.candidate) == canonical(change.candidate)
        and entry.baseline_commit in (None, baseline)
        and entry.candidate_commit in (None, candidate)
    )


# --------------------------------------------------------------------------
# Comparing
# --------------------------------------------------------------------------


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """Nested measurements as dotted paths. Lists are compared whole."""
    if isinstance(value, dict) and value:
        out: dict[str, Any] = {}
        for key, item in value.items():
            out.update(flatten(item, f"{prefix}.{key}" if prefix else str(key)))
        return out
    return {prefix: value}


def _failure(doc_id: str, label: str, record: dict[str, Any]) -> str:
    error = record.get("error_type", "UnnamedError")
    group = record.get("unmeasured")
    if group is None:
        return f"{doc_id}: {label} raised {error}"
    named = group if isinstance(group, str) and _GROUP.fullmatch(group) else "a measurement"
    return f"{doc_id}: {label} could not measure {named} ({error})"


def compare(
    manifest: Manifest,
    baseline: RevisionRun,
    candidate: RevisionRun,
    allowlist: list[AllowEntry] | None = None,
    changed_copies: Iterable[str] = (),
) -> Outcome:
    """Compare two runs over ``manifest``.

    ``changed_copies`` names documents whose verified copy did not survive the
    run intact. Neither revision's measurement of them can be trusted, so they
    fail like a crash does.
    """
    allowlist = allowlist or []
    moved = set(changed_copies)
    problems: list[str] = []
    changes: list[Change] = []
    documents: dict[str, str] = {}

    for run in (baseline, candidate):
        if run.fatal:
            problems.append(f"{run.label} could not start: {run.fatal}")
        if run.process != "ok":
            problems.append(f"{run.label} collector {run.process}")
        if not run.complete:
            problems.append(
                f"{run.label} output is incomplete: "
                f"{len(run.records)} of {len(manifest.entries)} documents recorded"
            )
        extra = set(run.records) - {entry.id for entry in manifest.entries}
        if extra:
            problems.append(f"{run.label} reported {len(extra)} document(s) the manifest does not list")

    for entry in manifest.entries:
        failed = False
        if entry.id in moved:
            problems.append(f"{entry.id}: its verified copy changed during the run")
            failed = True
        for run in (baseline, candidate):
            record = run.records.get(entry.id)
            if record is None:
                problems.append(f"{entry.id}: no {run.label} output")
                failed = True
            elif not record.get("ok"):
                problems.append(_failure(entry.id, run.label, record))
                failed = True
        if failed:
            documents[entry.id] = "failed"
            continue

        before = flatten(baseline.records[entry.id].get("metrics", {}))
        after = flatten(candidate.records[entry.id].get("metrics", {}))
        found = False
        for path in sorted(set(before) | set(after)):
            old, new = before.get(path, ABSENT), after.get(path, ABSENT)
            if canonical(old) == canonical(new):
                continue
            found = True
            changes.append(
                Change(
                    document=entry.id,
                    sha256=entry.sha256,
                    path=path,
                    baseline=old,
                    candidate=new,
                    critical=path in CRITICAL,
                )
            )
        documents[entry.id] = "changed" if found else "unchanged"

    used: set[int] = set()
    for change in changes:
        for entry in allowlist:
            if _matches(entry, change, baseline.commit, candidate.commit):
                change.allowlisted_by = entry.position
                used.add(entry.position)
                break
    unused = [entry for entry in allowlist if entry.position not in used]

    if problems:
        code = EXIT_EXECUTION
    elif any(change.allowlisted_by is None for change in changes) or unused:
        code = EXIT_CHANGED
    else:
        code = EXIT_PASS
    return Outcome(baseline, candidate, problems, changes, unused, documents, code)


# --------------------------------------------------------------------------
# Saying so
# --------------------------------------------------------------------------

_REASONS = {
    EXIT_PASS: "no behavioral change",
    EXIT_CHANGED: "behavioral changes",
    EXIT_EXECUTION: "execution failure or incomplete output",
}


#: Groups that restate a primary measurement in finer grain. In the report each
#: collapses to one line, however many entries moved; result.json keeps all.
_SECONDARY = (
    "claims.fields",
    "claims.null_counts",
    "findings.by_rule_category",
    "findings.by_rule_scope",
    "findings.by_rule_severity",
    "findings.identity",
    "findings.detail",
)
#: Primary groups, in the order a reviewer should read them.
_ORDER = ("pages", "printed", "unplaced", "findings", "summary", "claims", "metadata", "warnings")
_LINES_PER_GROUP = 6


def _show(value: Any) -> str:
    if value == ABSENT:
        return "absent"
    if isinstance(value, str) and value.startswith("h:"):
        return f"digest {value[2:10]}"
    if isinstance(value, list) and len(value) > 12:
        return f"[{len(value)} items]"
    return json.dumps(value, sort_keys=True)


def _group(path: str) -> str:
    return ".".join(path.split(".")[:2])


def _label(change: Change) -> str:
    if change.allowlisted_by is not None:
        return f"allowed #{change.allowlisted_by}"
    return "CRITICAL" if change.critical else "changed"


def _document_lines(found: list[Change]) -> list[str]:
    """One document's changes: critical first, then primary, then collapsed."""
    lines = [
        f"  {_label(change):<11} {change.path}: {_show(change.baseline)} -> {_show(change.candidate)}"
        for change in found
        if change.critical
    ]
    groups: dict[str, list[Change]] = {}
    for change in found:
        if not change.critical:
            groups.setdefault(_group(change.path), []).append(change)

    def rank(name: str) -> tuple[int, int, str]:
        head = name.split(".")[0]
        position = _ORDER.index(head) if head in _ORDER else len(_ORDER)
        return (name in _SECONDARY, position, name)

    for name in sorted(groups, key=rank):
        members = groups[name]
        if name not in _SECONDARY and len(members) <= _LINES_PER_GROUP:
            lines += [
                f"  {_label(change):<11} {change.path}: {_show(change.baseline)} -> {_show(change.candidate)}"
                for change in members
            ]
            continue
        approved = sum(1 for change in members if change.allowlisted_by is not None)
        label = "allowed" if approved == len(members) else "changed"
        leaves = [change.path[len(name) + 1:] or change.path for change in members]
        shown = ", ".join(leaves[:4]) + (", ..." if len(leaves) > 4 else "")
        note = f", {approved} approved" if 0 < approved < len(members) else ""
        lines.append(f"  {label:<11} {name}: {len(members)} entries changed{note} ({shown})")
    return lines


def render(outcome: Outcome, manifest: Manifest, verified: int) -> str:
    """The human-readable comparison. Ids, paths and safe values only."""
    lines = [
        f"LossLift real-corpus gate: {outcome.verdict.upper()} "
        f"(exit {outcome.exit_code}: {_REASONS[outcome.exit_code]})",
        "",
    ]
    total = len(manifest.entries)
    for run in (outcome.baseline, outcome.candidate):
        measured = sum(1 for record in run.records.values() if record.get("ok"))
        lines.append(
            f"{run.label:<10} {run.commit[:12]}  "
            f"({measured} of {total} documents measured, {run.seconds:.1f} s)"
        )
    lines.append(f"{'corpus':<10} {verified} of {total} documents verified against the manifest by SHA-256")
    applied = sum(1 for change in outcome.changes if change.allowlisted_by is not None)
    lines.append(
        f"{'allowlist':<10} {applied} change(s) approved, {len(outcome.unused)} unused entries"
    )
    lines.append("")

    counts = {state: sum(1 for value in outcome.documents.values() if value == state)
              for state in ("unchanged", "changed", "failed")}
    lines.append(
        f"{counts['unchanged']} unchanged, {counts['changed']} changed, {counts['failed']} failed"
    )

    if outcome.problems:
        lines += ["", "Execution problems:"]
        lines += [f"  {problem}" for problem in outcome.problems]

    by_document: dict[str, list[Change]] = {}
    for change in outcome.changes:
        by_document.setdefault(change.document, []).append(change)
    for document, found in sorted(by_document.items()):
        lines += ["", document]
        lines += _document_lines(sorted(found, key=lambda item: item.path))

    if outcome.unused:
        lines += ["", "Allowlist entries that matched nothing (stale -- remove or correct them):"]
        lines += [
            f"  #{entry.position} {entry.document_id} {entry.field}" for entry in outcome.unused
        ]
    if outcome.unapproved:
        lines += [
            "",
            "Exact values for review are in result.json. A change is approved only by an",
            "allowlist entry naming its document id and sha256, field, baseline and candidate",
            "value.",
        ]
    return "\n".join(lines) + "\n"


def to_json(outcome: Outcome, manifest: Manifest, verified: int, unlisted: int) -> dict[str, Any]:
    """The machine-readable result. Hashes, ids, counts and digests only."""
    by_id = {entry.id: entry for entry in manifest.entries}
    changes_by_doc: dict[str, list[dict[str, Any]]] = {}
    for change in outcome.changes:
        changes_by_doc.setdefault(change.document, []).append(
            {
                "field": change.path,
                "baseline": change.baseline,
                "candidate": change.candidate,
                "critical": change.critical,
                "allowlisted_by": change.allowlisted_by,
            }
        )
    return {
        "gate": {
            "schema": 1,
            "verdict": outcome.verdict,
            "exit_code": outcome.exit_code,
            "problems": outcome.problems,
        },
        "revisions": {
            run.label: {
                "commit": run.commit,
                "complete": run.complete,
                "fatal": run.fatal,
                "process": run.process,
                "seconds": round(run.seconds, 1),
                "measured": sum(1 for record in run.records.values() if record.get("ok")),
            }
            for run in (outcome.baseline, outcome.candidate)
        },
        "corpus": {
            "manifest_sha256": manifest.sha256,
            "documents": len(manifest.entries),
            "verified": verified,
            "unlisted": unlisted,
        },
        "allowlist": {
            "approved": sum(1 for change in outcome.changes if change.allowlisted_by is not None),
            "unused": [entry.position for entry in outcome.unused],
        },
        "documents": {
            doc_id: {
                "sha256": by_id[doc_id].sha256,
                "state": state,
                "changes": changes_by_doc.get(doc_id, []),
                "baseline": outcome.baseline.records.get(doc_id),
                "candidate": outcome.candidate.records.get(doc_id),
            }
            for doc_id, state in outcome.documents.items()
        },
    }
