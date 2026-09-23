"""The real-corpus A/B gate: what it compares, what it refuses, what it never says.

Three layers. The comparison and the manifest are tested directly on
synthetic measurements. The collector is tested on a real pipeline result of
a synthetic loss run, to prove what it writes carries none of the document.
And the command is run end to end against a throwaway git repository whose
``core`` is a fake that returns sentinel personal data, so that each revision
really is checked out, isolated and measured -- and so a leak of any sentinel
anywhere in the output fails the test.
"""

from __future__ import annotations

import builtins
import errno
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from tools.corpus_gate import collect
from tools.corpus_gate import compare as gate
from tools.corpus_gate import manifest as manifests
from tools.corpus_gate.manifest import Entry, Manifest, SetupError

REPO = Path(__file__).resolve().parents[1]
SALT = bytes.fromhex("11" * 32)
SHA_A = "a" * 64
SHA_B = "b" * 64
COMMIT_A = "1" * 40
COMMIT_B = "2" * 40


# --------------------------------------------------------------------------
# Comparison, on synthetic measurements
# --------------------------------------------------------------------------


def _metrics(**overrides):
    metrics = {
        "claim_count": 3,
        "status": "NEEDS_REVIEW",
        "extraction_method": "digital",
        "pages": {
            "processed_pages": [1, 2],
            "failed_pages": [],
            "skipped_pages": [],
            "unresolved_pages": [3],
            "scanned_pages": [],
            "unresolved_reasons": {"3": "h:" + "3" * 32},
            "page_count": 3,
        },
        "unplaced": {"count": 1, "by_page": {"2": 1}, "digest": "h:" + "4" * 32},
        "printed": {
            "claim_count": 3,
            "totals": {"incurred_total": "h:" + "5" * 32, "paid_total": "h:" + "6" * 32},
        },
        "findings": {
            "total": 2,
            "by_rule": {"R-04": 1, "R-22": 1},
            "by_rule_category": {"R-04/financial": 1, "R-22/extraction": 1},
            "identity": {"R-04": "h:" + "7" * 32, "R-22": "h:" + "8" * 32},
        },
        "summary": {"claim_counts": [3], "total_claims": 3},
    }
    for path, value in overrides.items():
        target = metrics
        *parents, leaf = path.split(".")
        for key in parents:
            target = target[key]
        target[leaf] = value
    return metrics


def _manifest(*ids):
    return Manifest(
        salt=SALT,
        entries=tuple(Entry(doc_id, f"{doc_id}.pdf", sha, 10) for doc_id, sha in ids),
        sha256="c" * 64,
    )


def _run(label, commit, records, complete=True, fatal=None, process="ok"):
    return gate.RevisionRun(
        label=label,
        commit=commit,
        records={
            doc_id: {"kind": "document", "id": doc_id, "ok": True, "metrics": metrics}
            if not isinstance(metrics, str)
            else {"kind": "document", "id": doc_id, "ok": False, "error_type": metrics}
            for doc_id, metrics in records.items()
        },
        complete=complete,
        fatal=fatal,
        process=process,
    )


def _compare(baseline, candidate, allowlist=None, docs=(("doc-a", SHA_A),)):
    return gate.compare(
        _manifest(*docs),
        _run("baseline", COMMIT_A, baseline),
        _run("candidate", COMMIT_B, candidate),
        allowlist,
    )


def test_identical_results_pass():
    outcome = _compare({"doc-a": _metrics()}, {"doc-a": _metrics()})
    assert outcome.exit_code == gate.EXIT_PASS
    assert outcome.changes == [] and outcome.problems == []
    assert outcome.documents == {"doc-a": "unchanged"}


def test_a_changed_claim_count_fails_and_is_called_critical():
    outcome = _compare({"doc-a": _metrics()}, {"doc-a": _metrics(claim_count=2)})
    assert outcome.exit_code == gate.EXIT_CHANGED
    [change] = outcome.changes
    assert (change.path, change.baseline, change.candidate, change.critical) == (
        "claim_count", 3, 2, True
    )


def test_a_changed_reconciliation_status_fails_and_is_called_critical():
    outcome = _compare({"doc-a": _metrics()}, {"doc-a": _metrics(status="CLEAN")})
    assert outcome.exit_code == gate.EXIT_CHANGED
    [change] = outcome.changes
    assert change.path == "status" and change.critical


@pytest.mark.parametrize(
    "path, value",
    [
        ("pages.processed_pages", [1, 2, 3]),
        ("pages.unresolved_pages", []),
        ("pages.failed_pages", [2]),
        ("pages.skipped_pages", [3]),
        ("pages.unresolved_reasons", {"3": "h:" + "9" * 32}),
    ],
)
def test_changed_page_accountability_fails(path, value):
    outcome = _compare({"doc-a": _metrics()}, {"doc-a": _metrics(**{path: value})})
    assert outcome.exit_code == gate.EXIT_CHANGED
    assert all(change.path.startswith("pages.") for change in outcome.changes)
    assert not any(change.critical for change in outcome.changes)


@pytest.mark.parametrize(
    "path, value",
    [
        ("printed.totals", {"incurred_total": "h:" + "0" * 32, "paid_total": "h:" + "6" * 32}),
        ("printed.claim_count", 4),
        ("unplaced.count", 2),
        ("summary.claim_counts", [2, 1]),
    ],
)
def test_changed_printed_evidence_fails(path, value):
    outcome = _compare({"doc-a": _metrics()}, {"doc-a": _metrics(**{path: value})})
    assert outcome.exit_code == gate.EXIT_CHANGED
    assert outcome.changes


@pytest.mark.parametrize(
    "path, value",
    [
        ("findings.by_rule", {"R-04": 1, "R-22": 2}),
        ("findings.by_rule_category", {"R-04/extraction": 1, "R-22/extraction": 1}),
        # Same counts, different claims flagged.
        ("findings.identity", {"R-04": "h:" + "e" * 32, "R-22": "h:" + "8" * 32}),
    ],
)
def test_changed_findings_fail(path, value):
    outcome = _compare({"doc-a": _metrics()}, {"doc-a": _metrics(**{path: value})})
    assert outcome.exit_code == gate.EXIT_CHANGED


def test_a_measurement_present_on_one_side_only_is_a_change():
    after = _metrics()
    after["pages"]["column_split_pages"] = []
    outcome = _compare({"doc-a": _metrics()}, {"doc-a": after})
    [change] = outcome.changes
    assert change.baseline == gate.ABSENT and change.candidate == []


def test_a_candidate_crash_on_a_document_is_an_execution_failure():
    outcome = _compare({"doc-a": _metrics()}, {"doc-a": "ValueError"})
    assert outcome.exit_code == gate.EXIT_EXECUTION
    assert outcome.documents == {"doc-a": "failed"}
    assert any("candidate raised ValueError" in problem for problem in outcome.problems)


def test_a_crash_on_both_sides_still_fails():
    outcome = _compare({"doc-a": "TypeError"}, {"doc-a": "TypeError"})
    assert outcome.exit_code == gate.EXIT_EXECUTION


