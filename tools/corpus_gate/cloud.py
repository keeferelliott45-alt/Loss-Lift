"""The trusted half of the cloud corpus gate.

``.github/workflows/corpus-gate.yml`` runs these steps, from ``main``, around
the unchanged ``python -m tools.corpus_gate run``:

    check-inputs   exact SHAs; the candidate is the pull request's live head
    download       the private corpus release, with the read-only corpus token
    unpack         validate the ZIP, refuse traversal, verify every file
    scrub          no GitHub credential is left where the gate could reach it
    stage-outputs  copy report.txt and result.json, and only after checking them
    summary        the job summary: SHAs, corpus release, count, verdict, exit
    cleanup        remove every document, manifest, worktree and container

Nothing here prints a file name, a path under the corpus, the salt, or any
byte of a document. Errors say what kind of thing went wrong and how many;
stdout carries ``key=value`` lines for ``$GITHUB_OUTPUT`` and nothing else.
Standard library only: this runs before anything is installed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from tools.corpus_gate import manifest as manifests
from tools.corpus_gate.manifest import SetupError

EXIT_PASS = 0
EXIT_SETUP = 3

FULL_SHA = re.compile(r"[0-9a-f]{40}")
PR_NUMBER = re.compile(r"[1-9][0-9]{0,9}")
TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")
CORPUS_REPOSITORY = "keeferelliott45-alt/LossLift-Corpus"
TOKEN_VARIABLES = (
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "LOSSLIFT_CORPUS_TOKEN",
    "LOSSLIFT_CORPUS_WRITE_TOKEN",
    "ACTIONS_RUNTIME_TOKEN",
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
    "ACTIONS_ID_TOKEN_REQUEST_URL",
)
MAX_ENTRIES = 20_000
MAX_MEMBER = 1 << 30  # 1 GiB
MAX_TOTAL = 6 << 30  # 6 GiB
MAX_RATIO = 200
MAX_ASSET = 6 << 30


class CloudError(Exception):
    """A setup failure. Its message is safe to print: no names, no paths."""


# --------------------------------------------------------------------------
# GitHub API
# --------------------------------------------------------------------------


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects, dropping the token whenever the host changes.

    A release asset redirects to a signed storage URL. urllib would otherwise
    carry the ``Authorization`` header there too.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            old_host = urllib.parse.urlsplit(req.full_url).hostname
            if urllib.parse.urlsplit(newurl).hostname != old_host:
                for key in list(new.headers):
                    if key.lower() == "authorization":
                        del new.headers[key]
                new.unredirected_hdrs.pop("Authorization", None)
        return new


def _api_base() -> str:
    return os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")


def http(
    url: str,
    token: str | None,
    *,
    accept: str = "application/vnd.github+json",
    method: str = "GET",
    data: bytes | None = None,
    content_type: str | None = None,
):
    """Open ``url``. Tests replace this function; nothing else talks to GitHub."""
    request = urllib.request.Request(url, method=method, data=data)
    request.add_header("Accept", accept)
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("User-Agent", "losslift-corpus-gate")
    if content_type:
        request.add_header("Content-Type", content_type)
    if token:
        request.add_unredirected_header("Authorization", f"Bearer {token}")
    opener = urllib.request.build_opener(_SameHostRedirect())
    return opener.open(request, timeout=120)


def api_json(url: str, token: str | None, **kwargs) -> Any:
    try:
        with http(url, token, **kwargs) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise _ApiError(error.code) from None
    except (urllib.error.URLError, OSError, ValueError):
        raise _ApiError(0) from None


class _ApiError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


def _token(variable: str) -> str:
    value = os.environ.get(variable, "")
    if not value.strip():
        raise CloudError(
            f"the {variable} secret is not available to this job: add it to the job's "
            "GitHub environment (see docs/cloud-corpus-gate.md)"
        )
    return value.strip()


# --------------------------------------------------------------------------
# Inputs and identity
# --------------------------------------------------------------------------


def check_sha(value: str, what: str) -> str:
    if not isinstance(value, str) or not FULL_SHA.fullmatch(value):
        raise CloudError(f"the {what} must be an exact 40-character lowercase commit SHA")
    return value


def check_tag(value: str, what: str = "corpus release tag") -> str:
    if not isinstance(value, str) or not TAG.fullmatch(value):
        raise CloudError(f"the {what} is missing or not a plain release tag")
    return value


def pull_request_head(repository: str, number: str, token: str) -> str:
    if not REPOSITORY.fullmatch(repository):
        raise CloudError("the repository name is not usable")
    if not PR_NUMBER.fullmatch(number):
        raise CloudError("the pull request number is not a positive integer")
    try:
        payload = api_json(f"{_api_base()}/repos/{repository}/pulls/{number}", token)
    except _ApiError as error:
        raise CloudError(f"pull request {number} could not be read (HTTP {error.status})") from None
    head = (payload.get("head") or {}).get("sha") if isinstance(payload, dict) else None
    base_repo = ((payload.get("base") or {}).get("repo") or {}).get("full_name")
    if base_repo != repository:
        raise CloudError(f"pull request {number} does not belong to this repository")
    if not isinstance(head, str) or not FULL_SHA.fullmatch(head):
        raise CloudError(f"pull request {number} has no readable head commit")
    return head


def check_inputs(
    *, repository: str, baseline: str, candidate: str, pr: str, token: str | None
) -> dict[str, str]:
    check_sha(baseline, "baseline")
    check_sha(candidate, "candidate")
    pr = (pr or "").strip()
    if pr:
        if not token:
            raise CloudError("a pull request was named but no token was given to read it")
        head = pull_request_head(repository, pr, token)
        if head != candidate:
            raise CloudError(
                f"the candidate is not the live head of pull request {pr}: the head is {head}. "
                "Dispatch again with the current head"
            )
    return {"baseline": baseline, "candidate": candidate, "pr": pr}


# --------------------------------------------------------------------------
# Downloading a release
# --------------------------------------------------------------------------


@dataclass
class ReleaseAsset:
    tag: str
    release_id: int
    asset_id: int
    size: int
    url: str


def find_zip_asset(repository: str, tag: str, token: str) -> ReleaseAsset:
    check_tag(tag)
    if not REPOSITORY.fullmatch(repository):
        raise CloudError("the corpus repository name is not usable")
    url = f"{_api_base()}/repos/{repository}/releases/tags/{urllib.parse.quote(tag, safe='')}"
    try:
        release = api_json(url, token)
    except _ApiError as error:
        if error.status in (401, 403, 404):
            raise CloudError(
                f"release {tag} was not found, or the corpus token cannot read it "
                f"(HTTP {error.status})"
            ) from None
        raise CloudError(f"release {tag} could not be read (HTTP {error.status})") from None
    if not isinstance(release, dict) or release.get("tag_name") != tag:
        raise CloudError(f"release {tag} did not answer as itself")
    zips = [
        asset
        for asset in release.get("assets") or []
        if isinstance(asset, dict) and str(asset.get("name", "")).lower().endswith(".zip")
    ]
    if len(zips) != 1:
        raise CloudError(f"release {tag} must carry exactly one .zip asset; it carries {len(zips)}")
    asset = zips[0]
    size = asset.get("size")
    if not isinstance(size, int) or size <= 0 or size > MAX_ASSET:
        raise CloudError(f"release {tag}'s archive is empty or too large")
    return ReleaseAsset(tag, int(release.get("id", 0)), int(asset["id"]), size, str(asset["url"]))


def download(repository: str, tag: str, token: str, out: Path) -> dict[str, Any]:
    """Fetch the release's one ZIP to ``out``; return its identity."""
    asset = find_zip_asset(repository, tag, token)
    out.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    written = 0
    try:
        with http(asset.url, token, accept="application/octet-stream") as response, out.open("xb") as sink:
            for block in iter(lambda: response.read(1 << 20), b""):
                written += len(block)
                if written > asset.size:
                    raise CloudError(f"release {tag}'s archive is larger than it said")
                digest.update(block)
                sink.write(block)
    except CloudError:
        out.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, OSError):
        out.unlink(missing_ok=True)
        raise CloudError(f"release {tag}'s archive could not be downloaded") from None
    if written != asset.size:
        out.unlink(missing_ok=True)
        raise CloudError(f"release {tag}'s archive arrived incomplete")
    return {
        "repository": repository,
        "tag": tag,
        "release_id": asset.release_id,
        "asset_id": asset.asset_id,
        "archive_sha256": digest.hexdigest(),
        "archive_bytes": written,
    }


