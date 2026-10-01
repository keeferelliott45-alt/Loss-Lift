"""Labelling: write truth from the page, never from LossLift.

Three steps, none of which runs the pipeline it will measure:

* ``skeleton`` writes a blank truth entry per manifest document holding only
  what the bytes say -- id, hash, page count, one entry per page. Every
  judgement is the placeholder :data:`TODO`, which ``validate`` refuses, so an
  unfilled skeleton can never be scored.
* ``review`` renders each page beside the labels anchored to it, as one
  self-contained HTML sheet per document, and lists what is still unlabelled.
  It reads the truth leniently, so a half-written file can be reviewed.
* ``sign_off`` marks one document adjudicated once its truth is complete, and
  appends a line to a log beside the truth file. It is the record that a
  person verified the labels against the page.

Every output holds real document content -- the sheets hold page images -- so
all of it must live outside the repository, as the corpus and truth do.
"""

from __future__ import annotations

import base64
import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import pymupdf

from tools.corpus_gate import manifest as manifests
from tools.corpus_gate.manifest import SetupError
from tools.qualification.truth import (
    CRITICAL_FIELDS,
    SCORABLE_FIELDS,
    TRUTH_VERSION,
    TruthError,
    parse_document,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TODO = "TODO"
RENDER_DPI = 110


def _verified(manifest_path: Path, corpus: Path) -> tuple[manifests.Manifest, dict[str, Path]]:
    """The manifest, and each listed document's file, its bytes checked."""
    manifests.ensure_outside(Path(manifest_path), REPO_ROOT, "manifest")
    manifests.ensure_outside(Path(corpus), REPO_ROOT, "corpus")
    manifest = manifests.load(Path(manifest_path))
    verification = manifests.verify(manifest, Path(corpus))
    if not verification.ok:
        raise SetupError(
            f"{len(verification.missing)} document(s) missing and "
            f"{len(verification.mismatched)} changed since the manifest was written")
    return manifest, {e.id: manifests.locate(e, Path(corpus)) for e in manifest.entries}


def _page_count(path: Path) -> int:
    with pymupdf.open(path) as document:
        return document.page_count


def skeleton(*, manifest_path: Path, corpus: Path, out: Path) -> dict[str, int]:
    """Write a blank truth file for every document the manifest lists."""
    manifests.ensure_outside(Path(out), REPO_ROOT, "truth file")
    if Path(out).exists():
        raise SetupError("the truth file already exists; a skeleton never overwrites truth")
    manifest, files = _verified(manifest_path, corpus)
    documents = []
    for entry in manifest.entries:
        pages = _page_count(files[entry.id])
        documents.append({
            "id": entry.id,
            "sha256": entry.sha256,
            "format_family": TODO,
            "adjudication": "provisional",
            "page_count": pages,
            "pages": [{"page": n, "run": None, "role": TODO} for n in range(1, pages + 1)],
            "runs": [],
            "status": TODO,
            "printed": {"claim_count": TODO, "totals": {}},
            "claims": [],
        })
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps({"version": TRUTH_VERSION, "documents": documents},
                                    indent=1) + "\n", encoding="utf-8")
    return {"documents": len(documents), "pages": sum(d["page_count"] for d in documents)}


# --------------------------------------------------------------------------
# Review sheet
# --------------------------------------------------------------------------


def _raw(truth_path: Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(truth_path).read_text(encoding="utf-8"))
    except OSError:
        raise TruthError("the truth file cannot be read") from None
    except (ValueError, UnicodeDecodeError):
        raise TruthError("the truth file is not valid JSON") from None
    if not isinstance(data, dict) or not isinstance(data.get("documents"), list):
        raise TruthError(f"the truth file must be a version {TRUTH_VERSION} object")
    return data