def test_incomplete_candidate_output_fails():
    docs = (("doc-a", SHA_A), ("doc-b", SHA_B))
    outcome = gate.compare(
        _manifest(*docs),
        _run("baseline", COMMIT_A, {"doc-a": _metrics(), "doc-b": _metrics()}),
        _run("candidate", COMMIT_B, {"doc-a": _metrics()}, complete=False),
    )
    assert outcome.exit_code == gate.EXIT_EXECUTION
    assert any("incomplete" in problem for problem in outcome.problems)
    assert any("doc-b: no candidate output" in problem for problem in outcome.problems)


@pytest.mark.parametrize(
    "kwargs, words",
    [
        ({"fatal": "ImportError"}, "could not start: ImportError"),
        ({"process": "timed out after 5 s"}, "timed out"),
        ({"process": "exited with code -11"}, "exited with code -11"),
    ],
)
def test_a_revision_that_died_fails(kwargs, words):
    outcome = gate.compare(
        _manifest(("doc-a", SHA_A)),
        _run("baseline", COMMIT_A, {"doc-a": _metrics()}),
        _run("candidate", COMMIT_B, {"doc-a": _metrics()}, **kwargs),
    )
    assert outcome.exit_code == gate.EXIT_EXECUTION
    assert any(words in problem for problem in outcome.problems)


def test_output_for_a_document_the_manifest_does_not_list_fails():
    outcome = _compare(
        {"doc-a": _metrics()}, {"doc-a": _metrics(), "doc-zzz": _metrics()}
    )
    assert outcome.exit_code == gate.EXIT_EXECUTION


def test_the_report_leads_with_what_matters_and_collapses_the_rest():
    after = _metrics(claim_count=2)
    after["findings"]["by_rule"] = {"R-04": 2, "R-22": 1}
    after["claims"] = {"fields": {f"field_{n:02d}": "h:" + "d" * 32 for n in range(20)}}
    before = _metrics()
    before["claims"] = {"fields": {f"field_{n:02d}": "h:" + "c" * 32 for n in range(20)}}
    outcome = _compare({"doc-a": before}, {"doc-a": after})
    report = gate.render(outcome, _manifest(("doc-a", SHA_A)), 1)
    lines = report.splitlines()
    first = lines.index("doc-a") + 1
    assert lines[first].split() == ["CRITICAL", "claim_count:", "3", "->", "2"]
    assert "  changed     findings.by_rule.R-04: 1 -> 2" in lines
    collapsed = [line for line in lines if "claims.fields" in line]
    assert len(collapsed) == 1 and "20 entries changed" in collapsed[0]


def test_a_torn_output_file_is_incomplete(tmp_path):
    out = tmp_path / "candidate.jsonl"
    record = {"kind": "document", "id": "doc-a", "ok": True, "metrics": _metrics()}
    out.write_text(
        json.dumps({"kind": "header"}) + "\n" + json.dumps(record) + "\n" + '{"kind": "comp',
        encoding="utf-8",
    )
    run = gate.read_run(out, gate.RevisionRun("candidate", COMMIT_B))
    assert not run.complete
    assert set(run.records) == {"doc-a"}


@pytest.mark.parametrize(
    "before, after",
    [
        (1, True),
        (0, False),
        (True, 1),
        ([1, 0], [True, False]),
        ([{"ties": 0}], [{"ties": False}]),
        (1, 1.0),
    ],
    ids=["1-to-true", "0-to-false", "true-to-1", "nested-list", "nested-object", "int-to-float"],
)
def test_a_value_that_changes_type_is_a_change(before, after):
    outcome = _compare(
        {"doc-a": _metrics(**{"summary.ties": before})},
        {"doc-a": _metrics(**{"summary.ties": after})},
    )
    assert outcome.exit_code == gate.EXIT_CHANGED
    assert [change.path for change in outcome.changes] == ["summary.ties"]


def test_a_document_unmeasured_the_same_way_on_both_sides_still_fails():
    unmeasured = {
        "kind": "document", "id": "doc-a", "ok": False,
        "error_type": "TypeError", "unmeasured": "findings",
    }
    outcome = gate.compare(
        _manifest(("doc-a", SHA_A)),
        gate.RevisionRun("baseline", COMMIT_A, {"doc-a": dict(unmeasured)}, complete=True),
        gate.RevisionRun("candidate", COMMIT_B, {"doc-a": dict(unmeasured)}, complete=True),
    )
    assert outcome.exit_code == gate.EXIT_EXECUTION
    assert outcome.documents == {"doc-a": "failed"}
    assert "doc-a: candidate could not measure findings (TypeError)" in outcome.problems


# --------------------------------------------------------------------------
# The allowlist: explicit, exact, empty by default
# --------------------------------------------------------------------------


def _allow(tmp_path, *entries):
    path = tmp_path / "allowlist.json"
    path.write_text(json.dumps({"version": 1, "entries": list(entries)}), encoding="utf-8")
    return gate.load_allowlist(path)


def _entry(**overrides):
    entry = {
        "document_id": "doc-a",
        "document_sha256": SHA_A,
        "field": "claim_count",
        "baseline": 3,
        "candidate": 2,
        "reason": "Reviewed: the third row was a section total, not a claim.",
    }
    entry.update(overrides)
    return entry


def test_the_allowlist_is_empty_by_default():
    assert gate.load_allowlist(None) == []


def test_an_exact_allowlist_entry_approves_exactly_that_change(tmp_path):
    allowlist = _allow(tmp_path, _entry())
    outcome = _compare({"doc-a": _metrics()}, {"doc-a": _metrics(claim_count=2)}, allowlist)
    assert outcome.exit_code == gate.EXIT_PASS
    assert outcome.changes[0].allowlisted_by == 1


@pytest.mark.parametrize(
    "override",
    [
        {"candidate": 1},
        {"baseline": 4},
        {"field": "status"},
        {"document_sha256": SHA_B},
        {"candidate_commit": "9" * 40},
    ],
)
def test_an_allowlist_entry_that_is_not_exact_approves_nothing(tmp_path, override):
    allowlist = _allow(tmp_path, _entry(**override))
    outcome = _compare({"doc-a": _metrics()}, {"doc-a": _metrics(claim_count=2)}, allowlist)
    assert outcome.exit_code == gate.EXIT_CHANGED
    assert outcome.changes[0].allowlisted_by is None
    assert [entry.position for entry in outcome.unused] == [1]


def test_a_stale_allowlist_entry_fails_even_when_nothing_changed(tmp_path):
    allowlist = _allow(tmp_path, _entry())
    outcome = _compare({"doc-a": _metrics()}, {"doc-a": _metrics()}, allowlist)
    assert outcome.exit_code == gate.EXIT_CHANGED
    assert outcome.unused and not outcome.changes


def test_an_allowlist_entry_bound_to_its_commits_applies_to_them(tmp_path):
    allowlist = _allow(
        tmp_path, _entry(baseline_commit=COMMIT_A, candidate_commit=COMMIT_B)
    )
    outcome = _compare({"doc-a": _metrics()}, {"doc-a": _metrics(claim_count=2)}, allowlist)
    assert outcome.exit_code == gate.EXIT_PASS


def test_approving_one_change_does_not_approve_its_neighbours(tmp_path):
    allowlist = _allow(tmp_path, _entry())
    outcome = _compare(
        {"doc-a": _metrics()}, {"doc-a": _metrics(claim_count=2, status="CLEAN")}, allowlist
    )
    assert outcome.exit_code == gate.EXIT_CHANGED
    assert [change.path for change in outcome.unapproved] == ["status"]


