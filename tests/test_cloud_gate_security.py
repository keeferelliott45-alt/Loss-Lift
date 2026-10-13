"""Regressions for the three findings on the cloud corpus gate.

1. Candidate-controlled measurements must not carry corpus bytes or the salt
   out of the runner: not as huge integers, not as many small ones, not as
   exception names, enumeration tokens, structure or timing.
2. A pull request's head must be re-confirmed after the run, and anything
   short of a successful re-check fails closed.
3. Cleanup must prove that no task container or image survived.

The workflow's own step scripts are executed here, so the tests exercise
the definition that runs rather than a paraphrase of it.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from tools.corpus_gate import cloud
from tools.corpus_gate import manifest as manifests

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "corpus-gate.yml"
IMAGE = "python:3.11-slim-bookworm"
SHA_A, SHA_B = "a" * 40, "b" * 40


def _steps():
    document = yaml.safe_load(WORKFLOW.read_text())
    return {step["name"]: step for step in document["jobs"]["gate"]["steps"]}


def _run_step(name, env, cwd=REPO):
    """Run one workflow step's script as the runner would."""
    script = _steps()[name]["run"]
    base = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")}
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        env={**base, **env}, cwd=cwd, capture_output=True, text=True, timeout=600,
    )


# --------------------------------------------------------------------------
# Finding 2: the pull-request recheck must itself succeed
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "recheck, moved, passes",
    [
        ("success", "false", True),     # unchanged head, confirmed
        ("success", "true", False),     # the head moved
        ("failure", "", False),         # API, token or network failure
        ("cancelled", "", False),
        ("skipped", "", False),         # never ran
        ("success", "", False),         # succeeded but wrote no output
        ("success", "maybe", False),    # malformed output
        ("success", "False", False),
    ],
)
def test_a_named_pull_request_passes_only_after_a_successful_recheck(recheck, moved, passes):
    env = {"GATE_CODE": "0", "STAGED": "success", "CLEANED": "success", "PR_NUMBER": "7",
           "RECHECK": recheck, "PR_MOVED": moved}
    assert (_run_step("Fail unless the gate passed", env).returncode == 0) is passes


def test_without_a_pull_request_no_recheck_is_needed():
    env = {"GATE_CODE": "0", "STAGED": "success", "CLEANED": "success", "PR_NUMBER": "",
           "RECHECK": "skipped", "PR_MOVED": ""}
    assert _run_step("Fail unless the gate passed", env).returncode == 0
    for failed in ("failure", "cancelled", ""):
        env["CLEANED"] = failed
        assert _run_step("Fail unless the gate passed", env).returncode != 0


def test_staging_and_upload_require_the_recheck_when_a_pull_request_is_named():
    steps = _steps()
    for name in ("Check and stage the privacy-safe outputs",):
        condition = steps[name]["if"]
        assert "steps.recheck.outcome == 'success'" in condition
        assert "steps.recheck.outputs.moved == 'false'" in condition
        assert "inputs.pr_number == ''" in condition
    assert "steps.stage.outcome == 'success'" in steps["Upload report.txt and result.json"]["if"]
    summary_env = steps["Job summary"]["env"]
    assert summary_env["RECHECK"] == "${{ steps.recheck.outcome }}"


@pytest.mark.parametrize(
    "recheck, moved, outcome",
    [("success", "false", "PASS"), ("success", "true", "FAIL"), ("failure", "", "FAIL"),
     ("skipped", "", "FAIL"), ("success", "", "FAIL")],
)
def test_the_summary_never_says_pass_without_a_confirmed_head(recheck, moved, outcome):
    text = cloud.summary(
        baseline=SHA_A, candidate=SHA_B, pr="7", identity={"tag": "corpus-v1"}, documents=2,
        exit_code=0, result={"gate": {"verdict": "pass", "exit_code": 0}},
        recheck=recheck, pr_moved=moved,
    )
    assert f"| Outcome | {outcome} |" in text


def test_a_recheck_that_cannot_read_the_pull_request_fails_and_writes_nothing(monkeypatch, capsys):
    def http(url, token, **kwargs):
        raise cloud.urllib.error.HTTPError(url, 502, "bad gateway", {}, None)

    monkeypatch.setattr(cloud, "http", http)
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    code = cloud.main(["check-inputs", "--recheck", "--repository", "o/r", "--baseline", SHA_A,
                       "--candidate", SHA_B, "--pr", "7"])
    assert code == 3 and capsys.readouterr().out == ""


# --------------------------------------------------------------------------
# Finding 3: cleanup must prove Docker resources are gone
# --------------------------------------------------------------------------