def _todos(document: dict[str, Any]) -> list[str]:
    """What a person still has to label, by position and field name."""
    todo: list[str] = []
    for key in ("format_family", "status"):
        if document.get(key) in (None, TODO):
            todo.append(key)
    printed = document.get("printed") or {}
    if printed.get("claim_count") in (None, TODO):
        todo.append("printed claim_count")
    for page in document.get("pages") or []:
        if isinstance(page, dict) and page.get("role") in (None, TODO):
            todo.append(f"page {page.get('page')} role")
    for run in document.get("runs") or []:
        if isinstance(run, dict):
            if run.get("status") in (None, TODO):
                todo.append(f"run {run.get('id')} status")
            if (run.get("printed") or {}).get("claim_count") in (None, TODO):
                todo.append(f"run {run.get('id')} printed claim_count")
    for index, claim in enumerate(document.get("claims") or [], start=1):
        fields = (claim.get("fields") or {}) if isinstance(claim, dict) else {}
        missing = [n for n in CRITICAL_FIELDS if fields.get(n) in (None, TODO)]
        if missing:
            todo.append(f"claim {index}: {', '.join(missing)}")
    return todo


def _shown(value: Any) -> str:
    if isinstance(value, dict) and "state" in value:
        return value["state"] if value.get("value") is None else str(value["value"])
    return "" if value is None else str(value)


def _page_images(path: Path, dpi: int) -> Iterator[str]:
    with pymupdf.open(path) as document:
        for page in document:
            png = page.get_pixmap(dpi=dpi).tobytes("png")
            yield base64.b64encode(png).decode("ascii")


def _facts(printed: Any) -> str:
    if not isinstance(printed, dict):
        return ""
    items = [("claim count", printed.get("claim_count"))]
    items += sorted((printed.get("totals") or {}).items())
    return ", ".join(f"{html.escape(name)}: <b>{html.escape(_shown(value))}</b>"
                     for name, value in items)


def _sheet(document: dict[str, Any], images: list[str]) -> str:
    e = html.escape
    doc_id = str(document.get("id"))
    try:
        parse_document(document, 1)
        completeness = "complete: every required label is present"
    except TruthError as error:
        completeness = f"incomplete: {error}"
    todo = _todos(document)
    columns = [n for n in SCORABLE_FIELDS
               if any(n in (c.get("fields") or {}) for c in document.get("claims") or []
                      if isinstance(c, dict))]
    pages = {p.get("page"): p for p in document.get("pages") or [] if isinstance(p, dict)}
    runs = [r for r in document.get("runs") or [] if isinstance(r, dict)]

    out = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>Review {e(doc_id)}</title><style>",
        "body{font:14px system-ui,sans-serif;margin:16px;background:#fff;color:#111}",
        "section{display:flex;flex-wrap:wrap;gap:16px;border-top:2px solid #999;padding:12px 0}",
        "img{max-width:100%;width:720px;border:1px solid #ccc}",
        "table{border-collapse:collapse;font-size:13px}td,th{border:1px solid #bbb;padding:2px 6px}",
        ".todo{color:#a40000}",
        "</style></head><body>",
        f"<h1>Review {e(doc_id)}</h1>",
        f"<p>sha256 {e(str(document.get('sha256')))} · {e(str(document.get('page_count')))} "
        f"page(s) · adjudication <b>{e(str(document.get('adjudication')))}</b> · "
        f"format {e(_shown(document.get('format_family')))} · "
        f"status <b>{e(_shown(document.get('status')))}</b></p>",
        f"<p>Truth is {e(completeness)}</p>",
        f"<p>Document prints: {_facts(document.get('printed'))}</p>",
    ]
    for run in runs:
        out.append(f"<p>Run {e(str(run.get('id')))} pages {e(str(run.get('pages')))} · status "
                   f"<b>{e(_shown(run.get('status')))}</b> · prints: "
                   f"{_facts(run.get('printed'))}</p>")
    if todo:
        out.append("<p class='todo'>Still to label:</p><ul class='todo'>")
        out += [f"<li>{e(item)}</li>" for item in todo]
        out.append("</ul>")
    out.append("<p>Check every label against the page image. Reply <b>confirm "
               f"{e(doc_id)}</b> when all are right, or list corrections.</p>")

    for number, image in enumerate(images, start=1):
        page = pages.get(number, {})
        claims = [c for c in document.get("claims") or []
                  if isinstance(c, dict) and (c.get("anchor") or {}).get("page") == number]
        out.append("<section><div>")
        out.append(f"<h2>Page {number}</h2><img alt='page {number}' "
                   f"src=\"data:image/png;base64,{image}\"></div><div>")
        out.append(f"<p>role <b>{e(_shown(page.get('role')))}</b> · run "
                   f"<b>{e(_shown(page.get('run')))}</b></p>")
        for section in document.get("sections") or []:
            if isinstance(section, dict) and section.get("page") == number:
                out.append(f"<p>Section prints: {_facts(section.get('printed'))}</p>")
        if claims:
            out.append("<table><tr><th>claim</th><th>row</th>"
                       + "".join(f"<th>{e(n)}</th>" for n in columns) + "</tr>")
            for claim in claims:
                number_text = (claim.get("claim_number") if claim.get("identity", "known")
                               == "known" else "(ambiguous)")
                fields = claim.get("fields") or {}
                out.append(f"<tr><td>{e(_shown(number_text))}</td>"
                           f"<td>{e(_shown((claim.get('anchor') or {}).get('row')))}</td>"
                           + "".join(f"<td>{e(_shown(fields.get(n)))}</td>" for n in columns)
                           + "</tr>")
            out.append("</table>")
        else:
            out.append("<p>No claims labelled on this page.</p>")
        out.append("</div></section>")
    out.append("</body></html>")
    return "\n".join(out)