@pytest.mark.parametrize(
    "entry",
    [
        {k: v for k, v in _entry().items() if k != "reason"},
        _entry(reason="ok"),
        _entry(document_sha256="abc123"),
        _entry(field="claims.*"),
        _entry(unexpected="value"),
        _entry(candidate_commit="abc"),
    ],
    ids=["no-reason", "no-real-reason", "short-sha", "wildcard", "unknown-key", "short-commit"],
)
def test_a_malformed_allowlist_entry_is_refused(tmp_path, entry):
    with pytest.raises(SetupError):
        _allow(tmp_path, entry)


def test_a_duplicated_allowlist_entry_is_refused(tmp_path):
    with pytest.raises(SetupError):
        _allow(tmp_path, _entry(), _entry())


@pytest.mark.parametrize(
    "actual, written",
    [
        (True, 1),
        (False, 0),
        (1, True),
        (0, False),
        ([True, False], [1, 0]),
        ([{"ties": True}], [{"ties": 1}]),
        (1, 1.0),
    ],
    ids=["true-as-1", "false-as-0", "1-as-true", "0-as-false", "nested-list", "nested-object",
         "int-as-float"],
)
@pytest.mark.parametrize("side", ["baseline", "candidate"])
def test_an_allowlist_value_matches_only_in_its_own_type(tmp_path, actual, written, side):
    other = "h:" + "0" * 32
    before, after = (actual, other) if side == "baseline" else (other, actual)
    values = {"baseline": other, "candidate": other, side: written}
    allowlist = _allow(tmp_path, _entry(field="summary.ties", **values))
    outcome = _compare(
        {"doc-a": _metrics(**{"summary.ties": before})},
        {"doc-a": _metrics(**{"summary.ties": after})},
        allowlist,
    )
    assert outcome.exit_code == gate.EXIT_CHANGED
    [change] = outcome.changes
    assert change.allowlisted_by is None
    assert [entry.position for entry in outcome.unused] == [1]


@pytest.mark.parametrize(
    "entry",
    [
        {k: v for k, v in _entry().items() if k != "document_id"},
        _entry(document_id="doc a"),
        _entry(document_id=""),
        _entry(document_id=7),
    ],
    ids=["hash-only", "malformed-id", "empty-id", "not-a-string"],
)
def test_an_allowlist_entry_must_name_one_manifest_document(tmp_path, entry):
    with pytest.raises(SetupError):
        _allow(tmp_path, entry)


_TWINS = (("doc-a", SHA_A), ("doc-a-2", SHA_A))  # the same bytes under two manifest entries


def test_approving_one_of_two_identical_documents_leaves_the_other_unapproved(tmp_path):
    allowlist = _allow(tmp_path, _entry(document_id="doc-a"))
    outcome = _compare(
        {"doc-a": _metrics(), "doc-a-2": _metrics()},
        {"doc-a": _metrics(claim_count=2), "doc-a-2": _metrics(claim_count=2)},
        allowlist,
        docs=_TWINS,
    )
    assert outcome.exit_code == gate.EXIT_CHANGED
    assert [(change.document, change.allowlisted_by) for change in outcome.changes] == [
        ("doc-a", 1),
        ("doc-a-2", None),
    ]
    assert outcome.unused == []


@pytest.mark.parametrize(
    "override",
    [
        {"document_id": "doc-a-2"},
        {"document_id": "doc-zzz"},
        {"document_id": "doc-a", "document_sha256": SHA_B},
    ],
    ids=["the-other-copy", "not-in-the-manifest", "id-and-hash-disagree"],
)
def test_an_allowlist_entry_for_another_document_approves_nothing_and_fails(tmp_path, override):
    allowlist = _allow(tmp_path, _entry(**override))
    outcome = _compare(
        {"doc-a": _metrics(), "doc-a-2": _metrics()},
        {"doc-a": _metrics(claim_count=2), "doc-a-2": _metrics()},
        allowlist,
        docs=_TWINS,
    )
    assert outcome.exit_code == gate.EXIT_CHANGED
    [change] = outcome.changes
    assert change.document == "doc-a" and change.allowlisted_by is None
    assert [entry.position for entry in outcome.unused] == [1]


# --------------------------------------------------------------------------
# The manifest: the expected set, byte for byte
# --------------------------------------------------------------------------


def _corpus(tmp_path, files):
    corpus = tmp_path / "corpus"
    for name, data in files.items():
        path = corpus / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return corpus


def test_a_manifest_records_every_pdf_under_hash_derived_ids(tmp_path):
    corpus = _corpus(tmp_path, {"one.pdf": b"%PDF one", "sub/two.PDF": b"%PDF two", "notes.txt": b"x"})
    made = manifests.create(corpus, tmp_path / "manifest.json")
    assert [entry.path for entry in made.entries] == ["one.pdf", "sub/two.PDF"]
    assert all(re.fullmatch(r"doc-[0-9a-f]{12}", entry.id) for entry in made.entries)
    assert len(made.salt) == 32
    assert manifests.verify(made, corpus).ok


def test_the_same_document_under_two_names_keeps_two_entries(tmp_path):
    corpus = _corpus(tmp_path, {"a.pdf": b"%PDF same", "b.pdf": b"%PDF same"})
    made = manifests.create(corpus, tmp_path / "manifest.json")
    ids = [entry.id for entry in made.entries]
    assert len(set(ids)) == 2 and ids[1] == ids[0] + "-2"


def test_an_existing_manifest_is_not_overwritten_by_accident(tmp_path):
    corpus = _corpus(tmp_path, {"one.pdf": b"%PDF one"})
    manifests.create(corpus, tmp_path / "manifest.json")
    with pytest.raises(SetupError):
        manifests.create(corpus, tmp_path / "manifest.json")


def test_a_missing_corpus_file_is_reported_by_id(tmp_path):
    corpus = _corpus(tmp_path, {"one.pdf": b"%PDF one", "two.pdf": b"%PDF two"})
    made = manifests.create(corpus, tmp_path / "manifest.json")
    (corpus / "two.pdf").unlink()
    verification = manifests.verify(made, corpus)
    assert not verification.ok
    assert verification.missing == (made.entries[1].id,)


def test_a_modified_corpus_file_is_a_hash_mismatch(tmp_path):
    corpus = _corpus(tmp_path, {"one.pdf": b"%PDF one"})
    made = manifests.create(corpus, tmp_path / "manifest.json")
    (corpus / "one.pdf").write_bytes(b"%PDF 0ne")  # same size, different bytes
    verification = manifests.verify(made, corpus)
    assert verification.mismatched == (made.entries[0].id,)


def test_an_unlisted_pdf_is_counted_not_run(tmp_path):
    corpus = _corpus(tmp_path, {"one.pdf": b"%PDF one"})
    made = manifests.create(corpus, tmp_path / "manifest.json")
    (corpus / "new.pdf").write_bytes(b"%PDF new")
    verification = manifests.verify(made, corpus)
    assert verification.ok and verification.unlisted == 1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m.update(version=2),
        lambda m: m.update(digest_salt="xyz"),
        lambda m: m.update(documents=[]),
        lambda m: m["documents"][0].update(path="../outside.pdf"),
        lambda m: m["documents"][0].update(path="/abs/one.pdf"),
        lambda m: m["documents"][0].update(sha256="abc"),
        lambda m: m["documents"].append(dict(m["documents"][0])),
    ],
    ids=["version", "salt", "empty", "traversal", "absolute", "sha", "duplicate"],
)
def test_a_malformed_manifest_is_refused(tmp_path, mutate):
    corpus = _corpus(tmp_path, {"one.pdf": b"%PDF one"})
    path = tmp_path / "manifest.json"
    manifests.create(corpus, path)
    payload = json.loads(path.read_text())
    mutate(payload)
    path.write_text(json.dumps(payload))
    with pytest.raises(SetupError):
        manifests.load(path)