_FAKE_DOCKER = r'''#!/usr/bin/env python3
"""A Docker CLI stand-in whose resources can refuse to go away."""
import json, os, sys
path = os.environ["FAKE_DOCKER_STATE"]
state = json.load(open(path))
args = sys.argv[1:]
log = state.setdefault("log", [])
log.append(args)
def save():
    json.dump(state, open(path, "w"))
def label_of(args):
    return next(a.split("=", 1)[1] for a in args if a.startswith("label="))
if state.get("daemon_down"):
    save(); sys.exit(1)
if args[:2] == ["ps", "-aq"]:
    want = label_of(args)
    print("\n".join(c for c, v in state["containers"].items() if want in v["labels"]))
elif args[:2] == ["rm", "--force"]:
    c = state["containers"].get(args[2])
    if c is None:
        save(); sys.exit(1)          # already gone
    if c.get("rm_fails"):
        save(); sys.exit(1)
    if not c.get("stuck"):
        del state["containers"][args[2]]
elif args[:3] == ["image", "rm", "--force"]:
    i = state["images"].get(args[3])
    if i is None:
        save(); sys.exit(1)
    if i.get("rm_fails"):
        save(); sys.exit(1)
    if not i.get("stuck"):
        del state["images"][args[3]]
elif args[:2] == ["image", "inspect"]:
    save(); sys.exit(0 if args[2] in state["images"] else 1)
elif args[:2] == ["images", "-q"]:
    want = label_of(args)
    print("\n".join(n for n, v in state["images"].items() if want in v.get("labels", [])))
else:
    save(); sys.exit(2)
save()
'''

LABEL = "losslift.corpus-gate=1"


@pytest.fixture()
def fake_docker(tmp_path, monkeypatch):
    script = tmp_path / "docker"
    script.write_text(_FAKE_DOCKER)
    script.chmod(0o755)
    state_path = tmp_path / "docker-state.json"

    def set_state(containers=None, images=None, **extra):
        state = {"containers": containers or {}, "images": images or {}, **extra}
        state_path.write_text(json.dumps(state))

    def state():
        return json.loads(state_path.read_text())

    monkeypatch.setenv("FAKE_DOCKER_STATE", str(state_path))
    set_state()
    return str(script), set_state, state


def _paths(tmp_path):
    gate = tmp_path / "gate"
    (gate / "corpus").mkdir(parents=True)
    (gate / "corpus" / "doc.pdf").write_bytes(b"%PDF")
    return [gate]


def test_cleanup_removes_every_labelled_container_and_the_image(tmp_path, fake_docker):
    docker, set_state, state = fake_docker
    set_state({"c1": {"labels": [LABEL]}, "c2": {"labels": [LABEL]}, "other": {"labels": []}},
              {"img": {"labels": [LABEL]}})
    assert cloud.cleanup(_paths(tmp_path), None, "img", docker=docker, require_docker=True) == []
    assert set(state()["containers"]) == {"other"} and state()["images"] == {}


@pytest.mark.parametrize(
    "containers, images, expected",
    [
        ({"c1": {"labels": [LABEL], "rm_fails": True}}, {"img": {"labels": [LABEL]}},
         "container"),
        ({"c1": {"labels": [LABEL], "stuck": True}}, {"img": {"labels": [LABEL]}}, "container"),
        ({}, {"img": {"labels": [LABEL], "rm_fails": True}}, "image"),
        ({}, {"img": {"labels": [LABEL], "stuck": True}}, "image"),
        ({}, {"img": {"labels": [], "stuck": True}}, "image"),
        ({}, {"other-img": {"labels": [LABEL], "stuck": True}}, "image"),
    ],
)
def test_cleanup_fails_when_a_resource_survives(tmp_path, fake_docker, containers, images, expected):
    docker, set_state, _ = fake_docker
    set_state(containers, images)
    paths = _paths(tmp_path)
    problems = cloud.cleanup(paths, None, "img", docker=docker, require_docker=True)
    assert any(expected in problem for problem in problems), problems
    assert not paths[0].exists(), "host paths must still be removed after a Docker failure"


def test_resources_already_removed_are_not_a_failure(tmp_path, fake_docker):
    docker, set_state, _ = fake_docker
    set_state({}, {})
    assert cloud.cleanup(_paths(tmp_path), None, "img", docker=docker, require_docker=True) == []


def test_one_failed_removal_does_not_stop_the_others(tmp_path, fake_docker):
    docker, set_state, state = fake_docker
    set_state({"c1": {"labels": [LABEL], "rm_fails": True}, "c2": {"labels": [LABEL]}},
              {"img": {"labels": [LABEL]}})
    problems = cloud.cleanup(_paths(tmp_path), None, "img", docker=docker, require_docker=True)
    assert problems and set(state()["containers"]) == {"c1"} and state()["images"] == {}


def test_cleanup_cannot_verify_without_a_reachable_docker(tmp_path, fake_docker):
    docker, set_state, _ = fake_docker
    set_state(daemon_down=True)
    assert cloud.cleanup(_paths(tmp_path), None, "img", docker=docker, require_docker=True)
    assert cloud.cleanup(_paths(tmp_path), None, "img", docker=str(tmp_path / "absent"),
                         require_docker=True)


def test_host_paths_alone_never_make_cleanup_succeed(tmp_path, fake_docker):
    docker, set_state, _ = fake_docker
    set_state({"c1": {"labels": [LABEL], "stuck": True}})
    code = cloud.main(["cleanup", "--path", str(tmp_path / "gate"), "--image", "img",
                       "--require-docker", "--docker", docker])
    assert code != 0


def test_the_workflow_cleanup_requires_docker_and_the_image_is_labelled():
    steps = _steps()
    assert "--require-docker" in steps["Remove documents, manifest, worktrees and containers"]["run"]
    assert f"--label {LABEL}" in steps["Build the sandbox image from main's requirements"]["run"]


# --------------------------------------------------------------------------
# Finding 1: numeric and structural exfiltration, end to end through the workflow
# --------------------------------------------------------------------------


def _docker_ready():
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode == 0