# --------------------------------------------------------------------------
# Reading an archive safely
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Member:
    info: zipfile.ZipInfo
    parts: tuple[str, ...]
    is_dir: bool


def safe_members(archive: zipfile.ZipFile) -> list[Member]:
    """Every entry, checked. Anything that could land outside the target stops it.

    Refused: absolute paths, drive letters, backslashes, ``.`` and ``..``,
    empty components, NUL, symbolic links and other special files, encrypted
    entries, names that collide when case is ignored, and sizes or
    compression ratios beyond the limits.
    """
    infos = archive.infolist()
    if not infos:
        raise CloudError("the archive is empty")
    if len(infos) > MAX_ENTRIES:
        raise CloudError("the archive has too many entries")
    seen: set[str] = set()
    total = 0
    members: list[Member] = []
    for info in infos:
        name = info.filename
        if info.flag_bits & 0x1:
            raise CloudError("the archive has an encrypted entry")
        if (
            not name
            or "\x00" in name
            or "\\" in name
            or name.startswith("/")
            or re.match(r"[A-Za-z]:", name)
        ):
            raise CloudError("the archive has an entry with an absolute or unsafe path")
        is_dir = name.endswith("/")
        parts = tuple(name[:-1].split("/") if is_dir else name.split("/"))
        if any(part in ("", ".", "..") for part in parts):
            raise CloudError("the archive has an entry that climbs out of its folder")
        kind = stat.S_IFMT(info.external_attr >> 16)
        if kind and info.create_system == 3:
            if kind == stat.S_IFLNK:
                raise CloudError("the archive has a symbolic link")
            if kind not in (stat.S_IFREG, stat.S_IFDIR) or (kind == stat.S_IFDIR) != is_dir:
                raise CloudError("the archive has a special file")
        key = "/".join(parts).casefold()
        if key in seen:
            raise CloudError("the archive has two entries with the same name")
        seen.add(key)
        if not is_dir:
            if info.file_size > MAX_MEMBER:
                raise CloudError("the archive has an entry that is too large")
            if info.file_size > (10 << 20) and info.file_size > MAX_RATIO * max(info.compress_size, 1):
                raise CloudError("the archive has an entry compressed beyond any real document")
            total += info.file_size
            if total > MAX_TOTAL:
                raise CloudError("the archive expands beyond the size limit")
        members.append(Member(info, parts, is_dir))
    # A file and a folder of the same name would collide on extraction too.
    folders = {"/".join(m.parts[:i]).casefold() for m in members for i in range(1, len(m.parts))}
    if any(not m.is_dir and "/".join(m.parts).casefold() in folders for m in members):
        raise CloudError("the archive has an entry that is both a file and a folder")
    return members