def test_a_missing_manifest_or_corpus_is_refused(tmp_path):
    with pytest.raises(SetupError):
        manifests.load(tmp_path / "absent.json")
    corpus = _corpus(tmp_path, {"one.pdf": b"%PDF one"})
    made = manifests.create(corpus, tmp_path / "manifest.json")
    with pytest.raises(SetupError):
        manifests.verify(made, tmp_path / "no-such-corpus")


def test_a_corpus_inside_the_repository_is_refused(tmp_path):
    with pytest.raises(SetupError):
        manifests.ensure_outside(REPO / "some-corpus", REPO, "corpus directory")
    manifests.ensure_outside(tmp_path, REPO, "corpus directory")


# --------------------------------------------------------------------------
# The snapshot: private copies whose names work on every filesystem
# --------------------------------------------------------------------------

#: Names Windows keeps for devices, with or without an extension: ``CON.pdf`` is ``CON``.
_WINDOWS_DEVICES = frozenset(
    {"con", "prn", "aux", "nul", "conin$", "conout$"}
    | {f"{port}{digit}" for port in ("com", "lpt") for digit in "0123456789\u00b9\u00b2\u00b3"}
)
#: What a snapshot file may be called: lower-case ASCII, so no case rule can merge two.
_PORTABLE_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}\.pdf")


def _reserved_on_windows(name):
    stem = name.partition(".")[0].rstrip(" ").casefold()
    return stem in _WINDOWS_DEVICES or name != name.rstrip(". ")


@pytest.fixture()
def windows_rules(monkeypatch):
    """Create files under the registered roots as Windows would.

    There, two names that differ only in case are one file, and a device name
    such as ``CON.pdf`` is not a file at all. Linux allows both, so without
    this a snapshot Windows cannot write would pass here. ``disk_full_at``
    makes that numbered file creation fail as a full disk would.
    """
    state = SimpleNamespace(roots=[], created=[], disk_full_at=None)
    real_open = io.open

    def open_like_windows(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)) and set(mode) & set("wxa+"):
            path = Path(os.fsdecode(file)).resolve()
            if any(path.is_relative_to(Path(root).resolve()) for root in state.roots):
                state.created.append(path)
                if state.disk_full_at == len(state.created):
                    raise OSError(errno.ENOSPC, "injected: no space left on device")
                if _reserved_on_windows(path.name):
                    raise OSError(errno.EINVAL, "Windows keeps this name for a device")
                if path.parent.is_dir() and any(
                    other.name != path.name and other.name.casefold() == path.name.casefold()
                    for other in path.parent.iterdir()
                ):
                    raise FileExistsError(errno.EEXIST, "the same file, ignoring case")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(io, "open", open_like_windows)
    monkeypatch.setattr(builtins, "open", open_like_windows)
    return state