needs_docker = pytest.mark.skipif(not _docker_ready(), reason=f"needs Docker with {IMAGE}")

_LEAK = '''

_measured = run_pipeline
LEAK = "{leak}"


def _salts():
    """Everything in this process that could be the corpus salt."""
    import gc
    import sys
    found = []
    for obj in gc.get_objects():
        try:
            value = obj.__dict__.get("_salt") if hasattr(obj, "__dict__") else None
        except Exception:
            value = None
        if isinstance(value, (bytes, bytearray)):
            found.append(bytes(value))
    frame = sys._getframe()
    while frame is not None:
        for value in list(frame.f_locals.values()):
            if isinstance(value, (bytes, bytearray)) and len(value) == 32:
                found.append(bytes(value))
        frame = frame.f_back
    for path in ("/proc/1/environ", "/proc/1/cmdline", "/proc/self/environ"):
        try:
            found.append(open(path, "rb").read())
        except OSError:
            pass
    return found


def run_pipeline(source, **options):
    import sys
    result = _measured(source, **options)
    data = open(source, "rb").read()
    salts = _salts()
    salt = salts[0] if salts else b""
    document = result.document
    if LEAK == "bigint":
        document.page_count = int.from_bytes(data, "big")
        document.printed_claim_count = int.from_bytes(salt or b"\\0", "big")
    elif LEAK == "manyints":
        document.page_count = len(data)
        document.rows_seen_per_page = {{i + 1: byte for i, byte in enumerate(data)}}
        document.column_split_pages = [[i + 1, byte] for i, byte in enumerate(salt)]
        document.processed_pages = [i + 1 for i, byte in enumerate(data) if byte & 1]
    elif LEAK == "exception":
        name = "E_" + data[9:30].hex()
        raise type(name, (Exception,), {{}})()
    elif LEAK == "status":
        letters = "".join(chr(65 + (b >> 4)) + chr(65 + (b & 15)) for b in data)[:22]
        result.reconciliation.status = "Q" + letters
    elif LEAK == "fields":
        claim = document.claims[0]
        for i, byte in enumerate(data[:40]):
            setattr(claim, "b%d_%d" % (i, byte), None)
    return result
'''

LEAKS = ("bigint", "manyints", "exception", "status", "fields")


@pytest.fixture(scope="module")
def leak_world(tmp_path_factory):
    from tests import test_corpus_gate as gate_tests

    root = tmp_path_factory.mktemp("leak-world")
    repo = root / "repo"
    (repo / "core").mkdir(parents=True)
    (repo / "core" / "__init__.py").write_text("")
    (repo / ".gitignore").write_text("tools/\n")
    for args in (["init", "-q"], ["config", "user.email", "g@example.invalid"],
                 ["config", "user.name", "G"]):
        gate_tests._git(repo, *args)
    base = gate_tests._commit(repo, "same", "base")
    source = textwrap.dedent(gate_tests._FAKE_PIPELINE.format(
        behaviour="same", claimant=gate_tests.CLAIMANT, claim=gate_tests.CLAIM_NUMBER,
        description=gate_tests.DESCRIPTION))
    commits = {"base": base}
    for leak in LEAKS:
        (repo / "core" / "pipeline.py").write_text(source + _LEAK.format(leak=leak))
        gate_tests._git(repo, "add", "-A")
        gate_tests._git(repo, "commit", "-q", "-m", leak)
        commits[leak] = gate_tests._git(repo, "rev-parse", "HEAD")
    # The trusted gate, as the workflow checks it out, beside the history under test.
    shutil.copytree(REPO / "tools", repo / "tools",
                    ignore=shutil.ignore_patterns("__pycache__"))
    return {"repo": repo, "root": root, **commits}


def _documents(gate):
    pdfs = gate / "corpus" / "pdfs"
    pdfs.mkdir(parents=True)
    docs = [b"%PDF-1.4 Jane Q. Sentinelle claim SNTL-44718 paid 1250.00",
            b"%PDF-1.4 second sentinel packet, SNTL-99120, 2500.00"]
    for n, data in enumerate(docs):
        (pdfs / f"Sentinelle packet {n}.pdf").write_bytes(data)
    manifests.create(pdfs, gate / "corpus" / "manifest.json")
    salt = bytes.fromhex(json.loads((gate / "corpus" / "manifest.json").read_text())["digest_salt"])
    return docs, salt


def _encodings(docs, salt):
    """Every way the leaking candidates above could have written a secret."""
    texts = [salt.hex(), base64.b64encode(salt).decode(), str(int.from_bytes(salt, "big"))]
    for data in docs:
        texts += [data.hex(), data[9:30].hex(), str(int.from_bytes(data, "big")), "Sentinelle", "SNTL",
                  "".join(chr(65 + (b >> 4)) + chr(65 + (b & 15)) for b in data)[:12]]
    return texts


def _ints(text):
    """Every integer written, leaving out ids, digests, SHAs and hashes (hex with a letter)."""
    text = re.sub(r"(?<![0-9a-f])(?=[0-9a-f]*[a-f])[0-9a-f]{7,}(?![0-9a-f])", " ", text)
    return [int(match) for match in re.findall(r"\d+", text)]