def open_archive(path: Path) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError):
        raise CloudError("the archive is not a readable ZIP") from None
    return archive


def stream_member(archive: zipfile.ZipFile, member: Member, sink: Callable[[bytes], None]) -> int:
    """Read one entry, never trusting its declared size. Returns bytes read."""
    count = 0
    try:
        with archive.open(member.info) as source:
            for block in iter(lambda: source.read(1 << 20), b""):
                count += len(block)
                if count > member.info.file_size:
                    raise CloudError("the archive has an entry larger than it declares")
                sink(block)
    except (zipfile.BadZipFile, OSError, EOFError, RuntimeError, NotImplementedError):
        raise CloudError("the archive has a damaged entry") from None
    if count != member.info.file_size:
        raise CloudError("the archive has a damaged entry")
    return count


def extract_member(archive: zipfile.ZipFile, member: Member, dest: Path, parts: tuple[str, ...]) -> Path:
    root = dest.resolve()
    target = root.joinpath(*parts)
    if not target.resolve().is_relative_to(root) or target.resolve() == root:
        raise CloudError("the archive has an entry that climbs out of its folder")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("xb") as handle:
            stream_member(archive, member, handle.write)
    except FileExistsError:
        raise CloudError("the archive has two entries with the same name") from None
    return target