def _hand_manifest(tmp_path, documents):
    """A manifest written by hand: ``documents`` lists ``(id, file name, bytes)``.

    The corpus file names are portable. The ids are what a person might type,
    and manifest validation accepts every one of them.
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    listed = []
    for doc_id, name, data in documents:
        (corpus / name).parent.mkdir(parents=True, exist_ok=True)
        (corpus / name).write_bytes(data)
        listed.append(
            {"id": doc_id, "path": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        )
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps({"version": 1, "digest_salt": SALT.hex(), "documents": listed}), encoding="utf-8"
    )
    return corpus, path, manifests.load(path)


def _snapshot_like_windows(tmp_path, windows_rules, documents):
    corpus, _, manifest = _hand_manifest(tmp_path, documents)
    windows_rules.roots.append(tmp_path / "scratch")
    copies = manifests.snapshot(manifest, corpus, tmp_path / "scratch" / "snapshot")
    return manifest, corpus, copies


def _assert_one_verified_copy_each(manifest, copies):
    assert list(copies) == [entry.id for entry in manifest.entries]
    names = [copy.name for copy in copies.values()]
    assert len({name.casefold() for name in names}) == len(names), f"merged ignoring case: {names}"
    for entry in manifest.entries:
        assert manifests.file_sha256(copies[entry.id]) == entry.sha256, entry.id


def test_ids_that_differ_only_in_case_get_distinct_snapshot_files(tmp_path, windows_rules):
    manifest, _, copies = _snapshot_like_windows(
        tmp_path,
        windows_rules,
        [
            ("Doc-A", "first-loss-run.pdf", b"%PDF-1.4 first"),
            ("doc-a", "second-loss-run.pdf", b"%PDF-1.4 second"),
        ],
    )
    _assert_one_verified_copy_each(manifest, copies)


def test_windows_device_names_cannot_break_the_snapshot(tmp_path, windows_rules):
    devices = ("CON", "PRN", "AUX", "NUL", "COM1", "LPT1")
    manifest, _, copies = _snapshot_like_windows(
        tmp_path,
        windows_rules,
        [
            (name, f"loss-run-{n}.pdf", f"%PDF-1.4 device {n}".encode())
            for n, name in enumerate(devices, start=1)
        ],
    )
    _assert_one_verified_copy_each(manifest, copies)
    assert not any(_reserved_on_windows(copy.name) for copy in copies.values())


@pytest.mark.parametrize(
    "ids", [("twin", "twin-2"), ("Twin", "twin")], ids=["distinct-ids", "ids-differing-in-case"]
)
def test_identical_bytes_under_two_ids_get_two_snapshot_files(tmp_path, windows_rules, ids):
    same = b"%PDF-1.4 the same bytes, listed twice"
    manifest, _, copies = _snapshot_like_windows(
        tmp_path,
        windows_rules,
        [(ids[0], "listed-once.pdf", same), (ids[1], "listed-twice.pdf", same)],
    )
    _assert_one_verified_copy_each(manifest, copies)
    assert copies[ids[0]] != copies[ids[1]]


def test_snapshot_file_names_say_nothing_about_the_document(tmp_path, windows_rules):
    manifest, corpus, copies = _snapshot_like_windows(
        tmp_path,
        windows_rules,
        [
            ("sentinelle-jane", f"{FILE_STEM} 1.pdf", b"%PDF-1.4 one"),
            ("doc-3f9a1c2b7e44", "claims/2024/renewal-packet.pdf", b"%PDF-1.4 two"),
        ],
    )
    _assert_one_verified_copy_each(manifest, copies)
    for entry in manifest.entries:
        name = copies[entry.id].name
        assert _PORTABLE_NAME.fullmatch(name) and not _reserved_on_windows(name), name
        source = manifests.locate(entry, corpus)
        for said in (entry.id, source.name, source.stem, *PurePosixPath(entry.path).parts):
            assert said.casefold() not in name.casefold(), f"{name} says {said}"


# --------------------------------------------------------------------------
# The collector, on a real pipeline result
# --------------------------------------------------------------------------

_SAFE_STRING = re.compile(
    r"h:[0-9a-f]{32}|R-\d{1,3}[a-z]?|[A-Z][A-Z_]{1,23}|[a-z][a-z_]{1,23}|absent"
)
_SAFE_KEY = re.compile(
    r"h:[0-9a-f]{32}|\d+|[a-z][a-z0-9_]{0,47}|(R-\d{1,3}[a-z]?|h:[0-9a-f]{32})"
    r"(/([A-Z][A-Z_]{1,23}|[a-z][a-z_]{1,23}|h:[0-9a-f]{32}))?"
)


def _walk(value, strings, keys):
    if isinstance(value, dict):
        for key, item in value.items():
            keys.append(key)
            _walk(item, strings, keys)
    elif isinstance(value, list):
        for item in value:
            _walk(item, strings, keys)
    elif isinstance(value, str):
        strings.append(value)


def _sensitive(result):
    """Every piece of text the document gave the pipeline, and what it said of them."""
    found = set()
    document = result.document
    for claim in document.claims:
        for name in ("claim_number", "claimant_name", "loss_description", "cause_of_loss",
                     "claimant_ref", "body_part", "nature_of_injury"):
            value = getattr(claim, name, None)
            if value:
                found.add(str(value))
        for name in ("date_of_loss", "date_reported"):
            value = getattr(claim, name, None)
            if value:
                found.add(value.isoformat())
    for name in ("carrier", "named_insured", "policy_number"):
        value = getattr(document, name, None)
        if value:
            found.add(str(value))
    for finding in result.reconciliation.findings:
        found.add(finding.message)
    found.update(result.warnings)
    return {text for text in found if len(text) >= 4}


@pytest.fixture(scope="module")
def measured(tmp_path_factory):
    """A golden fixture with claimant names, run through the real pipeline."""
    from core.pipeline import run_pipeline
    from tests.golden.fixtures import ALL_FIXTURES
    from tests.golden.generate import render

    fixture = next(item for item in ALL_FIXTURES if item.name == "us_basic")
    path = render(fixture, tmp_path_factory.mktemp("gate") / "us_basic.pdf")
    result = run_pipeline(path, use_vision=False)
    return result, collect.measure(result, collect.Digest(SALT))


def test_the_collector_measures_a_real_pipeline_result(measured):
    result, metrics = measured
    assert metrics["claim_count"] == len(result.document.claims) > 0
    assert metrics["status"] == result.reconciliation.status.value
    assert metrics["pages"]["processed_pages"] == sorted(result.document.processed_pages)
    assert metrics["findings"]["total"] == len(result.reconciliation.findings)
    assert metrics["summary"]["total_claims"] == len(result.document.claims)
    assert not any(
        isinstance(group, dict) and "unmeasured" in group for group in metrics.values()
    )


def test_the_collector_writes_nothing_the_document_said(measured):
    result, metrics = measured
    text = json.dumps(metrics, ensure_ascii=False)
    sensitive = _sensitive(result)
    assert any("," in item for item in sensitive), "precondition: claimant names were read"
    assert [item for item in sensitive if item in text] == []


def test_every_string_the_collector_writes_has_a_safe_shape(measured):
    _, metrics = measured
    strings, keys = [], []
    _walk(metrics, strings, keys)
    assert [value for value in strings if not _SAFE_STRING.fullmatch(value)] == []
    assert [key for key in keys if not _SAFE_KEY.fullmatch(key)] == []


def test_digests_are_keyed_by_the_corpus_salt():
    value = {"incurred_total": "12500.00"}
    assert collect.Digest(SALT)(value) == collect.Digest(SALT)(value)
    assert collect.Digest(SALT)(value) != collect.Digest(bytes.fromhex("22" * 32))(value)


def test_a_str_enum_is_measured_by_its_value():
    from core.schema import FindingCategory

    assert collect.plain(FindingCategory.FINANCIAL) == "financial"
    assert type(collect.plain(FindingCategory.FINANCIAL)) is str


def test_the_collector_refuses_to_measure_code_from_outside_its_root(tmp_path, monkeypatch):
    documents = tmp_path / "documents.json"
    documents.write_text("[]")
    out = tmp_path / "out.jsonl"
    monkeypatch.setattr(sys, "stdin", io.StringIO(SALT.hex() + "\n"))
    code = collect.main(
        ["--root", str(tmp_path), "--documents", str(documents), "--out", str(out)]
    )
    assert code == 3
    assert json.loads(out.read_text()) == {"kind": "fatal", "error_type": "IsolationError"}


def test_a_measurement_that_raises_leaves_the_document_unmeasured(tmp_path, monkeypatch):
    import core.pipeline

    def unmeasurable(source, **_):
        document = SimpleNamespace(claims=[], extraction_method="digital")
        reconciliation = SimpleNamespace(status="CLEAN", findings=5)  # not iterable
        return SimpleNamespace(document=document, reconciliation=reconciliation, warnings=[])

    monkeypatch.setattr(core.pipeline, "run_pipeline", unmeasurable)
    documents = tmp_path / "documents.json"
    documents.write_text(json.dumps([{"id": "doc-a", "path": str(tmp_path / "doc-a.pdf")}]))
    out = tmp_path / "out.jsonl"
    monkeypatch.setattr(sys, "stdin", io.StringIO(SALT.hex() + "\n"))
    code = collect.main(["--root", str(REPO), "--documents", str(documents), "--out", str(out)])
    assert code == 0
    [record] = [
        json.loads(line) for line in out.read_text().splitlines()
        if json.loads(line)["kind"] == "document"
    ]
    assert record["ok"] is False and "metrics" not in record
    assert (record["unmeasured"], record["error_type"]) == ("findings", "TypeError")


# --------------------------------------------------------------------------
# End to end: the command, against a throwaway repository
# --------------------------------------------------------------------------

CLAIMANT = "Jane Q. Sentinelle"
CLAIM_NUMBER = "SNTL-44718"
DESCRIPTION = "sentinel slipped on a wet sentinel floor"
FILE_STEM = "Sentinelle Jane claim SNTL-44718 loss run"

_FAKE_PIPELINE = '''
import hashlib
import json
import os
from decimal import Decimal
from enum import Enum


class Status(str, Enum):
    CLEAN = "CLEAN"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class Severity(str, Enum):
    ERROR = "ERROR"


class Category(str, Enum):
    FINANCIAL = "financial"


class Scope(str, Enum):
    CLAIM = "claim"


class Record:
    def __init__(self, **values):
        self.__dict__.update(values)

    def model_dump(self, mode="python"):
        return dict(self.__dict__)


BEHAVIOUR = "{behaviour}"


def _record_read(source, data):
    """For the tests that need to know which bytes a revision really read."""
    folder = os.environ.get("GATE_TEST_READ_LOG")
    if folder:
        line = json.dumps(dict(path=str(source), sha256=hashlib.sha256(data).hexdigest()))
        with open(os.path.join(folder, "%d.jsonl" % os.getpid()), "a", encoding="utf-8") as log:
            log.write(line + "\\n")


def run_pipeline(source, *, use_vision=True, **_):
    data = open(source, "rb").read()
    _record_read(source, data)
    if BEHAVIOUR == "hang":
        import signal
        import time
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        time.sleep(120)
    if BEHAVIOUR == "tamper":
        with open(source, "ab") as handle:
            handle.write(b" tampered")
    if BEHAVIOUR == "crash" and data.endswith(b"2"):
        raise ValueError("cannot parse {claimant} on claim {claim}")
    if BEHAVIOUR == "die":
        import os
        os._exit(9)
    extra = 1 if BEHAVIOUR == "more-claims" else 0
    claims = [
        Record(claim_number="{claim}-%d" % n, claimant_name="{claimant}",
               loss_description="{description}", incurred_total=Decimal("1250.00"))
        for n in range(2 + extra)
    ]
    finding = Record(rule_id="R-01", severity=Severity.ERROR, category=Category.FINANCIAL,
                     scope=Scope.CLAIM, subject="{claim}", condition="primary", field=None,
                     page=1, claim_number="{claim}", related_rows=(),
                     message="{claimant} claim {claim} does not add up",
                     expected=Decimal("1250.00"), actual=Decimal("1300.00"), delta=None)
    document = Record(
        claims=claims, processed_pages=[1], failed_pages=[], skipped_pages=[],
        unresolved_pages=[], scanned_pages=[], unresolved_reasons={{}}, page_count=1,
        unplaced_rows=[], printed_totals={{"incurred_total": Decimal("2500.00")}},
        printed_claim_count=2, named_insured="{claimant}", carrier="Sentinel Mutual",
        extraction_method="digital",
    )
    findings = 5 if BEHAVIOUR == "unmeasurable" else [finding]
    return Record(document=document,
                  reconciliation=Record(status=Status.NEEDS_REVIEW, findings=findings),
                  warnings=["page 1 mentions {claimant}"])
'''


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo, behaviour, message):
    source = _FAKE_PIPELINE.format(
        behaviour=behaviour, claimant=CLAIMANT, claim=CLAIM_NUMBER, description=DESCRIPTION
    )
    (repo / "core" / "pipeline.py").write_text(textwrap.dedent(source), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """A repository with one commit per behaviour, and a corpus beside it."""
    root = tmp_path_factory.mktemp("gate-world")
    repo = root / "repo"
    (repo / "core").mkdir(parents=True)
    (repo / "core" / "__init__.py").write_text("")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "gate@example.invalid")
    _git(repo, "config", "user.name", "Gate Test")
    commits = {
        "base": _commit(repo, "same", "baseline"),
        "same": _commit(repo, "same", "no behavioural change"),
        "more": _commit(repo, "more-claims", "one more claim"),
        "crash": _commit(repo, "crash", "raises on one document"),
        "die": _commit(repo, "die", "collector process dies"),
        "hang": _commit(repo, "hang", "never finishes, and ignores SIGTERM"),
        "tamper": _commit(repo, "tamper", "writes to the document it was given"),
        "unmeasurable": _commit(repo, "unmeasurable", "one measurement group raises"),
        "unmeasurable-again": _commit(repo, "unmeasurable", "the same group raises the same way"),
    }
    corpus = root / "corpus"
    corpus.mkdir()
    (corpus / f"{FILE_STEM} 1.pdf").write_bytes(b"%PDF-1.4 synthetic 1")
    (corpus / f"{FILE_STEM} 2.pdf").write_bytes(b"%PDF-1.4 synthetic 2")
    manifest = root / "local" / "manifest.json"
    code = _gate(["init-manifest", "--corpus", str(corpus), "--manifest", str(manifest),
                  "--repo", str(repo)])[0]
    assert code == 0
    return {"root": root, "repo": repo, "corpus": corpus, "manifest": manifest, **commits}


def _gate(args):
    completed = subprocess.run(
        [sys.executable, "-m", "tools.corpus_gate", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=300,
    )
    return completed.returncode, completed.stdout, completed.stderr


def _run_gate(world, candidate, *, baseline="base", corpus=None, manifest=None, out="out", extra=()):
    out_dir = world["root"] / out
    code, stdout, stderr = _gate(
        [
            "run",
            "--repo", str(world["repo"]),
            "--baseline", world[baseline],
            "--candidate", world[candidate],
            "--corpus", str(corpus or world["corpus"]),
            "--manifest", str(manifest or world["manifest"]),
            "--out", str(out_dir),
            *extra,
        ]
    )
    written = ""
    for name in ("result.json", "report.txt"):
        if (out_dir / name).exists():
            written += (out_dir / name).read_text(encoding="utf-8")
    return code, stdout, stderr, written


def _assert_no_leak(world, *texts):
    blob = "\n".join(texts)
    for secret in (CLAIMANT, CLAIM_NUMBER, DESCRIPTION, FILE_STEM, "Sentinelle", "SNTL",
                   "Sentinel Mutual", "1250.00", "2500.00", str(world["corpus"])):
        assert secret not in blob, f"leaked: {secret!r}"


def _assert_repository_untouched(world):
    assert _git(world["repo"], "worktree", "list").count("\n") == 0, "a worktree was left behind"
    assert _git(world["repo"], "status", "--porcelain") == ""


def test_the_command_passes_identical_behaviour(world):
    code, stdout, stderr, written = _run_gate(world, "same", out="same")
    assert code == 0, stdout
    assert "PASS" in stdout and "2 unchanged, 0 changed, 0 failed" in stdout
    _assert_no_leak(world, stdout, written)
    _assert_repository_untouched(world)


def test_the_command_fails_a_behaviour_change_and_names_it(world):
    code, stdout, _, written = _run_gate(world, "more", out="more")
    assert code == 1, stdout
    assert "CRITICAL" in stdout and "claim_count: 2 -> 3" in stdout
    result = json.loads((world["root"] / "more" / "result.json").read_text())
    assert result["gate"]["verdict"] == "fail"
    _assert_no_leak(world, stdout, written)
    _assert_repository_untouched(world)


def test_the_command_fails_a_candidate_that_raises(world):
    code, stdout, _, written = _run_gate(world, "crash", out="crash")
    assert code == 4, stdout
    assert "candidate raised ValueError" in stdout
    _assert_no_leak(world, stdout, written)
    _assert_repository_untouched(world)


def test_the_command_fails_a_candidate_that_dies(world):
    code, stdout, _, written = _run_gate(world, "die", out="die")
    assert code == 4, stdout
    assert "incomplete" in stdout and "exited with code 9" in stdout
    _assert_no_leak(world, stdout, written)
    _assert_repository_untouched(world)


def test_the_command_refuses_a_corpus_missing_a_document(world, tmp_path):
    partial = tmp_path / "partial"
    partial.mkdir()
    source = sorted(world["corpus"].iterdir())[0]
    (partial / source.name).write_bytes(source.read_bytes())
    code, stdout, _, written = _run_gate(world, "same", corpus=partial, out="missing")
    assert code == 3, stdout
    assert "missing (1)" in stdout and "Nothing was run" in stdout
    result = json.loads((world["root"] / "missing" / "result.json").read_text())
    assert result["gate"]["exit_code"] == 3 and len(result["corpus"]["missing"]) == 1
    _assert_no_leak(world, stdout, written)
    _assert_repository_untouched(world)


def test_the_command_refuses_a_modified_document(world, tmp_path):
    changed = tmp_path / "changed"
    changed.mkdir()
    for source in world["corpus"].iterdir():
        (changed / source.name).write_bytes(source.read_bytes())
    target = sorted(changed.iterdir())[1]
    target.write_bytes(target.read_bytes() + b" edited")
    code, stdout, _, _ = _run_gate(world, "same", corpus=changed, out="modified")
    assert code == 3, stdout
    assert "hash mismatch (1)" in stdout
    _assert_no_leak(world, stdout)


def test_the_command_refuses_a_missing_manifest(world):
    code, stdout, _, _ = _run_gate(world, "same", manifest=world["root"] / "none.json", out="nomf")
    assert code == 3 and "manifest file does not exist" in stdout


def test_the_command_refuses_an_unknown_revision(world):
    code, stdout, _ = _gate(
        ["run", "--repo", str(world["repo"]), "--baseline", world["base"],
         "--candidate", "no-such-branch", "--corpus", str(world["corpus"]),
         "--manifest", str(world["manifest"])]
    )
    assert code == 3 and "is not a commit" in stdout


def test_the_command_applies_an_exact_allowlist(world):
    _run_gate(world, "more", out="to-allow")
    result = json.loads((world["root"] / "to-allow" / "result.json").read_text())
    entries = [
        {
            "document_id": doc_id,
            "document_sha256": document["sha256"],
            "field": change["field"],
            "baseline": change["baseline"],
            "candidate": change["candidate"],
            "reason": "Synthetic: the fake pipeline was told to return one more claim.",
        }
        for doc_id, document in result["documents"].items()
        for change in document["changes"]
    ]
    allowlist = world["root"] / "allowlist.json"
    allowlist.write_text(json.dumps({"version": 1, "entries": entries}))
    code, stdout, _, _ = _run_gate(world, "more", out="allowed", extra=["--allowlist", str(allowlist)])
    assert code == 0, stdout
    assert "allowed #" in stdout


@pytest.mark.parametrize(
    "baseline, candidate",
    [("unmeasurable", "unmeasurable-again"), ("base", "unmeasurable")],
    ids=["both-sides-the-same-way", "candidate-only"],
)
def test_the_command_fails_a_document_it_could_not_measure(world, baseline, candidate):
    code, stdout, _, written = _run_gate(
        world, candidate, baseline=baseline, out=f"unmeasured-{baseline}"
    )
    assert code == 4, stdout
    assert "could not measure findings (TypeError)" in stdout
    assert "0 unchanged, 0 changed, 2 failed" in stdout
    _assert_no_leak(world, stdout, written)
    _assert_repository_untouched(world)


# --------------------------------------------------------------------------
# In process: which bytes the revisions read, and what the gate leaves behind
# --------------------------------------------------------------------------


@pytest.fixture()
def gate_tmp(tmp_path, monkeypatch):
    """Where the gate makes its private scratch directory, so a test can watch it go."""
    target = tmp_path / "system-tmp"
    target.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(target))
    return target


@pytest.fixture()
def collectors(monkeypatch):
    """Every collector the gate launches, and what it removed while one was unreaped.

    Faults can be injected: a launch that fails, a wait that raises, or a hook
    run just before the first collector starts. ``on_launch`` is shown every
    collector's command line before it starts. Any collector still running
    afterwards is killed here, so a failing test leaves nothing behind either.
    """
    from tools.corpus_gate import runner

    state = SimpleNamespace(
        launched=[], removed=[], fail_launch=None, fail_wait=None, before_launch=None,
        on_launch=None,
    )
    real_popen = subprocess.Popen
    real_remove_tree = runner._remove_tree

    def unreaped():
        return sum(1 for process in state.launched if process.returncode is None)

    def popen(args, *rest, **kwargs):
        words = [str(word) for word in args] if isinstance(args, (list, tuple)) else [str(args)]
        if not any(word.endswith("collect.py") for word in words):
            if "worktree" in words and "remove" in words:
                state.removed.append(("worktree", unreaped()))
            return real_popen(args, *rest, **kwargs)
        if state.before_launch is not None:
            hook, state.before_launch = state.before_launch, None
            hook()
        if state.on_launch is not None:
            state.on_launch(words)
        if state.fail_launch == len(state.launched):
            raise OSError("injected: the collector could not be started")
        process = real_popen(args, *rest, **kwargs)
        state.launched.append(process)
        real_wait = process.wait

        def wait(timeout=None):
            if timeout is not None and state.fail_wait is not None:
                failure, state.fail_wait = state.fail_wait, None
                raise failure
            return real_wait(timeout)

        process.wait = wait
        return process

    def remove_tree(path):
        state.removed.append(("temporary files", unreaped()))
        real_remove_tree(path)

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(runner, "_remove_tree", remove_tree)
    yield state
    for process in state.launched:
        if process.poll() is None:
            process.kill()
            process.wait()


def _own_corpus(world, tmp_path):
    """A private copy of the corpus, for a test that changes it."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for source in world["corpus"].iterdir():
        (corpus / source.name).write_bytes(source.read_bytes())
    return corpus