def _contains_run(values, sequence, length=6):
    """Whether ``values`` holds ``length`` items of ``sequence`` in order, evenly spaced.

    Spacing covers keys interleaved with values, as a JSON map writes them.
    Every window of the sequence is tried, not only its start.
    """
    for offset in range(0, max(1, len(sequence) - length + 1)):
        needle = list(sequence[offset:offset + length])
        if len(needle) < length:
            return False
        for step in range(1, 5):
            for start in range(step):
                strided = values[start::step]
                if any(strided[i:i + length] == needle for i in range(len(strided) - length + 1)):
                    return True
    return False


@needs_docker
@pytest.mark.parametrize("leak", LEAKS)
def test_a_leaking_candidate_cannot_get_corpus_bytes_or_the_salt_into_the_artifacts(leak_world, tmp_path, leak):
    runner_temp = tmp_path / "runner"
    gate = runner_temp / "losslift-gate"
    upload = runner_temp / "losslift-gate-upload"
    for folder in (gate, gate / "tmp", gate / "home", upload):
        folder.mkdir(parents=True)
    docs, salt = _documents(gate)
    outputs = tmp_path / "github-output"
    env = {"GATE": str(gate), "UPLOAD": str(upload), "RUNNER_TEMP": str(runner_temp),
           "GITHUB_WORKSPACE": str(leak_world["root"]), "GITHUB_OUTPUT": str(outputs),
           "BASELINE": leak_world["base"], "CANDIDATE": leak_world[leak],
           "SANDBOX_IMAGE": IMAGE}
    ran = _run_step("Run the corpus gate (collectors sandboxed)", env, cwd=leak_world["repo"])
    assert ran.returncode == 0, ran.stderr
    code = re.search(r"code=(\d+)", outputs.read_text()).group(1)
    assert code != "0", "a candidate this different must not pass"
    staged = _run_step("Check and stage the privacy-safe outputs", env, cwd=leak_world["repo"])
    uploaded = "".join(path.read_text() for path in sorted(upload.glob("*")))
    if staged.returncode != 0:
        assert uploaded == "", "a refused output must not be staged"
        return
    assert sorted(p.name for p in upload.iterdir()) == ["report.txt", "result.json"]
    for secret in _encodings(docs, salt):
        assert secret not in uploaded, f"{leak}: an encoding of a secret reached the artifacts"
    values = _ints(uploaded)
    assert all(value < 10**7 for value in values), f"{leak}: an unbounded integer reached the artifacts: {[v for v in values if v >= 10**7][:2]}"
    for data in docs:
        assert not _contains_run(values, list(data)), f"{leak}: document bytes reached the artifacts"
        assert not _contains_run(values, [i + 1 for i, b in enumerate(data) if b & 1])
    assert not _contains_run(values, list(salt)), f"{leak}: salt bytes reached the artifacts"
    result = json.loads((upload / "result.json").read_text())
    for document in result["documents"].values():
        assert "baseline" not in document and "candidate" not in document, "raw measurements uploaded"
    shutil.rmtree(gate / "out", ignore_errors=True)


# --------------------------------------------------------------------------
# Finding 1: the schema itself, attacked field by field
# --------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from tools.corpus_gate import collect, seal  # noqa: E402
from tools.corpus_gate import compare as comparison  # noqa: E402
from tools.corpus_gate.manifest import Entry, Manifest  # noqa: E402

SALT = bytes.fromhex("42" * 32)
DOC = "doc-0123456789ab"


def _result():
    """A pipeline result touching every measured group, sentinel data throughout."""
    def row(page):
        return SimpleNamespace(page=page, text="Jane Q. Sentinelle 1250.00")

    claims = [SimpleNamespace(claim_number=f"SNTL-{n}", claimant_name="Jane Q. Sentinelle",
                              incurred_total="1250.00", source_page=1 + n % 2) for n in range(3)]
    finding = SimpleNamespace(rule_id="R-01", severity="ERROR", category="financial", scope="claim",
                              subject="SNTL-1", condition="c", field=None, page=1,
                              claim_number="SNTL-1", related_rows=(), message="Jane", expected=1,
                              actual=2, delta=1, run_id="run-2")

    def run(run_id, pages):
        return SimpleNamespace(
            run_id=run_id, pages=pages, confidence="printed", ambiguous=run_id == "run-2",
            incomplete=None, printed_totals={"incurred_total": "1250.00"}, printed_claim_count=1,
            evidence=["Jane's letterhead"], carrier="Sentinel Mutual", named_insured="Jane",
            policy_number="SNTL-POL", valuation_date="2022-12-31")
    document = SimpleNamespace(
        claims=claims, processed_pages=[1, 2], failed_pages=[], skipped_pages=[3],
        unresolved_pages=[2], scanned_pages=[], unresolved_reasons={2: "Jane's page"}, page_count=3,
        column_split_pages=[(1, 312)], rows_seen_per_page={1: 3, 2: 0}, unplaced_rows=[row(2)],
        printed_totals={"incurred_total": "3750.00"}, unreadable_totals=["x"],
        unreadable_totals_page=3, printed_count_evidence=["3 claims"], printed_sections=[
            SimpleNamespace(printed_claim_count=3)],
        printed_claim_count=3, extraction_method="digital", carrier="Sentinel Mutual",
        runs=[run("run-1", [1]), run("run-2", [2, 3])],
        refused_claim_rows=[SimpleNamespace(page=2, identifier="SNTL-9 Jane", report=True)],
    )
    return SimpleNamespace(document=document, warnings=["page 2 mentions Jane"],
                           reconciliation=SimpleNamespace(
                               status="NEEDS_REVIEW", findings=[finding],
                               run_status={"run-1": "CLEAN", "run-2": "NEEDS_REVIEW"}))