def _strip_wrapper(members: list[Member]) -> int:
    """How many leading folders to drop: 1 when everything sits in one folder."""
    firsts = {m.parts[0] for m in members}
    if len(firsts) == 1 and not any(len(m.parts) == 1 and not m.is_dir for m in members):
        inner = [m for m in members if len(m.parts) > 1]
        if any(m.parts[1:] == ("manifest.json",) for m in inner):
            return 1
    return 0


@dataclass
class Corpus:
    documents: Path
    manifest: Path
    count: int
    manifest_sha256: str
    bound_release: str | None
    payload: dict[str, Any]


def unpack_corpus(archive_path: Path, dest: Path, tag: str | None) -> Corpus:
    """Extract a corpus archive and prove it is exactly what its manifest lists.

    The archive holds ``manifest.json`` and ``pdfs/``, nothing else. Every
    file under ``pdfs/`` must be listed, every listed file present with the
    listed size and SHA-256. A manifest that names its release must name
    this one.
    """
    dest.mkdir(parents=True, exist_ok=False)
    with open_archive(archive_path) as archive:
        members = safe_members(archive)
        drop = _strip_wrapper(members)
        unexpected = 0
        wanted: list[tuple[Member, tuple[str, ...]]] = []
        for member in members:
            parts = member.parts[drop:]
            if not parts:
                continue
            if parts == ("manifest.json",) and not member.is_dir:
                wanted.append((member, parts))
            elif parts[0] == "pdfs" and (member.is_dir or len(parts) > 1):
                if not member.is_dir:
                    wanted.append((member, parts))
            else:
                unexpected += 1
        if unexpected:
            raise CloudError(
                f"the archive has {unexpected} entr{'y' if unexpected == 1 else 'ies'} outside "
                "manifest.json and pdfs/"
            )
        if not any(parts == ("manifest.json",) for _, parts in wanted):
            raise CloudError("the archive has no manifest.json")
        for member, parts in wanted:
            extract_member(archive, member, dest, parts)
    documents = dest / "pdfs"
    documents.mkdir(exist_ok=True)
    manifest_path = dest / "manifest.json"
    try:
        manifest = manifests.load(manifest_path)
    except SetupError as error:
        raise CloudError(f"the corpus manifest is unusable: {error}") from None
    verify_exact(manifest, documents)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    bound = payload.get("release")
    if bound is not None and (not isinstance(bound, str) or bound != tag):
        raise CloudError("the corpus manifest names a different release than the one downloaded")
    return Corpus(documents, manifest_path, len(manifest.entries), manifest.sha256, bound, payload)


