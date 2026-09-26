"""The cloud corpus gate: trusted plumbing around ``tools.corpus_gate``.

Inputs and pull-request identity, the private release download, archive
validation, credential scrubbing, output staging, cleanup, the sandboxed
collectors, and the workflow definition itself. The container tests need a
running Docker engine with ``python:3.11-slim-bookworm`` present; they are
skipped, never faked, without one.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.request
import zipfile
from pathlib import Path

import pytest
import yaml

from tools.corpus_gate import cloud
from tools.corpus_gate import manifest as manifests
from tools.corpus_gate.cloud import CloudError
from tools.corpus_gate.sandbox import LABEL, DockerSandbox

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "corpus-gate.yml"
UPDATE_WORKFLOW = REPO / ".github" / "workflows" / "corpus-update.yml"
SHA_A = "a" * 40
SHA_B = "b" * 40
SECRET_NAME = "Maine Sentinel claimant packet"


# --------------------------------------------------------------------------
# Inputs and pull-request identity
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["", "abc1234", "a" * 39, "a" * 41, "A" * 40, "g" * 40, "HEAD", "main", "origin/main",
     SHA_A + "\n", " " + SHA_A, "a" * 20 + ";rm -rf /" + "a" * 11],
)
def test_only_an_exact_full_sha_is_accepted(value):
    with pytest.raises(CloudError, match="exact 40-character"):
        cloud.check_inputs(repository="o/r", baseline=value, candidate=SHA_B, pr="", token=None)
    with pytest.raises(CloudError, match="exact 40-character"):
        cloud.check_inputs(repository="o/r", baseline=SHA_A, candidate=value, pr="", token=None)


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _fake_api(monkeypatch, routes):
    """Answer GitHub API calls from ``routes``: url suffix -> payload, bytes or status."""
    seen = []

    def http(url, token, **kwargs):
        seen.append((url, token, kwargs))
        for suffix, answer in routes.items():
            if url.endswith(suffix):
                if isinstance(answer, int):
                    raise cloud.urllib.error.HTTPError(url, answer, "x", {}, None)
                if isinstance(answer, bytes):
                    return _Response(answer)
                return _Response(json.dumps(answer).encode())
        raise cloud.urllib.error.HTTPError(url, 404, "x", {}, None)

    monkeypatch.setattr(cloud, "http", http)
    return seen


def _pr(head, repo="o/r"):
    return {"head": {"sha": head}, "base": {"repo": {"full_name": repo}}}


def test_the_candidate_must_be_the_live_head_of_the_named_pull_request(monkeypatch):
    _fake_api(monkeypatch, {"/repos/o/r/pulls/7": _pr(SHA_B)})
    assert cloud.check_inputs(repository="o/r", baseline=SHA_A, candidate=SHA_B, pr="7", token="t")
    with pytest.raises(CloudError, match="not the live head of pull request 7"):
        cloud.check_inputs(repository="o/r", baseline=SHA_B, candidate=SHA_A, pr="7", token="t")


def test_a_pull_request_from_another_repository_or_unreadable_is_refused(monkeypatch):
    _fake_api(monkeypatch, {"/repos/o/r/pulls/7": _pr(SHA_B, repo="evil/r"), "/pulls/8": 403})
    with pytest.raises(CloudError, match="does not belong"):
        cloud.check_inputs(repository="o/r", baseline=SHA_A, candidate=SHA_B, pr="7", token="t")
    with pytest.raises(CloudError, match="HTTP 403"):
        cloud.check_inputs(repository="o/r", baseline=SHA_A, candidate=SHA_B, pr="8", token="t")
    for bad in ("0", "-1", "7 ", "7;x", "abc"):
        with pytest.raises(CloudError):
            cloud.check_inputs(repository="o/r", baseline=SHA_A, candidate=SHA_B, pr=bad, token="t")


def test_a_head_that_moves_during_the_run_is_reported_on_recheck(monkeypatch, capsys):
    _fake_api(monkeypatch, {"/repos/o/r/pulls/7": _pr("c" * 40)})
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    code = cloud.main(["check-inputs", "--recheck", "--repository", "o/r", "--baseline", SHA_A,
                       "--candidate", SHA_B, "--pr", "7"])
    assert code == 0 and capsys.readouterr().out == "moved=true\n"
    code = cloud.main(["check-inputs", "--repository", "o/r", "--baseline", SHA_A,
                       "--candidate", SHA_B, "--pr", "7"])
    assert code == 3


# --------------------------------------------------------------------------
# Downloading the private release
# --------------------------------------------------------------------------


def test_a_missing_secret_stops_before_anything_is_fetched(monkeypatch, tmp_path, capsys):
    seen = _fake_api(monkeypatch, {})
    monkeypatch.delenv("LOSSLIFT_CORPUS_TOKEN", raising=False)
    code = cloud.main(["download", "--tag", "corpus-v1", "--out", str(tmp_path / "c.zip"),
                       "--identity", str(tmp_path / "i.json")])
    assert code == 3 and seen == []
    assert "LOSSLIFT_CORPUS_TOKEN secret is not available" in capsys.readouterr().err


@pytest.mark.parametrize("status", [401, 403, 404])
def test_a_missing_or_unreadable_release_is_a_setup_failure(monkeypatch, tmp_path, status):
    _fake_api(monkeypatch, {"/releases/tags/corpus-v9": status})
    with pytest.raises(CloudError, match="not found, or the corpus token cannot read it"):
        cloud.download("o/c", "corpus-v9", "t", tmp_path / "c.zip")
    assert not (tmp_path / "c.zip").exists()


@pytest.mark.parametrize("assets", [[], [{"name": "a.zip", "id": 1, "size": 5, "url": "u1"},
                                         {"name": "b.ZIP", "id": 2, "size": 5, "url": "u2"}]])
def test_a_release_must_carry_exactly_one_zip(monkeypatch, tmp_path, assets):
    _fake_api(monkeypatch, {"/releases/tags/corpus-v1": {"tag_name": "corpus-v1", "id": 3,
                                                          "assets": assets}})
    with pytest.raises(CloudError, match="exactly one .zip"):
        cloud.download("o/c", "corpus-v1", "t", tmp_path / "c.zip")


def test_a_download_is_hashed_and_its_size_enforced(monkeypatch, tmp_path):
    body = b"PK-not-really"
    release = {"tag_name": "corpus-v1", "id": 3,
               "assets": [{"name": "corpus.zip", "id": 4, "size": len(body), "url": "https://api/x/4"}]}
    _fake_api(monkeypatch, {"/releases/tags/corpus-v1": release, "/x/4": body})
    identity = cloud.download("o/c", "corpus-v1", "t", tmp_path / "c.zip")
    assert identity["archive_sha256"] == __import__("hashlib").sha256(body).hexdigest()
    assert identity["tag"] == "corpus-v1" and identity["archive_bytes"] == len(body)
    release["assets"][0]["size"] = len(body) - 1
    with pytest.raises(CloudError, match="larger than it said"):
        cloud.download("o/c", "corpus-v1", "t", tmp_path / "d.zip")
    assert not (tmp_path / "d.zip").exists()


@pytest.mark.parametrize("tag", ["", "../x", "a b", "-x", "x/y", "a" * 101, "v1;id"])
def test_an_unusable_tag_is_refused(tag):
    with pytest.raises(CloudError):
        cloud.check_tag(tag)


def test_the_token_is_not_carried_to_another_host_on_redirect():
    handler = cloud._SameHostRedirect()
    request = urllib.request.Request("https://api.github.com/repos/o/c/releases/assets/4")
    request.add_unredirected_header("Authorization", "Bearer secret")
    request.add_header("Authorization", "Bearer secret")
    moved = handler.redirect_request(request, None, 302, "Found", {},
                                     "https://objects.githubusercontent.com/signed")
    assert "Authorization" not in moved.headers and "Authorization" not in moved.unredirected_hdrs
    same = handler.redirect_request(request, None, 302, "Found", {},
                                    "https://api.github.com/other")
    assert "Bearer secret" not in json.dumps(dict(same.unredirected_hdrs))


# --------------------------------------------------------------------------
# Archive validation
# --------------------------------------------------------------------------


def _corpus_files(root, count=2):
    pdfs = root / "src" / "pdfs"
    pdfs.mkdir(parents=True)
    for n in range(count):
        (pdfs / f"{SECRET_NAME} {n}.pdf").write_bytes(b"%PDF-1.4 real " + bytes([48 + n]))
    manifest = root / "src" / "manifest.json"
    manifests.create(pdfs, manifest)
    return root / "src"


def _zip(path, entries):
    """entries: name -> bytes, or (bytes, ZipInfo tweak)."""
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            if isinstance(data, tuple):
                data, tweak = data
                info = zipfile.ZipInfo(name)
                tweak(info)
                archive.writestr(info, data)
            else:
                archive.writestr(name, data)
    return path


def _corpus_zip(tmp_path, src, prefix="", extra=None, drop=None, change=None):
    entries = {f"{prefix}manifest.json": (src / "manifest.json").read_bytes()}
    for path in sorted((src / "pdfs").rglob("*.pdf")):
        rel = path.relative_to(src).as_posix()
        if rel == drop:
            continue
        entries[prefix + rel] = b"%PDF-changed" if rel == change else path.read_bytes()
    entries.update(extra or {})
    return _zip(tmp_path / "corpus.zip", entries)


def test_a_valid_corpus_archive_is_extracted_and_verified(tmp_path):
    src = _corpus_files(tmp_path)
    corpus = cloud.unpack_corpus(_corpus_zip(tmp_path, src), tmp_path / "out", "corpus-v1")
    assert corpus.count == 2 and corpus.bound_release is None


def test_a_single_wrapper_folder_is_accepted(tmp_path):
    src = _corpus_files(tmp_path)
    corpus = cloud.unpack_corpus(_corpus_zip(tmp_path, src, prefix="corpus-v1/"), tmp_path / "o", "x")
    assert corpus.count == 2


def _link(info):
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16


def _fifo(info):
    info.create_system = 3
    info.external_attr = (stat.S_IFIFO | 0o644) << 16


@pytest.mark.parametrize(
    "entry, words",
    [
        ({"../escape.pdf": b"x"}, "climbs out"),
        ({"pdfs/../../escape.pdf": b"x"}, "climbs out"),
        ({"pdfs/./a.pdf": b"x"}, "climbs out"),
        ({"pdfs//a.pdf": b"x"}, "climbs out"),
        ({"/etc/passwd": b"x"}, "absolute or unsafe"),
        ({"C:/x.pdf": b"x"}, "absolute or unsafe"),
        ({"pdfs\\..\\x.pdf": b"x"}, "absolute or unsafe"),
        ({"pdfs/link.pdf": (b"/etc/passwd", _link)}, "symbolic link"),
        ({"pdfs/fifo.pdf": (b"", _fifo)}, "special file"),
    ],
)
def test_archive_traversal_and_special_entries_are_refused(tmp_path, entry, words):
    src = _corpus_files(tmp_path)
    archive = _corpus_zip(tmp_path, src, extra=entry)
    with pytest.raises(CloudError, match=words) as raised:
        cloud.unpack_corpus(archive, tmp_path / "out", "corpus-v1")
    assert SECRET_NAME not in str(raised.value)
    assert not (tmp_path / "escape.pdf").exists()


def test_an_encrypted_entry_is_refused(tmp_path):
    path = _zip(tmp_path / "c.zip", {"manifest.json": b"{}", "pdfs/secret.pdf": b"%PDF-x"})
    raw = bytearray(path.read_bytes())
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        at = raw.find(signature)
        while at != -1:
            raw[at + offset] |= 0x1
            at = raw.find(signature, at + 4)
    path.write_bytes(bytes(raw))
    with pytest.raises(CloudError, match="encrypted"):
        cloud.unpack_corpus(path, tmp_path / "out", None)


def test_names_that_collide_ignoring_case_are_refused(tmp_path):
    archive = _zip(tmp_path / "c.zip", {"manifest.json": b"{}", "pdfs/A.pdf": b"1", "pdfs/a.pdf": b"2"})
    with pytest.raises(CloudError, match="same name"):
        cloud.unpack_corpus(archive, tmp_path / "out", None)


def test_a_file_that_is_also_a_folder_is_refused(tmp_path):
    archive = _zip(tmp_path / "c.zip", {"manifest.json": b"{}", "pdfs/a": b"1", "pdfs/a/b.pdf": b"2"})
    with pytest.raises(CloudError, match="both a file and a folder"):
        cloud.unpack_corpus(archive, tmp_path / "out", None)


def test_a_compression_bomb_is_refused(tmp_path):
    path = tmp_path / "bomb.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("pdfs/bomb.pdf", b"\0" * (64 << 20))
    with pytest.raises(CloudError, match="compressed beyond"):
        cloud.unpack_corpus(path, tmp_path / "out", None)


@pytest.mark.parametrize("data", [b"", b"not a zip at all", b"PK\x03\x04truncated"])
def test_a_malformed_zip_is_refused(tmp_path, data):
    path = tmp_path / "bad.zip"
    path.write_bytes(data)
    with pytest.raises(CloudError, match="not a readable ZIP"):
        cloud.unpack_corpus(path, tmp_path / "out", None)


def test_a_damaged_entry_is_refused(tmp_path):
    src = _corpus_files(tmp_path)
    archive = _corpus_zip(tmp_path, src)
    raw = bytearray(archive.read_bytes())
    at = raw.find(b"%PDF-1.4 real 0")
    raw[at] ^= 0xFF  # stored entry: its CRC no longer matches
    archive.write_bytes(bytes(raw))
    with pytest.raises(CloudError, match="damaged"):
        cloud.unpack_corpus(archive, tmp_path / "out", None)


def test_an_entry_outside_the_layout_is_refused(tmp_path):
    src = _corpus_files(tmp_path)
    with pytest.raises(CloudError, match="1 entry outside manifest.json and pdfs/"):
        cloud.unpack_corpus(_corpus_zip(tmp_path, src, extra={"notes.txt": b"x"}), tmp_path / "o", None)


def test_a_missing_document_is_refused(tmp_path):
    src = _corpus_files(tmp_path)
    archive = _corpus_zip(tmp_path, src, drop=f"pdfs/{SECRET_NAME} 1.pdf")
    with pytest.raises(CloudError, match="1 listed document\\(s\\) missing") as raised:
        cloud.unpack_corpus(archive, tmp_path / "out", None)
    assert SECRET_NAME not in str(raised.value)


def test_an_additional_document_is_refused(tmp_path):
    src = _corpus_files(tmp_path)
    archive = _corpus_zip(tmp_path, src, extra={"pdfs/unlisted.pdf": b"%PDF-new"})
    with pytest.raises(CloudError, match="1 file\\(s\\) the manifest does not list"):
        cloud.unpack_corpus(archive, tmp_path / "out", None)


def test_a_hash_mismatched_document_is_refused(tmp_path):
    src = _corpus_files(tmp_path)
    archive = _corpus_zip(tmp_path, src, change=f"pdfs/{SECRET_NAME} 0.pdf")
    with pytest.raises(CloudError, match="1 document\\(s\\) whose size or SHA-256 differs"):
        cloud.unpack_corpus(archive, tmp_path / "out", None)


def test_a_manifest_bound_to_another_release_is_refused(tmp_path):
    src = _corpus_files(tmp_path)
    payload = json.loads((src / "manifest.json").read_text())
    payload["release"] = "corpus-v2"
    (src / "manifest.json").write_text(json.dumps(payload))
    with pytest.raises(CloudError, match="different release"):
        cloud.unpack_corpus(_corpus_zip(tmp_path, src), tmp_path / "out", "corpus-v3")
    corpus = cloud.unpack_corpus(_corpus_zip(tmp_path, src), tmp_path / "ok", "corpus-v2")
    assert corpus.bound_release == "corpus-v2"


def test_unpack_deletes_the_archive_even_when_it_is_refused(tmp_path):
    path = tmp_path / "bad.zip"
    path.write_bytes(b"junk")
    identity = tmp_path / "identity.json"
    identity.write_text(json.dumps({"tag": "corpus-v1"}))
    code = cloud.main(["unpack", "--zip", str(path), "--dest", str(tmp_path / "o"),
                       "--identity", str(identity)])
    assert code == 3 and not path.exists()


# --------------------------------------------------------------------------
# Credentials, outputs, cleanup
# --------------------------------------------------------------------------


def test_scrub_removes_stored_credentials_and_refuses_a_token_in_the_environment(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for key, value in [("http.https://github.com/.extraheader", "AUTHORIZATION: basic eDp5"),
                       ("credential.helper", "store")]:
        subprocess.run(["git", "-C", str(repo), "config", key, value], check=True)
    assert cloud.scrub(repo, environ={}) == []
    config = (repo / ".git" / "config").read_text()
    assert "extraheader" not in config and "credential" not in config
    problems = cloud.scrub(repo, environ={"GITHUB_TOKEN": "ghs_x", "LOSSLIFT_CORPUS_TOKEN": "p"})
    assert problems == ["GITHUB_TOKEN is set in the gate's environment",
                        "LOSSLIFT_CORPUS_TOKEN is set in the gate's environment"]


def _public(verdict="pass", code=0, problems=(), **document):
    doc = {"sha256": "c" * 64, "state": "unchanged" if code == 0 else "changed", "changed": []}
    doc.update(document)
    return {
        "gate": {"schema": 2, "verdict": verdict, "exit_code": code, "problems": list(problems)},
        "revisions": {label: {"commit": sha, "complete": True, "fatal": None, "process": "ok",
                              "measured": 1}
                      for label, sha in (("baseline", SHA_A), ("candidate", SHA_B))},
        "corpus": {"manifest_sha256": "d" * 64, "documents": 1, "verified": 1, "unlisted": 0},
        "allowlist": {"approved": 0, "unused": []},
        "documents": {"doc-0123456789ab": doc},
    }


def _gate_out(tmp_path, report="LossLift real-corpus gate: PASS\n", result=None, raw=None):
    out = tmp_path / "gate-out"
    out.mkdir()
    (out / "report.txt").write_text(report)
    (out / "result.json").write_text(raw if raw is not None else json.dumps(result or _public()))
    (out / "measurements.jsonl").write_text("{}")  # never staged
    return out


def _manifest_file(tmp_path, name=SECRET_NAME):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"version": 1, "digest_salt": "5" * 64,
                                "documents": [{"path": f"{name}.pdf"}]}))
    return path


def test_only_report_and_result_are_staged(tmp_path):
    out = _gate_out(tmp_path)
    result = cloud.stage_outputs(out, tmp_path / "up", _manifest_file(tmp_path), [str(tmp_path)])
    assert sorted(p.name for p in (tmp_path / "up").iterdir()) == ["report.txt", "result.json"]
    assert result["gate"]["verdict"] == "pass"


def test_the_runners_own_report_is_never_published(tmp_path):
    out = _gate_out(tmp_path, report=f"{SECRET_NAME} 1250.00 {'5' * 64}\n")
    cloud.stage_outputs(out, tmp_path / "up", _manifest_file(tmp_path), [str(tmp_path)])
    published = (tmp_path / "up" / "report.txt").read_text()
    assert SECRET_NAME not in published and "1250" not in published
    assert published.startswith("LossLift real-corpus gate: PASS (exit 0")


def _problem(text):
    return _public("fail", 4, problems=[text])


@pytest.mark.parametrize(
    "result, words",
    [
        (_problem(f"doc-1 {SECRET_NAME}"), "corpus name, path or secret"),
        (_problem("5" * 64), "corpus name, path or secret"),
        (_problem(f"{SECRET_NAME.lower()}.pdf"), "corpus name"),
        (_problem("written to /home/runner/work/x"), "local path"),
        (_problem("/tmp/losslift-gate-x/1.pdf"), "local path"),
        (_problem("Jos\u00e9"), "not the public result"),
        (dict(_public(), x="anything"), "not the public result"),
        ({"gate": {"exit_code": 4}}, "not the public result"),
    ],
)
def test_outputs_that_could_leak_are_not_staged(tmp_path, result, words):
    out = _gate_out(tmp_path, result=result)
    with pytest.raises(CloudError, match=words):
        cloud.stage_outputs(out, tmp_path / "up", _manifest_file(tmp_path), [str(tmp_path)])
    assert not (tmp_path / "up").exists()


def test_names_are_matched_as_whole_words_and_generic_names_fail_closed(tmp_path):
    out = _gate_out(tmp_path, result=_problem("doc-0123456789ab Buffalonian"))
    cloud.stage_outputs(out, tmp_path / "up", _manifest_file(tmp_path, "Buffalo"), [])
    with pytest.raises(CloudError, match="corpus name"):
        cloud.stage_outputs(out, tmp_path / "up2", _manifest_file(tmp_path, "real-corpus"), [])


def test_cleanup_removes_read_only_trees_and_reports_nothing_left(tmp_path):
    gate = tmp_path / "gate"
    (gate / "corpus" / "pdfs").mkdir(parents=True)
    doc = gate / "corpus" / "pdfs" / "a.pdf"
    doc.write_bytes(b"%PDF")
    os.chmod(doc, stat.S_IREAD)
    os.chmod(gate / "corpus" / "pdfs", stat.S_IREAD | stat.S_IEXEC)
    (gate / "corpus.zip").write_bytes(b"x")
    assert cloud.cleanup([gate, tmp_path / "never-existed"], None, None, docker="no-docker-here") == []
    assert not gate.exists()


@pytest.mark.parametrize("code, verdict, moved, outcome", [
    (0, "pass", "false", "PASS"), (1, "fail", "false", "FAIL"), (3, "fail", "false", "FAIL"),
    (4, "fail", "false", "FAIL"), (None, None, "false", "FAIL"), (0, "pass", "true", "FAIL"),
])
def test_the_summary_names_the_run_exactly(code, verdict, moved, outcome):
    identity = {"tag": "corpus-v2", "archive_sha256": "1" * 64, "manifest_sha256": "2" * 64,
                "documents": 7, "bound": True}
    result = {"gate": {"verdict": verdict, "exit_code": code}} if verdict else None
    text = cloud.summary(baseline=SHA_A, candidate=SHA_B, pr="12", identity=identity, documents=7,
                         exit_code=code, result=result, recheck="success", pr_moved=moved)
    assert f"| Outcome | {outcome} |" in text
    for needle in (SHA_A, SHA_B, "corpus-v2", "1" * 64, "2" * 64, "| Documents verified | 7 |", "#12"):
        assert needle in text
    assert f"| Gate exit code | {'not reached' if code is None else code} |" in text


# --------------------------------------------------------------------------
# The workflow definition
# --------------------------------------------------------------------------


def _load(path):
    document = yaml.safe_load(path.read_text())
    document["on"] = document.pop(True, document.get("on"))  # YAML 1.1 reads `on` as true
    return document


def _steps(path, job):
    return _load(path)["jobs"][job]["steps"]


def _all_workflows():
    return sorted((REPO / ".github" / "workflows").glob("*.y*ml"))


def test_the_gate_runs_only_when_dispatched_from_main():
    workflow = _load(WORKFLOW)
    assert list(workflow["on"]) == ["workflow_dispatch"]
    job = workflow["jobs"]["gate"]
    assert "github.ref == 'refs/heads/main'" in job["if"]
    assert job["environment"] == "corpus-gate"
    guard = job["steps"][0]["run"]
    assert '"$REPOSITORY/.github/workflows/corpus-gate.yml@refs/heads/main"' in guard
    assert set(workflow["on"]["workflow_dispatch"]["inputs"]) == {
        "baseline_sha", "candidate_sha", "corpus_tag", "pr_number"}


def test_no_workflow_runs_with_secrets_on_pull_request_code():
    for path in _all_workflows():
        triggers = _load(path)["on"]
        names = [triggers] if isinstance(triggers, str) else list(triggers)
        assert not {"pull_request", "pull_request_target", "workflow_run", "push"} & set(names), path


def test_the_trusted_code_is_main_and_never_the_candidate():
    for path, job in [(WORKFLOW, "gate"), (UPDATE_WORKFLOW, "propose"), (UPDATE_WORKFLOW, "publish")]:
        checkouts = [s for s in _steps(path, job) if str(s.get("uses", "")).startswith("actions/checkout@")]
        assert checkouts, path
        for step in checkouts:
            assert step["with"]["ref"] == "${{ github.sha }}"
            assert step["with"]["persist-credentials"] is False
            assert "inputs" not in json.dumps(step["with"])


def test_actions_are_pinned_and_inputs_never_reach_a_shell_unquoted():
    for path in _all_workflows():
        for job in _load(path)["jobs"].values():
            for step in job["steps"]:
                if "uses" in step:
                    assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", step["uses"]), step["uses"]
                run = step.get("run", "")
                assert "${{" not in run, f"expression interpolated into a shell in {path.name}"


def test_permissions_are_read_only():
    for path in _all_workflows():
        workflow = _load(path)
        assert all(value in ("read", "none") for value in workflow["permissions"].values())
        for job in workflow["jobs"].values():
            assert "permissions" not in job


def test_the_corpus_token_reaches_only_the_download_step():
    steps = _steps(WORKFLOW, "gate")
    holders = [s["name"] for s in steps if "secrets." in json.dumps(s)]
    assert holders == ["Download the private corpus release"]
    names = [s["name"] for s in steps]
    after = names[names.index("Validate, extract and verify the corpus"):]
    for step in steps:
        if step["name"] in after:
            assert "github.token" not in json.dumps(step) or step["name"] == "Re-check the pull request head"


def test_the_gate_runs_with_an_empty_environment_after_the_scrub():
    steps = _steps(WORKFLOW, "gate")
    names = [s["name"] for s in steps]
    assert names.index("Remove every GitHub credential") < names.index("Run the corpus gate (collectors sandboxed)")
    gate = steps[names.index("Run the corpus gate (collectors sandboxed)")]
    assert gate["run"].lstrip().startswith("set +e\n") and "env -i PATH=" in gate["run"]
    assert "--sandbox-image" in gate["run"] and "python3 -P -m tools.corpus_gate run" in gate["run"]
    assert "env" not in gate


def test_cleanup_and_reporting_always_run():
    steps = {s["name"]: s for s in _steps(WORKFLOW, "gate")}
    for name in ("Remove documents, manifest, worktrees and containers", "Job summary",
                 "Fail unless the gate passed"):
        assert steps[name]["if"].startswith("always()"), name
    for job in ("propose", "publish"):
        cleanup = [s for s in _steps(UPDATE_WORKFLOW, job) if s["name"] == "Remove every document and manifest"]
        assert cleanup and cleanup[0]["if"] == "always()"


def test_only_the_privacy_safe_files_are_uploaded():
    uploads = [s for s in _steps(WORKFLOW, "gate") if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
    assert len(uploads) == 1
    paths = [line.strip() for line in uploads[0]["with"]["path"].splitlines() if line.strip()]
    assert [Path(p).name for p in paths] == ["report.txt", "result.json"]
    assert all(p.startswith("${{ runner.temp }}/losslift-gate-upload/") for p in paths)
    assert "steps.stage.outcome == 'success'" in uploads[0]["if"]


@pytest.mark.parametrize("code", ["1", "2", "3", "4", "", "137"])
def test_the_final_step_fails_for_every_exit_code_but_zero(code):
    step = next(s for s in _steps(WORKFLOW, "gate") if s["name"] == "Fail unless the gate passed")
    env = {"PATH": os.environ["PATH"], "GATE_CODE": code, "PR_MOVED": "false", "STAGED": "success",
           "CLEANED": "success", "PR_NUMBER": "7", "RECHECK": "success"}
    assert subprocess.run(["bash", "-e", "-c", step["run"]], env=env, capture_output=True).returncode != 0
    env["GATE_CODE"] = "0"
    assert subprocess.run(["bash", "-e", "-c", step["run"]], env=env, capture_output=True).returncode == 0
    env["PR_MOVED"] = "true"
    assert subprocess.run(["bash", "-e", "-c", step["run"]], env=env, capture_output=True).returncode != 0


def test_publishing_needs_its_own_environment_and_token():
    workflow = _load(UPDATE_WORKFLOW)
    assert list(workflow["on"]) == ["workflow_dispatch"]
    propose, publish = workflow["jobs"]["propose"], workflow["jobs"]["publish"]
    assert propose["environment"] == "corpus-gate" and publish["environment"] == "corpus-publish"
    assert "LOSSLIFT_CORPUS_WRITE_TOKEN" not in json.dumps(propose)
    assert "LOSSLIFT_CORPUS_WRITE_TOKEN" in json.dumps(publish)
    for job in (propose, publish):
        assert "github.ref == 'refs/heads/main'" in job["if"]


# --------------------------------------------------------------------------
# The sandbox command
# --------------------------------------------------------------------------


def test_the_container_gets_no_network_no_credentials_and_read_only_inputs(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_should_not_pass")
    monkeypatch.setenv("LOSSLIFT_CORPUS_TOKEN", "github_pat_should_not_pass")
    ro = [tmp_path / "worktree", tmp_path / "collect.py", tmp_path / "snapshot"]
    rw = [tmp_path / "out", tmp_path / "scratch"]
    words = DockerSandbox("img").command(name="n", argv=["-P", "c.py"], env={"TMPDIR": "/x"},
                                         read_only=ro, writable=rw, workdir=ro[0])
    text = " ".join(words)
    for flag in ("--network none", "--read-only", "--cap-drop ALL", "--security-opt no-new-privileges",
                 "--pull never", "--log-driver none", "--ipc none", "--rm", f"--label {LABEL}"):
        assert flag in text
    user = words[words.index("--user") + 1]
    assert not user.startswith("0:") and user != "0"
    assert "ghs_" not in text and "github_pat_" not in text and "--env-file" not in text
    assert [w for w in words if w.startswith("TMPDIR")] == ["TMPDIR=/x"]
    mounts = [words[i + 1] for i, w in enumerate(words) if w == "--mount"]
    assert sorted(m for m in mounts if m.endswith(",readonly")) == sorted(
        f"type=bind,source={p},target={p},readonly" for p in ro)
    assert sorted(m for m in mounts if not m.endswith(",readonly")) == sorted(
        f"type=bind,source={p},target={p}" for p in rw)
    assert "-v" not in words and "--privileged" not in words and "docker.sock" not in text


@pytest.mark.parametrize("bad", ["relative/path", "/tmp/a,readonly=false", "/tmp/a\nb"])
def test_a_mount_path_that_could_rewrite_the_mount_is_refused(bad):
    with pytest.raises(manifests.SetupError):
        DockerSandbox("img").command(name="n", argv=[], env={}, read_only=[Path(bad)], writable=[],
                                     workdir=Path("/"))


# --------------------------------------------------------------------------
# End to end, collectors in real containers
# --------------------------------------------------------------------------

IMAGE = "python:3.11-slim-bookworm"


def _docker_ready():
    if not shutil.which("docker"):
        return False
    probe = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True)
    return probe.returncode == 0


needs_docker = pytest.mark.skipif(not _docker_ready(), reason=f"needs Docker with {IMAGE}")

_PROBE = '''

_measured = run_pipeline


def run_pipeline(source, **options):
    """Refuse to measure anything unless the sandbox holds."""
    import os
    import socket
    problems = []
    for key in os.environ:
        if "TOKEN" in key or "SECRET" in key or key.startswith(("GITHUB", "ACTIONS", "LOSSLIFT")):
            problems.append("credential")
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=3).close()
        problems.append("network")
    except OSError:
        pass
    try:
        socket.getaddrinfo("github.com", 443)
        problems.append("dns")
    except OSError:
        pass
    if os.getuid() == 0:
        problems.append("root")
    for target in (str(source), __file__):
        try:
            open(target, "ab").close()
            problems.append("write")
        except OSError:
            pass
    if os.path.exists("/var/run/docker.sock"):
        problems.append("docker")
    if problems:
        raise PermissionError(",".join(problems))
    return _measured(source, **options)
'''


@pytest.fixture(scope="module")
def cloud_world(tmp_path_factory):
    from tests import test_corpus_gate as gate_tests

    root = tmp_path_factory.mktemp("cloud-world")
    repo = root / "repo"
    (repo / "core").mkdir(parents=True)
    (repo / "core" / "__init__.py").write_text("")
    for args in (["init", "-q"], ["config", "user.email", "g@example.invalid"], ["config", "user.name", "G"]):
        gate_tests._git(repo, *args)
    commits = {name: gate_tests._commit(repo, behaviour, name) for name, behaviour in
               [("base", "same"), ("same", "same"), ("more", "more-claims"), ("die", "die"),
                ("hang", "hang")]}
    source = gate_tests._FAKE_PIPELINE.format(
        behaviour="same", claimant=gate_tests.CLAIMANT, claim=gate_tests.CLAIM_NUMBER,
        description=gate_tests.DESCRIPTION)
    (repo / "core" / "pipeline.py").write_text(textwrap.dedent(source) + _PROBE)
    gate_tests._git(repo, "add", "-A")
    gate_tests._git(repo, "commit", "-q", "-m", "probe")
    commits["probe"] = gate_tests._git(repo, "rev-parse", "HEAD")
    corpus = root / "corpus"
    corpus.mkdir()
    (corpus / f"{SECRET_NAME} 1.pdf").write_bytes(b"%PDF-1.4 synthetic 1")
    (corpus / f"{SECRET_NAME} 2.pdf").write_bytes(b"%PDF-1.4 synthetic 2")
    manifest = root / "local" / "manifest.json"
    manifests.create(corpus, manifest)
    return {"root": root, "repo": repo, "corpus": corpus, "manifest": manifest, **commits}


@pytest.fixture()
def private_tmp(tmp_path, monkeypatch):
    target = tmp_path / "system-tmp"
    target.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(target))
    return target


def _containers():
    listed = subprocess.run(["docker", "ps", "-aq", "--filter", f"label={LABEL}"],
                            capture_output=True, text=True)
    return listed.stdout.split()


def _sandboxed(world, candidate, out, **options):
    from tools.corpus_gate import runner

    return runner.run_gate(repo=world["repo"], baseline=world["base"], candidate=world[candidate],
                           corpus=world["corpus"], manifest_path=world["manifest"], out_dir=out,
                           sandbox=DockerSandbox(IMAGE), public=True, **options)


def _assert_nothing_left(world, private_tmp):
    assert _containers() == [], "a container outlived the gate"
    assert list(private_tmp.iterdir()) == [], "temporary material was left behind"
    worktrees = subprocess.run(["git", "-C", str(world["repo"]), "worktree", "list"],
                               capture_output=True, text=True).stdout
    assert worktrees.count("\n") == 1


@needs_docker
@pytest.mark.parametrize("candidate, code", [("same", 0), ("more", 1), ("die", 4)])
def test_sandboxed_gate_exit_codes(cloud_world, tmp_path, private_tmp, candidate, code):
    outcome = _sandboxed(cloud_world, candidate, tmp_path / "out")
    assert outcome.exit_code == code
    manifest = tmp_path / "m.json"
    shutil.copy(cloud_world["manifest"], manifest)
    cloud.stage_outputs(tmp_path / "out", tmp_path / "up", manifest, [str(cloud_world["root"])])
    _assert_nothing_left(cloud_world, private_tmp)


@needs_docker
def test_a_corpus_that_fails_verification_is_exit_3_and_starts_no_container(cloud_world, tmp_path, private_tmp):
    corpus = tmp_path / "corpus"
    shutil.copytree(cloud_world["corpus"], corpus)
    next(corpus.iterdir()).write_bytes(b"%PDF-changed")
    from tools.corpus_gate import runner

    outcome = runner.run_gate(repo=cloud_world["repo"], baseline=cloud_world["base"],
                              candidate=cloud_world["same"], corpus=corpus,
                              manifest_path=cloud_world["manifest"], out_dir=tmp_path / "out",
                              sandbox=DockerSandbox(IMAGE))
    assert outcome.exit_code == 3
    _assert_nothing_left(cloud_world, private_tmp)


@needs_docker
def test_candidate_code_sees_no_network_no_credentials_and_cannot_write(cloud_world, tmp_path,
                                                                        private_tmp, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_sentinel_token")
    monkeypatch.setenv("LOSSLIFT_CORPUS_TOKEN", "github_pat_sentinel")
    # The same probe, unsandboxed, sees the token and the writable copy: it detects.
    from tools.corpus_gate import runner

    host = runner.run_gate(repo=cloud_world["repo"], baseline=cloud_world["base"],
                           candidate=cloud_world["probe"], corpus=cloud_world["corpus"],
                           manifest_path=cloud_world["manifest"], out_dir=tmp_path / "host")
    assert host.exit_code == 4
    sandboxed = _sandboxed(cloud_world, "probe", tmp_path / "sandboxed")
    assert sandboxed.exit_code == 0, sandboxed.report
    for name in ("report.txt", "result.json"):
        text = (tmp_path / "sandboxed" / name).read_text()
        assert "sentinel" not in text.lower() and SECRET_NAME not in text
    _assert_nothing_left(cloud_world, private_tmp)


@needs_docker
def test_a_hung_candidate_is_timed_out_and_its_container_removed(cloud_world, tmp_path, private_tmp):
    started = time.monotonic()
    outcome = _sandboxed(cloud_world, "hang", tmp_path / "out", timeout=8)
    assert outcome.exit_code == 4 and "timed out" in outcome.report
    assert time.monotonic() - started < 90
    _assert_nothing_left(cloud_world, private_tmp)


@needs_docker
def test_an_interrupted_run_stops_its_containers_and_removes_everything(cloud_world, tmp_path):
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "TMPDIR": str(scratch),
           "PYTHONPATH": str(REPO)}
    process = subprocess.Popen(
        [sys.executable, "-P", "-m", "tools.corpus_gate", "run", "--repo", str(cloud_world["repo"]),
         "--baseline", cloud_world["base"], "--candidate", cloud_world["hang"],
         "--corpus", str(cloud_world["corpus"]), "--manifest", str(cloud_world["manifest"]),
         "--out", str(tmp_path / "out"), "--sandbox-image", IMAGE],
        cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    deadline = time.monotonic() + 60
    while not _containers() and time.monotonic() < deadline:
        time.sleep(0.5)
    time.sleep(5)  # the baseline finishes; the hung candidate's container stays
    assert _containers()
    process.send_signal(signal.SIGTERM)
    stdout, _ = process.communicate(timeout=120)
    assert process.returncode == 4 and "Interrupted" in stdout
    assert _containers() == []
    assert list(scratch.iterdir()) == []
    worktrees = subprocess.run(["git", "-C", str(cloud_world["repo"]), "worktree", "list"],
                               capture_output=True, text=True).stdout
    assert worktrees.count("\n") == 1