def _measure(digest):
    """``collect.measure`` over ``_result``, with a summary group present.

    The real summary needs a real document; this one is what it measures.
    """
    original = collect._summary
    collect._summary = lambda document, d: {
        "periods": 1, "claim_counts": [3], "total_claims": 3, "open_claims": [1],
        "closed_claims": [2], "printed_claims": [3], "ties": [True], "digest": d(["period 1"]),
    }
    try:
        return collect.measure(_result(), digest)
    finally:
        collect._summary = original


def _raw_record():
    return {"kind": "document", "id": DOC, "ok": True,
            "metrics": _measure(collect.RawDigest())}


def _manifest():
    return Manifest(salt=SALT, entries=(Entry(DOC, "a.pdf", "c" * 64, 1),), sha256="d" * 64)


def _read(tmp_path, lines, *, raw_lines=None):
    path = tmp_path / "measurements.jsonl"
    body = [json.dumps({"kind": "header", "schema": 1, "isolated": True, "python": "3.11.0",
                        "platform": "linux"})]
    body += raw_lines if raw_lines is not None else [json.dumps(line) for line in lines]
    body += [json.dumps({"kind": "complete", "documents": 1})]
    path.write_text("\n".join(body) + "\n")
    run = comparison.RevisionRun("candidate", SHA_B)
    return seal.read_run(path, run, _manifest())


def _rejected(run):
    return run.process == seal.REJECTED and run.records == {} and not run.complete


def test_sealing_outside_the_collector_gives_the_same_digests_as_the_salt_inside(tmp_path):
    sealed = _read(tmp_path, [_raw_record()]).records[DOC]["metrics"]
    assert sealed == _measure(collect.Digest(SALT))


def test_the_real_pipeline_seals_to_the_same_measurements(tmp_path):
    from core.pipeline import run_pipeline
    from tests.golden.fixtures import ALL_FIXTURES
    from tests.golden.generate import render

    fixture = next(item for item in ALL_FIXTURES if item.name == "us_basic")
    result = run_pipeline(render(fixture, tmp_path / "us_basic.pdf"), use_vision=False)
    record = {"kind": "document", "id": DOC, "ok": True,
              "metrics": collect.measure(result, collect.RawDigest())}
    sealed = _read(tmp_path, [record]).records[DOC]["metrics"]
    assert sealed == collect.measure(result, collect.Digest(SALT))


def _leaves(value, path=()):
    if isinstance(value, dict):
        yield path, value
        for key, item in value.items():
            yield from _leaves(item, (*path, key))
    elif isinstance(value, list):
        yield path, value
        for index, item in enumerate(value):
            yield from _leaves(item, (*path, index))
    else:
        yield path, value


def _set(record, path, value):
    target = record
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = value


def _mutations():
    """Every integer, boolean, digest slot, list and object in a real record, attacked."""
    base = _raw_record()
    for path, value in _leaves(base):
        if not path or path[0] != "metrics":
            continue
        if type(value) is int:
            for bad in (10**7, -1, True, 1.5, "12", None if path[-1] not in ("claim_count",) else [],
                        [value]):
                if bad is None:
                    continue
                yield f"{'.'.join(map(str, path))}={bad!r}", path, bad
        elif type(value) is bool:
            for bad in (1, 0, "true"):
                yield f"{'.'.join(map(str, path))}={bad!r}", path, bad
        elif isinstance(value, str) and value.startswith(collect.RAW_PREFIX):
            for bad in ("h:" + "0123456789abcdef" * 2, "Jane Q. Sentinelle", 7, [value],
                        "r:not base64 !"):
                yield f"{'.'.join(map(str, path))}={bad!r}", path, bad
        elif isinstance(value, dict):
            yield f"{'.'.join(map(str, path))}+unknown", (*path, "Unknown Key!"), 0
            yield f"{'.'.join(map(str, path))}=list", path, list(value.values())


MUTATIONS = list(_mutations())


def test_the_mutation_set_covers_every_group():
    groups = {case[1][1] for case in MUTATIONS if len(case[1]) > 1}
    assert groups >= set(seal.GROUPS) - {"status", "extraction_method", "review_status"}
    assert len(MUTATIONS) > 150


@pytest.mark.parametrize("name, path, bad", MUTATIONS, ids=[case[0] for case in MUTATIONS])
def test_every_field_of_the_wrong_type_range_or_shape_rejects_the_run(tmp_path, name, path, bad):
    record = _raw_record()
    if path[-1] == "Unknown Key!":
        target = record
        for step in path[:-1]:
            target = target[step]
        target["Unknown Key!"] = bad
    else:
        _set(record, path, bad)
    # Compared as JSON: True == 1 in Python, and that is exactly the attack.
    assert json.dumps(record, sort_keys=True) != json.dumps(_raw_record(), sort_keys=True), name
    assert _rejected(_read(tmp_path, [record])), name


@pytest.mark.parametrize(
    "text",
    [
        '"claim_count": NaN', '"claim_count": Infinity', '"claim_count": -Infinity',
        '"claim_count": 1e3', '"claim_count": 3.0', '"claim_count": 123456789012345678901234567890',
        '"claim_count": 3, "claim_count": 4',
    ],
)
def test_non_finite_fractional_huge_and_repeated_numbers_are_refused(tmp_path, text):
    line = json.dumps(_raw_record()).replace('"claim_count": 3', text, 1)
    assert text in line
    assert _rejected(_read(tmp_path, [], raw_lines=[line]))


