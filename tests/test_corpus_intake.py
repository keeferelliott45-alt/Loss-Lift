"""Growing the private corpus from the browser: propose, approve, publish.

Every archive here is synthetic. File names carry a sentinel so any report
that names a file is caught.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from tools.corpus_gate import cloud, intake
from tools.corpus_gate import manifest as manifests
from tools.corpus_gate.cloud import CloudError

SENTINEL = "Maine Sentinel"


def _pdf(label: str) -> bytes:
    return f"%PDF-1.7 synthetic {label}\n%%EOF\n".encode()


def _zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


@pytest.fixture()
def active(tmp_path):
    """corpus-v1: two approved documents, unpacked, with the release identity."""
    src = tmp_path / "v1"
    (src / "pdfs").mkdir(parents=True)
    (src / "pdfs" / f"{SENTINEL} old A.pdf").write_bytes(_pdf("old A"))
    (src / "pdfs" / f"{SENTINEL} old B.pdf").write_bytes(_pdf("old B"))
    manifests.create(src / "pdfs", src / "manifest.json")
    entries = {"manifest.json": (src / "manifest.json").read_bytes()}
    for path in sorted((src / "pdfs").iterdir()):
        entries[f"pdfs/{path.name}"] = path.read_bytes()
    archive = _zip(tmp_path / "v1.zip", entries)
    corpus = cloud.unpack_corpus(archive, tmp_path / "active", "corpus-v1")
    identity = {"tag": "corpus-v1", "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
    return corpus, identity


def _intake(tmp_path, entries, name="intake.zip"):
    path = _zip(tmp_path / name, entries)
    return path, {"tag": "intake-1", "archive_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


MAINE = {
    f"Real test loss run reports/{SENTINEL} complete packet.pdf": _pdf("maine complete"),
    f"Real test loss run reports/{SENTINEL} pages 1-40.pdf": _pdf("maine 1-40"),
    f"Real test loss run reports/{SENTINEL} pages 41-80.pdf": _pdf("maine 41-80"),
    f"Real test loss run reports/{SENTINEL} pages 81-120.pdf": _pdf("maine 81-120"),
    f"Real test loss run reports/{SENTINEL} pages 121-160.pdf": _pdf("maine 121-160"),
    "Real test loss run reports/Buffalo Sentinel.pdf": _pdf("buffalo"),
    "Real test loss run reports/San Leandro Sentinel.xlsx": b"PK\x03\x04 spreadsheet",
}


def _id(data: bytes) -> str:
    return "doc-" + hashlib.sha256(data).hexdigest()[:12]


def _build(tmp_path, active, entries, approve, new_tag="corpus-v2", digest=None):
    corpus, identity = active
    archive, intake_identity = _intake(tmp_path, entries)
    proposal = intake.propose(corpus, identity, archive, intake_identity)
    return proposal, intake.build(
        corpus=corpus, proposal=proposal, intake_zip=archive,
        review_digest=digest or proposal.digest, approved=approve, new_tag=new_tag,
        work=tmp_path / "work",
    )


# --------------------------------------------------------------------------
# Proposals
# --------------------------------------------------------------------------


def test_the_intake_is_classified_and_nothing_is_admitted(tmp_path, active):
    corpus, identity = active
    archive, intake_identity = _intake(tmp_path, MAINE)
    proposal = intake.propose(corpus, identity, archive, intake_identity)
    classes = [item.cls for item in proposal.items]
    assert classes.count("candidate") == 6 and classes.count("unsupported") == 1
    assert set(proposal.candidates) == {_id(data) for name, data in MAINE.items() if name.endswith(".pdf")}
    assert proposal.intake["files"] == 7
    unsupported = next(item for item in proposal.items if item.cls == "unsupported")
    assert unsupported.extension == "xlsx" and unsupported.sha256 is None
    # Proposing changes nothing in the active corpus.
    assert manifests.load(corpus.manifest).sha256 == corpus.manifest_sha256


def test_the_proposal_names_no_file_and_carries_no_salt(tmp_path, active):
    corpus, identity = active
    archive, intake_identity = _intake(tmp_path, MAINE)
    report = intake.render(intake.propose(corpus, identity, archive, intake_identity))
    salt = json.loads(corpus.manifest.read_text())["digest_salt"]
    for secret in (SENTINEL, "Buffalo", "San Leandro", "Real test loss run reports", salt):
        assert secret not in report
    assert ".xlsx is outside the PDF gate" in report and "Nothing has been admitted" in report


def test_an_intake_copy_of_an_approved_document_is_already_approved(tmp_path, active):
    corpus, identity = active
    old = (corpus.documents / f"{SENTINEL} old A.pdf").read_bytes()
    archive, intake_identity = _intake(tmp_path, {f"pdfs/{SENTINEL} old A.pdf": old, "new.pdf": _pdf("n")})
    proposal = intake.propose(corpus, identity, archive, intake_identity)
    assert [item.cls for item in proposal.items] == ["already-approved", "candidate"]
    assert proposal.items[0].id == manifests.load(corpus.manifest).entries[0].id


def test_a_changed_approved_document_blocks_the_update(tmp_path, active):
    corpus, identity = active
    archive, intake_identity = _intake(tmp_path, {f"pdfs/{SENTINEL} old B.pdf": _pdf("edited"),
                                                  "new.pdf": _pdf("n")})
    proposal = intake.propose(corpus, identity, archive, intake_identity)
    assert proposal.blocked and proposal.items[0].cls == "changed-approved"
    assert "BLOCKED" in intake.render(proposal)
    with pytest.raises(CloudError, match="blocked"):
        intake.build(corpus=corpus, proposal=proposal, intake_zip=archive,
                     review_digest=proposal.digest, approved=[_id(_pdf("n"))],
                     new_tag="corpus-v2", work=tmp_path / "w")


def test_duplicate_pdfs_are_reported_and_never_admitted_twice(tmp_path, active):
    corpus, identity = active
    data = _pdf("same bytes")
    archive, intake_identity = _intake(tmp_path, {"a.pdf": data, "b/c.pdf": data, "d.PDF": data})
    proposal = intake.propose(corpus, identity, archive, intake_identity)
    assert [item.cls for item in proposal.items] == ["candidate", "duplicate", "duplicate"]
    assert proposal.items[1].same_as == "intake #1" and proposal.items[1].id is None
    _, (_, new) = _build(tmp_path, active, {"a.pdf": data, "b/c.pdf": data, "d.PDF": data}, [_id(data)])
    shas = [entry["sha256"] for entry in new["documents"]]
    assert len(shas) == len(set(shas)) == 3


@pytest.mark.parametrize("name, data, cls", [
    ("claims.xlsx", b"PK", "unsupported"), ("notes.txt", b"x", "unsupported"),
    ("scan.PNG", b"x", "unsupported"), ("noextension", b"%PDF-x", "unsupported"),
    ("fake.pdf", b"<html>not a pdf", "not-a-pdf"), ("empty.pdf", b"", "not-a-pdf"),
])
def test_unsupported_files_are_excluded_from_the_pdf_gate(tmp_path, active, name, data, cls):
    corpus, identity = active
    archive, intake_identity = _intake(tmp_path, {name: data})
    item = intake.propose(corpus, identity, archive, intake_identity).items[0]
    assert item.cls == cls and item.id is None


def test_an_unsafe_intake_archive_is_refused(tmp_path, active):
    corpus, identity = active
    archive, intake_identity = _intake(tmp_path, {"../escape.pdf": _pdf("x")})
    with pytest.raises(CloudError, match="climbs out"):
        intake.propose(corpus, identity, archive, intake_identity)


# --------------------------------------------------------------------------
# Approval and the new manifest
# --------------------------------------------------------------------------


def test_the_salt_and_every_existing_entry_are_preserved(tmp_path, active):
    corpus, _ = active
    old = json.loads(corpus.manifest.read_text())
    complete = MAINE[f"Real test loss run reports/{SENTINEL} complete packet.pdf"]
    _, (archive, new) = _build(tmp_path, active, MAINE, [_id(complete)])
    assert new["digest_salt"] == old["digest_salt"]
    assert new["documents"][:2] == old["documents"]
    assert new["documents"][2] == {"id": _id(complete), "path": f"added/{_id(complete)}.pdf",
                                   "sha256": hashlib.sha256(complete).hexdigest(), "bytes": len(complete)}
    assert new["release"] == "corpus-v2" and new["previous_release"] == "corpus-v1"
    assert new["previous_manifest_sha256"] == corpus.manifest_sha256
    # The published archive is itself a valid corpus bound to its release.
    again = cloud.unpack_corpus(archive, tmp_path / "check", "corpus-v2")
    assert again.count == 3 and again.bound_release == "corpus-v2"
    with zipfile.ZipFile(archive) as z:
        assert not any(SENTINEL in name for name in z.namelist() if name.startswith("pdfs/added/"))


def test_unapproved_additions_stay_out(tmp_path, active):
    complete = MAINE[f"Real test loss run reports/{SENTINEL} complete packet.pdf"]
    buffalo = MAINE["Real test loss run reports/Buffalo Sentinel.pdf"]
    _, (_, new) = _build(tmp_path, active, MAINE, [_id(complete), _id(buffalo)])
    added = {entry["id"] for entry in new["documents"][2:]}
    assert added == {_id(complete), _id(buffalo)}
    for name, data in MAINE.items():
        if "pages" in name:
            assert _id(data) not in added  # the page-range transport copies


@pytest.mark.parametrize("approve, words", [
    (["doc-000000000000"], "not candidates"),
])
def test_only_proposed_candidates_can_be_approved(tmp_path, active, approve, words):
    with pytest.raises(CloudError, match=words):
        _build(tmp_path, active, MAINE, approve)


def test_an_existing_duplicate_or_excluded_file_cannot_be_approved(tmp_path, active):
    corpus, identity = active
    old = manifests.load(corpus.manifest).entries[0]
    data = _pdf("d")
    with pytest.raises(CloudError, match="not candidates"):
        _build(tmp_path, active, {"a.pdf": data, "b.pdf": data}, [old.id])


@pytest.mark.parametrize("text, words", [
    ("", "no document was approved"), (" , ", "no document was approved"),
    ("doc-abc,doc-abc", "listed twice"), ("../x", "not a document id"),
])
def test_approval_text_is_explicit(text, words):
    with pytest.raises(CloudError, match=words):
        intake.parse_approvals(text)


def test_approval_is_bound_to_the_reviewed_proposal(tmp_path, active):
    complete = MAINE[f"Real test loss run reports/{SENTINEL} complete packet.pdf"]
    with pytest.raises(CloudError, match="review digest does not match"):
        _build(tmp_path, active, MAINE, [_id(complete)], digest="0" * 64)
    # A digest reviewed for one intake does not approve another.
    corpus, identity = active
    first, first_identity = _intake(tmp_path, MAINE, "first.zip")
    reviewed = intake.propose(corpus, identity, first, first_identity).digest
    changed = dict(MAINE, **{"extra.pdf": _pdf("slipped in later")})
    with pytest.raises(CloudError, match="review digest does not match"):
        _build(tmp_path, active, changed, [_id(complete)], digest=reviewed)


@pytest.mark.parametrize("new_tag, words", [
    ("corpus-v1", "differ"), ("corpus-v0", "look like"), ("v2", "look like"),
    ("corpus-v2-final", "look like"), ("", "look like"),
])
def test_the_new_release_is_a_higher_version(tmp_path, active, new_tag, words):
    complete = MAINE[f"Real test loss run reports/{SENTINEL} complete packet.pdf"]
    with pytest.raises(CloudError, match=words):
        _build(tmp_path, active, MAINE, [_id(complete)], new_tag=new_tag)


def test_a_lower_version_than_the_active_one_is_refused():
    with pytest.raises(CloudError, match="higher version"):
        intake.check_new_tag("corpus-v3", "corpus-v4")
    assert intake.check_new_tag("corpus-v10", "corpus-v9") == "corpus-v10"
    assert intake.check_new_tag("corpus-v2", "my-first-corpus") == "corpus-v2"


@pytest.mark.parametrize("mutate, words", [
    (lambda m: m["documents"].pop(0), "adds no document|changes, reorders or removes"),
    (lambda m: m["documents"][0].update(sha256="0" * 64), "changes, reorders or removes"),
    (lambda m: m["documents"].reverse(), "changes, reorders or removes"),
    (lambda m: m.update(digest_salt="1" * 64), "salt"),
    (lambda m: m["documents"].append(dict(m["documents"][0], id="doc-other")), "twice"),
])
def test_a_new_manifest_that_is_not_a_pure_append_is_refused(active, mutate, words):
    corpus, _ = active
    old = json.loads(corpus.manifest.read_text())
    new = json.loads(json.dumps(old))
    new["documents"].append({"id": "doc-new", "path": "added/doc-new.pdf", "sha256": "f" * 64, "bytes": 1})
    mutate(new)
    with pytest.raises(CloudError, match=words):
        intake.check_superset(old, new)


def test_an_active_corpus_with_a_missing_or_changed_document_cannot_be_grown(tmp_path):
    src = tmp_path / "v1"
    (src / "pdfs").mkdir(parents=True)
    (src / "pdfs" / "a.pdf").write_bytes(_pdf("a"))
    (src / "pdfs" / "b.pdf").write_bytes(_pdf("b"))
    manifests.create(src / "pdfs", src / "manifest.json")
    missing = _zip(tmp_path / "m.zip", {"manifest.json": (src / "manifest.json").read_bytes(),
                                        "pdfs/a.pdf": _pdf("a")})
    with pytest.raises(CloudError, match="missing"):
        cloud.unpack_corpus(missing, tmp_path / "x", "corpus-v1")
    changed = _zip(tmp_path / "c.zip", {"manifest.json": (src / "manifest.json").read_bytes(),
                                        "pdfs/a.pdf": _pdf("a"), "pdfs/b.pdf": _pdf("B!")})
    with pytest.raises(CloudError, match="differs"):
        cloud.unpack_corpus(changed, tmp_path / "y", "corpus-v1")


# --------------------------------------------------------------------------
# Publishing, and the command end to end
# --------------------------------------------------------------------------


def _fake_github(monkeypatch, releases, published):
    def api_json(url, token, **kwargs):
        if kwargs.get("method") == "POST" and url.endswith("/releases"):
            body = json.loads(kwargs["data"])
            published.append(("release", body))
            return {"id": 9, "upload_url": "https://uploads.github.invalid/r/9/assets{?name,label}"}
        if kwargs.get("method") == "POST":
            published.append(("asset", url, len(kwargs["data"])))
            return {"id": 10}
        tag = url.rsplit("/", 1)[-1]
        if tag in releases:
            return {"tag_name": tag}
        raise cloud._ApiError(404)

    monkeypatch.setattr(cloud, "api_json", api_json)


def test_publish_never_replaces_an_existing_release(tmp_path, monkeypatch):
    published = []
    _fake_github(monkeypatch, {"corpus-v2"}, published)
    (tmp_path / "a.zip").write_bytes(b"x")
    with pytest.raises(CloudError, match="already exists"):
        intake.publish("o/c", "corpus-v2", tmp_path / "a.zip", "t", "s")
    assert published == []


def test_publish_creates_the_release_but_does_not_make_it_active(tmp_path, monkeypatch):
    published = []
    _fake_github(monkeypatch, set(), published)
    (tmp_path / "a.zip").write_bytes(b"xyz")
    intake.publish("o/c", "corpus-v2", tmp_path / "a.zip", "t", "summary")
    kind, body = published[0]
    assert body["tag_name"] == "corpus-v2" and body["draft"] is False and body["make_latest"] == "false"
    assert published[1] == ("asset", "https://uploads.github.invalid/r/9/assets?name=corpus.zip", 3)


@pytest.fixture()
def releases(tmp_path, active, monkeypatch):
    """The command end to end, with downloads served from local archives."""
    corpus, _ = active
    served = {"corpus-v1": tmp_path / "v1.zip"}
    published = []

    def download(repository, tag, token, out):
        if tag not in served:
            raise CloudError(f"release {tag} was not found, or the corpus token cannot read it (HTTP 404)")
        out.write_bytes(served[tag].read_bytes())
        return {"tag": tag, "archive_sha256": hashlib.sha256(out.read_bytes()).hexdigest()}

    monkeypatch.setattr(cloud, "download", download)
    monkeypatch.setenv("LOSSLIFT_CORPUS_TOKEN", "read")
    monkeypatch.setenv("LOSSLIFT_CORPUS_WRITE_TOKEN", "write")
    monkeypatch.setattr(intake, "publish", lambda *args: published.append(args) or {"tag": args[1]})
    return served, published


def _cli(tmp_path, *args, name="run"):
    return intake.main([*args, "--work", str(tmp_path / name), "--report", str(tmp_path / f"{name}.md")])


def test_propose_then_publish_end_to_end(tmp_path, releases, capsys):
    served, published = releases
    served["intake-1"], _ = _intake(tmp_path, MAINE)
    assert _cli(tmp_path, "propose", "--active-tag", "corpus-v1", "--intake-tag", "intake-1", name="p") == 0
    out = capsys.readouterr().out
    digest = out.split("review_digest=")[1].split()[0]
    assert "candidates=6" in out and published == []
    complete = _id(MAINE[f"Real test loss run reports/{SENTINEL} complete packet.pdf"])
    code = _cli(tmp_path, "publish", "--active-tag", "corpus-v1", "--intake-tag", "intake-1",
                "--new-tag", "corpus-v2", "--approve", complete, "--review-digest", digest,
                "--token-variable", "LOSSLIFT_CORPUS_WRITE_TOKEN", name="q")
    assert code == 0, capsys.readouterr().err
    (repository, tag, archive, token, _summary), = published
    assert (tag, token) == ("corpus-v2", "write")
    assert "not active" in (tmp_path / "q.md").read_text()


def test_publish_without_explicit_approval_does_nothing(tmp_path, releases, capsys):
    served, published = releases
    served["intake-1"], _ = _intake(tmp_path, MAINE)
    code = _cli(tmp_path, "publish", "--active-tag", "corpus-v1", "--intake-tag", "intake-1",
                "--new-tag", "corpus-v2", "--approve", "", "--review-digest", "a" * 64)
    assert code == 3 and published == [] and not (tmp_path / "run").exists()
    assert "no document was approved" in capsys.readouterr().err


def test_a_missing_intake_release_is_reported(tmp_path, releases, capsys):
    assert _cli(tmp_path, "propose", "--active-tag", "corpus-v1", "--intake-tag", "intake-9") == 3
    assert "release intake-9 was not found" in capsys.readouterr().err