def review(*, manifest_path: Path, corpus: Path, truth_path: Path, out_dir: Path,
           dpi: int = RENDER_DPI) -> list[Path]:
    """Write one review sheet per truth document; return their paths."""
    manifests.ensure_outside(Path(out_dir), REPO_ROOT, "review directory")
    manifests.ensure_outside(Path(truth_path), REPO_ROOT, "truth file")
    manifest, files = _verified(manifest_path, corpus)
    listed = {entry.id: entry for entry in manifest.entries}
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    sheets: list[Path] = []
    for position, document in enumerate(_raw(truth_path)["documents"], start=1):
        if not isinstance(document, dict) or document.get("id") not in listed:
            raise TruthError(f"document {position}: not in the manifest")
        entry = listed[document["id"]]
        if document.get("sha256") != entry.sha256:
            raise TruthError(f"document {entry.id}: truth was written for other bytes "
                             f"than the manifest lists")
        sheet = Path(out_dir) / f"{entry.id}.html"
        sheet.write_text(_sheet(document, list(_page_images(files[entry.id], dpi))),
                         encoding="utf-8")
        sheets.append(sheet)
    return sheets


# --------------------------------------------------------------------------
# Sign-off
# --------------------------------------------------------------------------


def sign_off(*, truth_path: Path, document_id: str, note: str = "") -> None:
    """Mark one complete document adjudicated, and log who-verified-what."""
    manifests.ensure_outside(Path(truth_path), REPO_ROOT, "truth file")
    data = _raw(truth_path)
    matches = [d for d in data["documents"]
               if isinstance(d, dict) and d.get("id") == document_id]
    if len(matches) != 1:
        raise TruthError("the truth file holds no single document with that id")
    document = matches[0]
    document["adjudication"] = "adjudicated"
    position = data["documents"].index(document) + 1
    parse_document(document, position)  # complete, or it is not signed off
    Path(truth_path).write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log = Path(truth_path).with_name(Path(truth_path).name + ".signoff.log")
    with log.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp}\t{document_id}\t{document['sha256']}\tadjudicated"
                     f"{chr(9) + note if note else ''}\n")