@pytest.mark.parametrize("builder", [
    lambda r: r["metrics"]["pages"].update(processed_pages=list(range(1, seal.MAX_LIST + 2))),
    lambda r: r["metrics"]["pages"].update(processed_pages=[2, 1]),
    lambda r: r["metrics"]["pages"].update(processed_pages=[seal.MAX_PAGE + 1]),
    lambda r: r["metrics"]["pages"].update(rows_seen_per_page={str(i): 1 for i in range(seal.MAX_LIST + 1)}),
    lambda r: r["metrics"]["pages"].update(rows_seen_per_page={"01": 1}),
    lambda r: r["metrics"]["pages"].update(rows_seen_per_page={"Jane": 1}),
    lambda r: r["metrics"]["findings"].update(by_rule={f"R-{i:03d}": 1 for i in range(seal.MAX_NAMED_KEYS + 1)}),
    lambda r: r["metrics"]["findings"].update(by_rule_severity={"R-01/ERROR/X": 1}),
    lambda r: r["metrics"]["claims"].update(fields={"a" * 49: r["metrics"]["claims"]["digest"]}),
    lambda r: r["metrics"]["pages"].update(column_split_pages=[[1, 2, 3]]),
    lambda r: r["metrics"]["metadata"].update(carrier="Sentinel Mutual"),
    lambda r: r["metrics"].update(summary={"periods": 1}),
    lambda r: r["metrics"]["pages"].update(unresolved_reasons=functools_reduce_nest(200)),
])
def test_oversized_nested_or_out_of_vocabulary_structure_is_refused(tmp_path, builder):
    record = _raw_record()
    builder(record)
    assert _rejected(_read(tmp_path, [record]))


def functools_reduce_nest(depth):
    value = "r:"
    for _ in range(depth):
        value = [value]
    return {"1": value}


def test_deep_nesting_in_the_raw_text_is_refused(tmp_path):
    line = json.dumps(_raw_record()).replace('"claim_count": 3', '"claim_count": ' + "[" * 100_000 + "]" * 100_000, 1)
    assert _rejected(_read(tmp_path, [], raw_lines=[line]))


@pytest.mark.parametrize("record", [
    {"kind": "document", "id": DOC, "ok": True, "metrics": {}, "seconds": 1},
    {"kind": "document", "id": DOC, "ok": False, "error_type": "ValueError", "seconds": 1.5},
    {"kind": "document", "id": DOC, "ok": 1, "error_type": "ValueError"},
    {"kind": "document", "id": "doc-ffffffffffff", "ok": False, "error_type": "ValueError"},
    {"kind": "document", "id": DOC, "ok": False, "error_type": "ValueError", "unmeasured": "jane"},
    {"kind": "surprise"},
    {"kind": "fatal", "error_type": "ValueError", "note": "Jane"},
])
def test_records_outside_the_schema_including_timing_are_refused(tmp_path, record):
    assert _rejected(_read(tmp_path, [record]))


def test_a_repeated_document_or_a_wrong_completion_count_is_refused(tmp_path):
    ok = {"kind": "document", "id": DOC, "ok": False, "error_type": "ValueError"}
    assert _rejected(_read(tmp_path, [ok, ok]))
    path = tmp_path / "m2.jsonl"
    path.write_text(json.dumps({"kind": "header", "schema": 1, "isolated": True, "python": "3",
                                "platform": "linux"}) + "\n" + json.dumps(ok) + "\n"
                    + json.dumps({"kind": "complete", "documents": 2}) + "\n")
    assert _rejected(seal.read_run(path, comparison.RevisionRun("c", SHA_B), _manifest()))


@pytest.mark.parametrize("name, sealed", [
    ("ValueError", "ValueError"), ("KeyError", "KeyError"), ("Jane_Sentinelle", "UnlistedError"),
    ("E_2550444620", "UnlistedError"), ("PdfminerException", "UnlistedError"), (7, "UnlistedError"),
])
def test_exception_names_are_builtin_or_unlisted(tmp_path, name, sealed):
    record = {"kind": "document", "id": DOC, "ok": False, "error_type": name}
    run = _read(tmp_path, [record])
    assert run.records[DOC]["error_type"] == sealed
    fatal = tmp_path / "f.jsonl"
    fatal.write_text(json.dumps({"kind": "fatal", "error_type": name}) + "\n")
    assert seal.read_run(fatal, comparison.RevisionRun("c", SHA_B), _manifest()).fatal == sealed


def _outcome(baseline, candidate):
    manifest = _manifest()
    runs = []
    for label, metrics in (("baseline", baseline), ("candidate", candidate)):
        run = comparison.RevisionRun(label, SHA_A if label == "baseline" else SHA_B)
        run.records[DOC] = {"kind": "document", "id": DOC, "ok": True, "metrics": metrics}
        run.complete = True
        runs.append(run)
    return comparison.compare(manifest, *runs), manifest