def verify_exact(manifest: manifests.Manifest, documents: Path) -> None:
    """The documents folder holds the listed files, unchanged, and nothing else."""
    try:
        verification = manifests.verify(manifest, documents)
    except SetupError as error:
        raise CloudError(str(error)) from None
    listed = {entry.path for entry in manifest.entries}
    present = {
        PurePosixPath(path.relative_to(documents).as_posix()).as_posix()
        for path in documents.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    additional = len(present - listed)
    problems = []
    if verification.missing:
        problems.append(f"{len(verification.missing)} listed document(s) missing")
    if verification.mismatched:
        problems.append(f"{len(verification.mismatched)} document(s) whose size or SHA-256 differs")
    if additional:
        problems.append(f"{additional} file(s) the manifest does not list")
    if problems:
        raise CloudError("the corpus does not match its manifest: " + "; ".join(problems))


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------


def scrub(repo_dir: Path, environ: dict[str, str] | None = None) -> list[str]:
    """Remove stored GitHub credentials from the checkout; name what was found.

    Returns the problems that could not be removed here: a token variable in
    this process's own environment means the step was given one it must not
    have, and the workflow fails rather than carry on.
    """
    environ = dict(os.environ) if environ is None else environ
    for pattern in (r"^http\..*\.extraheader$", r"^credential\..*", r"^url\..*\.insteadof$"):
        listed = subprocess.run(
            ["git", "-C", str(repo_dir), "config", "--local", "--name-only", "--get-regexp", pattern],
            capture_output=True, text=True, check=False,
        )
        for key in sorted(set(listed.stdout.split())):
            subprocess.run(
                ["git", "-C", str(repo_dir), "config", "--local", "--unset-all", key],
                capture_output=True, check=False,
            )
    config = (repo_dir / ".git" / "config")
    problems = []
    if config.is_file() and re.search(r"(?i)authorization|x-access-token|ghp_|ghs_|github_pat_",
                                      config.read_text(encoding="utf-8", errors="replace")):
        problems.append("the checkout's git config still carries a credential")
    for variable in TOKEN_VARIABLES:
        if environ.get(variable):
            problems.append(f"{variable} is set in the gate's environment")
    return problems


# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------

_SAFE_TEXT = re.compile(r"[\x20-\x7e]*")
_ABSOLUTE = re.compile(r"(^|[\s(\"'=])(/[A-Za-z0-9._-]+/|[A-Za-z]:[\\/]|~/)")
OUTPUT_FILES = ("report.txt", "result.json")


def _forbidden(manifest_path: Path | None, extra: list[str]) -> list[str]:
    words = [value for value in extra if value]
    if manifest_path is not None and manifest_path.is_file():
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        words.append(str(payload.get("digest_salt", "")))
        for item in payload.get("documents", []):
            rel = str(item.get("path", ""))
            words.append(rel)
            stem = PurePosixPath(rel).stem
            if len(stem) >= 6:
                words.append(stem)
    return [word for word in words if len(word) >= 6]


def _check_text(text: str, forbidden: list[str], where: str) -> None:
    for line in text.splitlines():
        if not _SAFE_TEXT.fullmatch(line):
            raise CloudError(f"{where} holds characters outside printable ASCII")
        if _ABSOLUTE.search(line):
            raise CloudError(f"{where} holds something shaped like a local path")
    folded = text.casefold()
    for word in forbidden:
        # Whole words: a file called "Buffalo 2024" must not appear, but the
        # gate's own vocabulary must not trip over a stem it happens to contain.
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(word.casefold())}(?![A-Za-z0-9])", folded):
            raise CloudError(f"{where} holds a corpus name, path or secret")


def _strings(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, str):
        yield value


def stage_outputs(out_dir: Path, dest: Path, manifest_path: Path | None, forbid: list[str]) -> dict[str, Any]:
    """Write the two published files, from the checked result alone; nothing else.

    ``result.json`` must be exactly the public result (``seal.validate_public``):
    known keys only, exact types, bounded integers and lists, no raw
    measurement and no timing. It is re-serialised from the validated object,
    and ``report.txt`` is rendered from that object here, so the runner's own
    report is never published. Both are then checked for printable ASCII,
    nothing shaped like a path, and none of the corpus's file names, paths or
    salt.
    """
    from tools.corpus_gate import seal

    forbidden = _forbidden(manifest_path, forbid)
    result_path = out_dir / "result.json"
    if not result_path.is_file() or result_path.stat().st_size > (4 << 20):
        raise CloudError("the gate wrote no usable result")
    try:
        result = seal.strict_loads(result_path.read_text(encoding="utf-8", errors="strict"))
        seal.validate_public(result)
    except (json.JSONDecodeError, UnicodeDecodeError, seal.Rejected):
        raise CloudError("result.json is not strict JSON") from None
    except seal.Unpublishable as error:
        raise CloudError(f"result.json is not the public result: {error}") from None
    raw = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    report = seal.render_public(result)
    _check_text(report, forbidden, "report.txt")
    _check_text(raw, forbidden, "result.json")
    for text in _strings(result):
        _check_text(text, forbidden, "result.json")
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "result.json").write_text(raw, encoding="ascii")
    (dest / "report.txt").write_text(report, encoding="ascii")
    return result