def _in_process(world, baseline, candidate, corpus, out, manifest=None, **options):
    from tools.corpus_gate import runner

    return runner.run_gate(
        repo=world["repo"],
        baseline=world[baseline],
        candidate=world[candidate],
        corpus=corpus,
        manifest_path=manifest or world["manifest"],
        out_dir=out,
        **options,
    )


def _assert_nothing_survives(world, collectors, gate_tmp):
    assert all(process.returncode is not None for process in collectors.launched), (
        "a collector outlived the gate"
    )
    assert collectors.removed, "nothing was cleaned up"
    assert all(unreaped == 0 for _, unreaped in collectors.removed), (
        f"files were removed while a collector still ran: {collectors.removed}"
    )
    assert list(gate_tmp.iterdir()) == [], "temporary material was left behind"
    _assert_repository_untouched(world)


def test_a_document_replaced_after_verification_is_never_measured(
    world, tmp_path, monkeypatch, collectors, gate_tmp
):
    corpus = _own_corpus(world, tmp_path)
    verify = manifests.verify

    def verify_then_replace(manifest, directory):
        verification = verify(manifest, directory)
        for path in directory.iterdir():
            path.write_bytes(b"%PDF-1.4 replaced after verification")
        return verification

    monkeypatch.setattr(manifests, "verify", verify_then_replace)
    outcome = _in_process(world, "base", "same", corpus, tmp_path / "out")
    assert outcome.exit_code == gate.EXIT_SETUP, outcome.report
    assert "changed after it was verified" in outcome.report
    assert collectors.launched == []
    assert list(gate_tmp.iterdir()) == []
    _assert_no_leak(world, outcome.report, json.dumps(outcome.result))
    assert str(corpus) not in outcome.report
    _assert_repository_untouched(world)