def test_the_published_result_carries_no_measured_value_but_claim_count_and_status():
    base = _measure(collect.Digest(SALT))
    leak = json.loads(json.dumps(base))
    data = b"Jane Q. Sentinelle SNTL-44718 1250.00"
    leak["pages"]["page_count"] = len(data)
    leak["pages"]["rows_seen_per_page"] = {str(i + 1): b for i, b in enumerate(data)}
    leak["pages"]["processed_pages"] = [i + 1 for i, b in enumerate(data) if b & 1]
    leak["pages"]["column_split_pages"] = [[i + 1, b] for i, b in enumerate(data)]
    leak["findings"]["by_rule"] = {f"R-{b:03d}": i for i, b in enumerate(data[:40])}
    leak["claims"]["null_counts"] = {f"f{i}": b for i, b in enumerate(data)}
    leak["summary"] = None
    leak["claim_count"] = 7
    outcome, manifest = _outcome(base, leak)
    published = seal.public_result(outcome, manifest, 1, 0)
    seal.validate_public(published)
    text = json.dumps(published) + seal.render_public(published)
    values = _ints(text)
    assert not _contains_run(values, list(data))
    assert "R-083" not in text and "f12" not in text
    document = published["documents"][DOC]
    assert document["claim_count"] == {"baseline": 3, "candidate": 7}
    assert set(document["changed"]) <= set(seal.KNOWN_PATHS)
    assert "baseline" not in document and "candidate" not in document
    assert "seconds" not in text


@pytest.mark.parametrize("value, shown", [
    ("NEEDS_REVIEW", "NEEDS_REVIEW"), ("JANE_SENTINELLE", "unlisted"), ("QFEGHDACEB", "unlisted"),
])
def test_only_known_statuses_are_published(value, shown):
    base = _measure(collect.Digest(SALT))
    changed = dict(base, status=value)
    base = dict(base, status="CLEAN")
    outcome, manifest = _outcome(base, changed)
    assert seal.public_result(outcome, manifest, 1, 0)["documents"][DOC]["status"]["candidate"] == shown


def _valid_public():
    base = _measure(collect.Digest(SALT))
    outcome, manifest = _outcome(base, dict(base, claim_count=4))
    return seal.public_result(outcome, manifest, 1, 0)


def _public_mutations():
    base = _valid_public()
    for path, value in _leaves(base):
        if type(value) is int:
            yield path, 10**7
            yield path, True
            yield path, 1.5
        elif type(value) is bool:
            yield path, 1
        elif isinstance(value, dict) and path:
            yield (*path, "extra"), "x"
        elif isinstance(value, list):
            yield path, [*value, 10**6] if path[-1] != "problems" else ["Janeé"]
    yield ("revisions", "candidate", "seconds"), 3.3
    yield ("documents", DOC, "baseline"), {"claim_count": 3}
    yield ("documents", DOC, "changed"), ["pages.rows_seen_per_page.17"]
    yield ("documents", DOC, "claim_count", "candidate"), 10**6 + 1
    yield ("revisions", "candidate", "fatal"), "Jane_Sentinelle"
    yield ("revisions", "candidate", "process"), "exited with code 4 Jane"


PUBLIC_MUTATIONS = list(_public_mutations())


@pytest.mark.parametrize("path, bad", PUBLIC_MUTATIONS, ids=[".".join(map(str, p)) for p, _ in PUBLIC_MUTATIONS])
def test_staging_refuses_any_result_outside_the_public_schema(tmp_path, path, bad):
    result = _valid_public()
    target = result
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = bad
    out = tmp_path / "out"
    out.mkdir()
    (out / "result.json").write_text(json.dumps(result))
    with pytest.raises(cloud.CloudError):
        cloud.stage_outputs(out, tmp_path / "up", None, [])
    assert not (tmp_path / "up").exists()


def test_a_valid_public_result_is_staged_and_its_report_rebuilt(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "result.json").write_text(json.dumps(_valid_public()))
    (out / "report.txt").write_text("99999999 Jane\n")
    cloud.stage_outputs(out, tmp_path / "up", None, [])
    assert "Jane" not in (tmp_path / "up" / "report.txt").read_text()
    assert "claim_count: 3 -> 4" in (tmp_path / "up" / "report.txt").read_text()


# --------------------------------------------------------------------------
# Finding 1: the collector process never holds the salt
# --------------------------------------------------------------------------


def test_the_collector_is_told_raw_and_never_the_salt(leak_world, tmp_path, monkeypatch):
    from tools.corpus_gate import runner

    sent = []
    real = subprocess.Popen

    class Spy(real):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            if self.stdin is not None:
                write = self.stdin.write
                self.stdin.write = lambda text: (sent.append(text), write(text))[1]

    monkeypatch.setattr(subprocess, "Popen", Spy)
    gate = tmp_path / "g"
    docs, salt = _documents(gate)
    runner.run_gate(repo=leak_world["repo"], baseline=leak_world["base"],
                    candidate=leak_world["base"], corpus=gate / "corpus" / "pdfs",
                    manifest_path=gate / "corpus" / "manifest.json", out_dir=tmp_path / "out")
    assert sent == ["raw\n", "raw\n"]
    assert not any(salt.hex() in text for text in sent)