def summary(
    *,
    baseline: str,
    candidate: str,
    pr: str,
    identity: dict[str, Any],
    documents: int | None,
    exit_code: int | None,
    result: dict[str, Any] | None,
    recheck: str = "",
    pr_moved: str = "",
) -> str:
    """The job summary. PASS needs exit 0, a passing verdict, and -- when a
    pull request was named -- a re-check that ran, succeeded and said the head
    had not moved. Anything short of that is FAIL."""
    verdict = "not reached"
    if isinstance(result, dict) and isinstance(result.get("gate"), dict):
        value = result["gate"].get("verdict")
        verdict = value if value in ("pass", "fail") else "unreadable"
    named = bool(pr)
    confirmed = recheck == "success" and pr_moved == "false"
    passed = exit_code == 0 and verdict == "pass" and (confirmed or not named)
    rows = [
        ("Outcome", "PASS" if passed else "FAIL"),
        ("Gate exit code", "not reached" if exit_code is None else str(int(exit_code))),
        ("Verdict", verdict),
        ("Baseline", f"`{baseline}`" if FULL_SHA.fullmatch(baseline) else "invalid"),
        ("Candidate", f"`{candidate}`" if FULL_SHA.fullmatch(candidate) else "invalid"),
        ("Pull request", f"#{pr}" if pr and PR_NUMBER.fullmatch(pr) else "none named"),
        ("Head re-confirmed after the run",
         ("yes" if confirmed else "no") if named else "not needed"),
        ("Corpus release", f"`{identity.get('tag')}`" if TAG.fullmatch(str(identity.get("tag", ""))) else "unknown"),
        ("Release archive SHA-256", _hex_or_unknown(identity.get("archive_sha256"))),
        ("Manifest SHA-256", _hex_or_unknown(identity.get("manifest_sha256"))),
        ("Manifest bound to release", "yes" if identity.get("bound") else "no (older manifest)"),
        ("Documents verified", "not reached" if documents is None else str(int(documents))),
    ]
    lines = ["## LossLift real-corpus gate", "", "| | |", "|---|---|"]
    lines += [f"| {key} | {value} |" for key, value in rows]
    if named and pr_moved == "true":
        lines += ["", "The pull request's head moved during the run; this result is for the "
                      "candidate SHA above only. Dispatch again for the new head."]
    elif named and not confirmed:
        lines += ["", "The pull request's head could not be re-confirmed after the run, so the "
                      "result cannot stand for it. Dispatch again."]
    return "\n".join(lines) + "\n"


def _hex_or_unknown(value: Any) -> str:
    return f"`{value}`" if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) else "unknown"


# --------------------------------------------------------------------------
# Cleanup
# --------------------------------------------------------------------------


def remove_tree(path: Path) -> None:
    def retry(function, target, _info):
        try:
            os.chmod(target, stat.S_IRWXU)
            os.chmod(os.path.dirname(target), stat.S_IRWXU)
            function(target)
        except OSError:
            pass

    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=retry)
        else:
            shutil.rmtree(path, onerror=retry)