def test_both_revisions_read_only_the_verified_bytes(
    world, tmp_path, monkeypatch, collectors, gate_tmp
):
    corpus = _own_corpus(world, tmp_path)
    reads = tmp_path / "reads"
    reads.mkdir()
    monkeypatch.setenv("GATE_TEST_READ_LOG", str(reads))

    def replace_the_corpus():
        for path in corpus.iterdir():
            path.write_bytes(b"%PDF-1.4 replaced as the first collector started")

    collectors.before_launch = replace_the_corpus
    outcome = _in_process(world, "base", "same", corpus, tmp_path / "out")
    listed = sorted(entry.sha256 for entry in manifests.load(world["manifest"]).entries)
    assert not {manifests.file_sha256(path) for path in corpus.iterdir()} & set(listed), (
        "precondition: the corpus was replaced before any collector read it"
    )
    logs = sorted(reads.iterdir())
    assert len(logs) == 2, "one read log per revision"
    for log in logs:
        seen = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        assert sorted(read["sha256"] for read in seen) == listed, "a revision read unverified bytes"
        for read in seen:
            path = Path(read["path"])
            assert not path.resolve().is_relative_to(corpus.resolve())
            assert not path.exists(), "the verified copy outlived the run"
    assert outcome.exit_code == gate.EXIT_PASS, outcome.report
    assert list(gate_tmp.iterdir()) == []