def test_a_raw_mode_collector_holds_no_salt_to_find(tmp_path, monkeypatch):
    import gc

    import core.pipeline

    found = []

    def snoop(source, **_):
        for obj in gc.get_objects():
            if isinstance(getattr(obj, "__dict__", None), dict) and "_salt" in obj.__dict__:
                found.append(obj)
        frame = sys._getframe()
        while frame is not None:
            found += [v for v in frame.f_locals.values() if isinstance(v, bytes) and len(v) == 32]
            frame = frame.f_back
        return _result()

    monkeypatch.setattr(core.pipeline, "run_pipeline", snoop)
    documents = tmp_path / "documents.json"
    documents.write_text(json.dumps([{"id": DOC, "path": str(tmp_path / "a.pdf")}]))
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("raw\n"))
    code = collect.main(["--root", str(REPO), "--documents", str(documents), "--out",
                         str(tmp_path / "out.jsonl")])
    assert code == 0 and found == []
    assert '"h:' not in (tmp_path / "out.jsonl").read_text()


_SALT_PROBE = '''

_measured = run_pipeline
# Masked, so this file and this module do not themselves hold the salt.
SALT_MASKED = "{masked}"


def run_pipeline(source, **options):
    """Search everything this sandbox can reach for the corpus salt."""
    import gc
    import os
    import sys
    salt = bytes(b ^ 0x5A for b in bytes.fromhex(SALT_MASKED))
    needles = (salt, salt.hex().encode())
    places = []
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            for name in ("environ", "cmdline"):
                try:
                    places.append(open("/proc/%s/%s" % (pid, name), "rb").read())
                except OSError:
                    pass
    try:
        places.append(sys.stdin.buffer.read() if not sys.stdin.closed else b"")
    except (OSError, ValueError):
        pass
    for root in ("/", "/tmp"):
        for folder, _dirs, files in os.walk(root):
            if folder.startswith(("/proc", "/sys", "/dev", "/usr", "/lib", "/bin", "/sbin", "/etc")):
                continue
            for name in files:
                try:
                    with open(os.path.join(folder, name), "rb") as handle:
                        places.append(handle.read(1 << 20))
                except OSError:
                    pass
    for obj in gc.get_objects():
        if isinstance(obj, (bytes, bytearray, str)):
            places.append(obj.encode() if isinstance(obj, str) else bytes(obj))
        attributes = getattr(obj, "__dict__", None)
        if isinstance(attributes, dict):
            places += [v for v in attributes.values() if isinstance(v, bytes)]
    frame = sys._getframe().f_back  # not this frame: it holds the needles
    while frame is not None:
        places += [v for v in frame.f_locals.values() if isinstance(v, bytes)]
        frame = frame.f_back
    if any(needle in place for place in places for needle in needles):
        raise PermissionError("salt reachable")
    return _measured(source, **options)
'''


@needs_docker
@pytest.mark.parametrize("protocol", ["current", "salt-on-stdin control"])
def test_the_salt_is_nowhere_a_sandboxed_candidate_can_look(leak_world, tmp_path, monkeypatch, protocol):
    from tests import test_corpus_gate as gate_tests
    from tools.corpus_gate import runner
    from tools.corpus_gate.sandbox import DockerSandbox

    gate = tmp_path / "g"
    docs, salt = _documents(gate)
    repo = leak_world["repo"]
    source = textwrap.dedent(gate_tests._FAKE_PIPELINE.format(
        behaviour="same", claimant=gate_tests.CLAIMANT, claim=gate_tests.CLAIM_NUMBER,
        description=gate_tests.DESCRIPTION))
    masked = bytes(b ^ 0x5A for b in salt).hex()
    (repo / "core" / "pipeline.py").write_text(source + _SALT_PROBE.format(masked=masked))
    gate_tests._git(repo, "add", "core")
    gate_tests._git(repo, "commit", "-q", "-m", "salt probe")
    probe = gate_tests._git(repo, "rev-parse", "HEAD")
    gate_tests._git(repo, "checkout", "-q", leak_world["base"], "--", "core")
    if protocol != "current":
        # The protocol before the fix: the probe must find the salt, or it proves nothing.
        monkeypatch.setattr(runner, "collect_raw_mode", lambda: salt.hex())
    outcome = runner.run_gate(repo=repo, baseline=leak_world["base"], candidate=probe,
                              corpus=gate / "corpus" / "pdfs",
                              manifest_path=gate / "corpus" / "manifest.json",
                              out_dir=tmp_path / "out", sandbox=DockerSandbox(IMAGE), public=True)
    if protocol == "current":
        assert outcome.exit_code == 0, outcome.report
    else:
        assert outcome.exit_code == 4 and "PermissionError" in outcome.report


@pytest.mark.parametrize("kind", ["symlink", "fifo", "directory"])
def test_an_output_that_is_not_a_regular_file_is_refused_unread(tmp_path, kind):
    path = tmp_path / "measurements.jsonl"
    host_file = tmp_path / "host-secret"
    host_file.write_text(json.dumps({"kind": "complete", "documents": 1}) + "\n")
    if kind == "symlink":
        path.symlink_to(host_file)
    elif kind == "fifo":
        os.mkfifo(path)  # reading it would block the gate forever
    else:
        path.mkdir()
    run = seal.read_run(path, comparison.RevisionRun("candidate", SHA_B), _manifest())
    assert _rejected(run)


def test_an_oversized_output_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(seal, "MAX_FILE", 1000)
    path = tmp_path / "measurements.jsonl"
    path.write_text("x" * 1001)
    assert _rejected(seal.read_run(path, comparison.RevisionRun("c", SHA_B), _manifest()))