def _docker(docker: str, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([docker, *args], capture_output=True, text=True, check=False, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess([docker, *args], 127, "", "")


def _labelled(docker: str, kind: str) -> list[str] | None:
    """Ids of task containers or images, or None when Docker cannot say."""
    from tools.corpus_gate.sandbox import LABEL

    listing = ("ps", "-aq") if kind == "containers" else ("images", "-q")
    listed = _docker(docker, *listing, "--filter", f"label={LABEL}")
    return listed.stdout.split() if listed.returncode == 0 else None


def clean_docker(docker: str, image: str | None, required: bool) -> list[str]:
    """Remove every task container and the task image, then prove they are gone.

    Every removal is attempted whatever happened to the one before. What
    counts is not what the removals returned but what is left afterwards:
    containers and images carrying the task label are listed again, and the
    named image is inspected. Anything still there -- or a Docker that
    cannot be asked -- is a problem.
    """
    problems: list[str] = []
    if shutil.which(docker) is None:
        return ["Docker is not available to verify cleanup"] if required else []
    containers = _labelled(docker, "containers")
    if containers is None:
        return ["Docker could not list task containers"]
    failed = [c for c in containers if _docker(docker, "rm", "--force", c).returncode != 0]
    images = _labelled(docker, "images") or []
    for name in ([image] if image else []) + images:
        _docker(docker, "image", "rm", "--force", name)
    remaining = _labelled(docker, "containers")
    if remaining is None:
        problems.append("Docker could not list task containers after removal")
    elif remaining:
        problems.append(f"{len(remaining)} task container(s) remain"
                        + (f" ({len(failed)} removal(s) failed)" if failed else ""))
    left_images = _labelled(docker, "images")
    if left_images is None:
        problems.append("Docker could not list task images after removal")
    elif left_images:
        problems.append(f"{len(left_images)} labelled task image(s) remain")
    if image and _docker(docker, "image", "inspect", image).returncode == 0:
        problems.append("the task image remains")
    return problems


def cleanup(
    paths: list[Path],
    repo_dir: Path | None,
    image: str | None,
    docker: str = "docker",
    require_docker: bool = False,
) -> list[str]:
    """Remove everything; never stop half-way. Returns what could not be removed.

    Containers go first, because a live one still has the documents mounted.
    Host paths are removed whatever Docker said, and their removal never
    stands in for Docker's.
    """
    left: list[str] = []
    try:
        left += clean_docker(docker, image, require_docker)
    except Exception:  # noqa: BLE001 - keep cleaning; report it
        left.append("Docker cleanup raised")
    for position, path in enumerate(paths, start=1):
        try:
            remove_tree(path)
        except OSError:
            pass
        if path.exists() or path.is_symlink():
            left.append(f"path {position}")
    if repo_dir is not None and (repo_dir / ".git").exists():
        subprocess.run(["git", "-C", str(repo_dir), "worktree", "prune"], capture_output=True, check=False)
        listed = subprocess.run(["git", "-C", str(repo_dir), "worktree", "list", "--porcelain"],
                                capture_output=True, text=True, check=False)
        if listed.stdout.count("worktree ") > 1:
            left.append("git worktrees")
    return left


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.corpus_gate.cloud")
    commands = parser.add_subparsers(dest="command", required=True)

    inputs = commands.add_parser("check-inputs")
    inputs.add_argument("--repository", required=True)
    inputs.add_argument("--baseline", required=True)
    inputs.add_argument("--candidate", required=True)
    inputs.add_argument("--pr", default="")
    inputs.add_argument("--recheck", action="store_true",
                        help="report a moved head as moved=true instead of failing")

    fetch = commands.add_parser("download")
    fetch.add_argument("--repository", default=CORPUS_REPOSITORY)
    fetch.add_argument("--tag", required=True)
    fetch.add_argument("--out", required=True, type=Path)
    fetch.add_argument("--identity", required=True, type=Path)
    fetch.add_argument("--token-variable", default="LOSSLIFT_CORPUS_TOKEN")

    unpack = commands.add_parser("unpack")
    unpack.add_argument("--zip", required=True, type=Path)
    unpack.add_argument("--dest", required=True, type=Path)
    unpack.add_argument("--identity", required=True, type=Path)

    clean = commands.add_parser("scrub")
    clean.add_argument("--repo-dir", required=True, type=Path)

    stage = commands.add_parser("stage-outputs")
    stage.add_argument("--out", required=True, type=Path)
    stage.add_argument("--dest", required=True, type=Path)
    stage.add_argument("--manifest", type=Path, default=None)
    stage.add_argument("--forbid", action="append", default=[])

    summ = commands.add_parser("summary")
    summ.add_argument("--baseline", required=True)
    summ.add_argument("--candidate", required=True)
    summ.add_argument("--pr", default="")
    summ.add_argument("--identity", type=Path, required=True)
    summ.add_argument("--result", type=Path, required=True)
    summ.add_argument("--exit-code", default="")
    summ.add_argument("--recheck", default="")
    summ.add_argument("--pr-moved", default="")

    remove = commands.add_parser("cleanup")
    remove.add_argument("--path", action="append", type=Path, default=[])
    remove.add_argument("--repo-dir", type=Path, default=None)
    remove.add_argument("--image", default=None)
    remove.add_argument("--docker", default="docker")
    remove.add_argument("--require-docker", action="store_true",
                        help="fail unless Docker can be asked and confirms nothing is left")
    return parser


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _emit(**values: Any) -> None:
    for key, value in values.items():
        print(f"{key}={value}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "check-inputs":
            token = os.environ.get("GITHUB_TOKEN") or None
            try:
                check_inputs(repository=args.repository, baseline=args.baseline,
                             candidate=args.candidate, pr=args.pr, token=token)
            except CloudError as error:
                if args.recheck and "not the live head" in str(error):
                    _emit(moved="true")
                    print(f"corpus gate: {error}", file=sys.stderr)
                    return EXIT_PASS
                raise
            if args.recheck:
                _emit(moved="false")
        elif args.command == "download":
            token = _token(args.token_variable)
            identity = download(args.repository, check_tag(args.tag), token, args.out)
            args.identity.parent.mkdir(parents=True, exist_ok=True)
            args.identity.write_text(json.dumps(identity, sort_keys=True), encoding="utf-8")
            _emit(archive_sha256=identity["archive_sha256"])
        elif args.command == "unpack":
            identity = _read_json(args.identity)
            try:
                corpus = unpack_corpus(args.zip, args.dest, identity.get("tag"))
            finally:
                args.zip.unlink(missing_ok=True)
            identity.update(documents=corpus.count, manifest_sha256=corpus.manifest_sha256,
                            bound=corpus.bound_release is not None)
            args.identity.write_text(json.dumps(identity, sort_keys=True), encoding="utf-8")
            _emit(documents=corpus.count)
        elif args.command == "scrub":
            problems = scrub(args.repo_dir)
            if problems:
                raise CloudError("; ".join(problems))
        elif args.command == "stage-outputs":
            result = stage_outputs(args.out, args.dest, args.manifest, args.forbid)
            _emit(verdict=result["gate"]["verdict"])
        elif args.command == "summary":
            identity = _read_json(args.identity)
            result = _read_json(args.result) or None
            code = int(args.exit_code) if re.fullmatch(r"[0-9]{1,3}", args.exit_code) else None
            docs = identity.get("documents")
            sys.stdout.write(summary(
                baseline=args.baseline, candidate=args.candidate, pr=args.pr,
                identity=identity, documents=docs if isinstance(docs, int) else None,
                exit_code=code, result=result, recheck=args.recheck, pr_moved=args.pr_moved,
            ))
        elif args.command == "cleanup":
            left = cleanup(args.path, args.repo_dir, args.image, docker=args.docker,
                           require_docker=args.require_docker)
            if left:
                print(f"corpus gate cleanup: could not remove {', '.join(left)}", file=sys.stderr)
                return EXIT_SETUP
    except CloudError as error:
        print(f"corpus gate: {error}", file=sys.stderr)
        return EXIT_SETUP
    return EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