def test_a_revision_that_writes_to_its_document_fails(world, tmp_path, collectors, gate_tmp):
    corpus = _own_corpus(world, tmp_path)
    outcome = _in_process(world, "base", "tamper", corpus, tmp_path / "out")
    assert outcome.exit_code == gate.EXIT_EXECUTION, outcome.report
    assert manifests.verify(manifests.load(world["manifest"]), corpus).ok, (
        "the corpus itself was written to"
    )
    _assert_no_leak(world, outcome.report)
    _assert_nothing_survives(world, collectors, gate_tmp)


def test_a_failed_worktree_leaves_no_collector_or_copy_behind(
    world, tmp_path, monkeypatch, collectors, gate_tmp
):
    from tools.corpus_gate import runner

    git = runner._git
    added = []

    def second_worktree_fails(repo, *args):
        if args[:2] == ("worktree", "add"):
            added.append(args)
            if len(added) == 2:
                raise SetupError("git worktree failed")
        return git(repo, *args)

    monkeypatch.setattr(runner, "_git", second_worktree_fails)
    with pytest.raises(SetupError):
        _in_process(world, "hang", "hang", _own_corpus(world, tmp_path), tmp_path / "out")
    _assert_nothing_survives(world, collectors, gate_tmp)


def test_a_failed_launch_stops_the_collector_already_running(world, tmp_path, collectors, gate_tmp):
    collectors.fail_launch = 1
    with pytest.raises(OSError):
        _in_process(world, "hang", "hang", _own_corpus(world, tmp_path), tmp_path / "out")
    assert len(collectors.launched) == 1
    _assert_nothing_survives(world, collectors, gate_tmp)


@pytest.mark.parametrize(
    "failure", [KeyboardInterrupt, RuntimeError], ids=["interrupted", "wait-failed"]
)
def test_an_interrupted_wait_stops_every_collector_before_cleanup(
    world, tmp_path, collectors, gate_tmp, failure
):
    collectors.fail_wait = failure()
    with pytest.raises(failure):
        _in_process(world, "hang", "hang", _own_corpus(world, tmp_path), tmp_path / "out")
    assert len(collectors.launched) == 2
    _assert_nothing_survives(world, collectors, gate_tmp)


def test_a_timed_out_run_leaves_no_collector_or_copy_behind(world, tmp_path, collectors, gate_tmp):
    outcome = _in_process(
        world, "hang", "hang", _own_corpus(world, tmp_path), tmp_path / "out", timeout=1.0
    )
    assert outcome.exit_code == gate.EXIT_EXECUTION
    assert "timed out" in outcome.report
    assert len(collectors.launched) == 2
    _assert_nothing_survives(world, collectors, gate_tmp)


def test_every_id_is_given_its_own_verified_copy_under_windows_rules(
    world, tmp_path, monkeypatch, collectors, gate_tmp, windows_rules
):
    same = b"%PDF-1.4 the same bytes, listed twice"
    corpus, manifest_path, manifest = _hand_manifest(
        tmp_path,
        [
            ("Doc-A", "first-loss-run.pdf", b"%PDF-1.4 first"),
            ("doc-a", "second-loss-run.pdf", b"%PDF-1.4 second"),
            ("CON", "third-loss-run.pdf", b"%PDF-1.4 third"),
            ("nul", "fourth-loss-run.pdf", b"%PDF-1.4 fourth"),
            ("Twin", "listed-once.pdf", same),
            ("twin", "listed-twice.pdf", same),
        ],
    )
    windows_rules.roots.append(gate_tmp)
    reads = tmp_path / "reads"
    reads.mkdir()
    monkeypatch.setenv("GATE_TEST_READ_LOG", str(reads))
    handed = []

    def read_documents_json(words):
        records = json.loads(Path(words[words.index("--documents") + 1]).read_text(encoding="utf-8"))
        handed.append(
            [(r["id"], Path(r["path"]), manifests.file_sha256(Path(r["path"]))) for r in records]
        )

    collectors.on_launch = read_documents_json
    outcome = _in_process(world, "base", "same", corpus, tmp_path / "out", manifest=manifest_path)
    assert outcome.exit_code == gate.EXIT_PASS, outcome.report

    assert len(handed) == 2 and handed[0] == handed[1], "both revisions were given the same copies"
    given = handed[0]
    assert [doc_id for doc_id, _, _ in given] == [entry.id for entry in manifest.entries]
    names = [path.name for _, path, _ in given]
    assert len({name.casefold() for name in names}) == len(names), f"merged ignoring case: {names}"
    for entry, (doc_id, path, sha) in zip(manifest.entries, given):
        assert sha == entry.sha256, f"{doc_id} was given another document's bytes"
        assert path.resolve().is_relative_to(gate_tmp.resolve()), f"{doc_id}: not a private copy"
        assert _PORTABLE_NAME.fullmatch(path.name) and not _reserved_on_windows(path.name)
        assert doc_id.casefold() not in path.name.casefold()
    written = [path for path in windows_rules.created if path.suffix == ".pdf"]
    assert len(written) == len(given), "every copy was written under Windows rules"

    expected = sorted((str(path), sha) for _, path, sha in given)
    for log in sorted(reads.iterdir()):
        seen = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        assert sorted((read["path"], read["sha256"]) for read in seen) == expected
    assert len(list(reads.iterdir())) == 2, "one read log per revision"
    _assert_no_leak(world, outcome.report, json.dumps(outcome.result))
    _assert_nothing_survives(world, collectors, gate_tmp)


def test_a_failure_while_copying_leaves_no_snapshot_behind(
    world, tmp_path, collectors, gate_tmp, windows_rules
):
    windows_rules.roots.append(gate_tmp)
    windows_rules.disk_full_at = 2
    with pytest.raises(OSError):
        _in_process(world, "base", "same", _own_corpus(world, tmp_path), tmp_path / "out")
    assert [path.suffix for path in windows_rules.created] == [".pdf", ".pdf"], (
        "the disk filled on the second copy"
    )
    assert collectors.launched == []
    assert list(gate_tmp.iterdir()) == []
    _assert_repository_untouched(world)


def test_an_unexpected_error_is_an_execution_failure_named_by_type(tmp_path, monkeypatch, capsys):
    from tools.corpus_gate import __main__ as cli

    def explode(**_):
        raise OSError(f"cannot read {FILE_STEM} 1.pdf for {CLAIMANT}")

    monkeypatch.setattr(cli, "run_gate", explode)
    code = cli.main(
        ["run", "--baseline", "a", "--candidate", "b", "--corpus", str(tmp_path),
         "--manifest", str(tmp_path / "manifest.json")]
    )
    out, err = capsys.readouterr()
    assert code == gate.EXIT_EXECUTION
    assert "OSError" in out
    assert CLAIMANT not in out + err and FILE_STEM not in out + err
