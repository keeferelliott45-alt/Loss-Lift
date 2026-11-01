"""Run the real-document corpus and append the results.

    python -m benchmark.run --docs /path/to/corpus            # append a row per doc
    python -m benchmark.run --docs /path/to/corpus --label cold-baseline

Source PDFs are never committed. They are real carrier documents containing
claimant names and injury descriptions, so the repository holds the manifest,
the measurements and the failure registry, and the documents stay wherever the
person running this keeps them (spec section 9). ``--docs`` points at that
directory; a document listed in the manifest but absent there is recorded as
``not_present`` rather than silently skipped, because a corpus that quietly
shrinks is how a benchmark starts lying.

Ground truth is only ever read from ``corpus_manifest.csv``. Nothing here
infers an expected claim count from what the engine happened to produce.
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BENCHMARK_DIR = Path(__file__).resolve().parent
MANIFEST = BENCHMARK_DIR / "corpus_manifest.csv"
RESULTS = BENCHMARK_DIR / "results.csv"

RESULT_FIELDS = [
    "doc_id", "engine_version", "timestamp", "claims_expected", "claims_extracted",
    "claim_precision", "claim_recall", "money_fields_expected", "money_fields_exact",
    "mapping_accuracy", "reconciliation_status", "false_clean", "review_flags",
    "processing_seconds", "label", "notes",
    # Appended 2026-11: the engine's ERROR-only status kept for comparison,
    # the trust-blocking rule ids behind a NEEDS_REVIEW (ids and counts only,
    # never message text), and money accuracy scored against adjudicated truth.
    "engine_status", "blocking_rules", "money_precision", "money_recall",
    "truth_source",
]


def engine_version() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True, cwd=BENCHMARK_DIR.parent,
        ).stdout.strip()
    except Exception:  # pragma: no cover - git absent
        return "unknown"


def load_manifest() -> list[dict[str, str]]:
    with MANIFEST.open() as handle:
        return list(csv.DictReader(handle))


def _blank_row(doc_id: str, label: str, note: str) -> dict[str, object]:
    row = {name: "" for name in RESULT_FIELDS}
    row.update(doc_id=doc_id, engine_version=engine_version(), label=label,
               timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
               notes=note)
    return row


def describe_status(row: dict[str, object], reconciliation, *, needs_mapping: bool) -> None:
    """Status as every other layer shows it, and which rules keep it amber.

    ``reconciliation_status`` is the canonical policy (``core.review``), never
    the engine's ERROR-only status: an extraction WARN blocks trust in the app
    and the workbook, so it must block it here too (AGENTS rule 6). The engine
    status is kept beside it. ``blocking_rules`` names rule ids and counts
    only; a finding's message can quote the document and is never written.
    """
    from core.review import blocks_trust, canonical_status

    row["engine_status"] = reconciliation.status.value
    row["reconciliation_status"] = (
        "NEEDS_MAPPING" if needs_mapping
        else canonical_status(reconciliation).value
    )
    counts: dict[str, int] = {}
    for finding in reconciliation.findings:
        if blocks_trust(finding):
            counts[finding.rule_id] = counts.get(finding.rule_id, 0) + 1
    for run_id, status in sorted(reconciliation.run_status.items()):
        if status.value != "CLEAN":
            counts[f"run:{run_id}"] = counts.get(f"run:{run_id}", 0) + 1
    row["blocking_rules"] = ";".join(
        f"{rule}x{count}" for rule, count in sorted(counts.items()))


def apply_truth(row: dict[str, object], record: dict | None) -> None:
    """Money accuracy and false-CLEAN from a qualification record, or nothing.

    Only an adjudicated, scored record counts. Anything else leaves the
    columns blank: an unmeasured rate is never reported as perfect.
    """
    if not record or record.get("category") != "scored" or not record.get("qualified_truth"):
        return
    metrics = record.get("metrics") or {}
    money = (metrics.get("overall") or {}).get("money") or {}
    row["money_fields_exact"] = money.get("correct", "")
    if money:
        row["money_fields_expected"] = (money.get("correct", 0) + money.get("incorrect", 0)
                                        + money.get("missing", 0))
    for column, key in (("money_precision", "precision"), ("money_recall", "recall")):
        value = money.get(key)
        row[column] = "" if value is None else round(value, 4)
    false_clean = (metrics.get("status") or {}).get("false_clean_documents")
    if false_clean is not None:
        row["false_clean"] = "yes" if false_clean else "no"
    row["truth_source"] = "qualification"


def load_qualification(report_path: Path, manifest_path: Path, docs: Path) -> dict[str, dict]:
    """Qualification records keyed by benchmark doc id.

    The gate manifest names documents ``doc-<hash>`` and the report seals those
    ids under the manifest's salt. A benchmark document is matched to its gate
    entry by the bytes of ``<docs>/<doc_id>.pdf`` (sha256), never by name, then
    sealed the same way and looked up. No match, no record.
    """
    import json

    from tools.corpus_gate import manifest as manifests
    from tools.qualification import public

    report = json.loads(report_path.read_text(encoding="utf-8"))
    gate = manifests.load(manifest_path)
    by_hash = {entry.sha256: entry.id for entry in gate.entries}
    records = {r["document"]: r for r in report.get("records", []) if "document" in r}
    found: dict[str, dict] = {}
    for doc_id in (entry["doc_id"] for entry in load_manifest()):
        path = docs / f"{doc_id}.pdf"
        gate_id = by_hash.get(manifests.file_sha256(path)) if path.is_file() else None
        sealed = public.seal(gate_id, gate.salt) if gate_id else None
        if sealed in records:
            found[doc_id] = records[sealed]
    return found


def measure(doc_id: str, path: Path, expected: str, label: str) -> dict[str, object]:
    """One document's numbers. Unknown stays empty; nothing is invented."""
    from core.pipeline import run_pipeline

    row = _blank_row(doc_id, label, "")
    row["claims_expected"] = expected
    started = time.time()
    try:
        result = run_pipeline(str(path), use_vision=False)
    except Exception as error:  # a crash is a result, not an absence of one
        row["reconciliation_status"] = "CRASH"
        row["notes"] = f"{type(error).__name__}: {error}"[:180]
        row["processing_seconds"] = round(time.time() - started, 2)
        return row

    document = result.document
    findings = result.reconciliation.findings
    extracted = len(document.claims)
    row["processing_seconds"] = round(time.time() - started, 2)
    row["claims_extracted"] = extracted
    describe_status(row, result.reconciliation, needs_mapping=result.needs_mapping)
    row["review_flags"] = len(findings)

    # Recall and precision need an adjudicated count. Where the manifest has
    # one, recall is measurable; precision is not, because knowing how many of
    # the extracted rows are genuine needs row-level adjudication that has not
    # been done. An unmeasured metric stays blank.
    if expected.isdigit() and int(expected) > 0:
        row["claim_recall"] = round(min(extracted, int(expected)) / int(expected), 4)

    # False-clean: called clean while the evidence says otherwise. Only a
    # claim count mismatch is machine-adjudicable today; a document with no
    # ground truth cannot be judged, and is left blank rather than passed.
    clean = row["reconciliation_status"] == "CLEAN"
    row["truth_source"] = "printed_count" if expected.isdigit() else ""
    if not expected.isdigit():
        row["false_clean"] = "" if clean else "no"
    else:
        row["false_clean"] = "yes" if clean and extracted != int(expected) else "no"
    return row


def append_rows(path: Path, rows: list[dict[str, object]]) -> None:
    """Append, widening an older file's header in place when columns were added.

    Earlier rows keep their values and read blank in the new columns.
    """
    existing: list[dict[str, str]] = []
    rewrite = not path.exists()
    if not rewrite:
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != RESULT_FIELDS:
                rewrite = True
                existing = list(reader)
    if rewrite:
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=RESULT_FIELDS, restval="")
            writer.writeheader()
            writer.writerows(existing)
    with path.open("a", newline="") as handle:
        csv.DictWriter(handle, fieldnames=RESULT_FIELDS, restval="").writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs", required=True, type=Path,
                        help="directory holding the corpus PDFs, named <doc_id>.pdf")
    parser.add_argument("--label", default="", help="a name for this run")
    parser.add_argument("--qualification", type=Path,
                        help="qualification.json from tools.qualification run")
    parser.add_argument("--manifest", type=Path,
                        help="the gate manifest the qualification run used (holds the salt)")
    args = parser.parse_args()
    if bool(args.qualification) != bool(args.manifest):
        parser.error("--qualification and --manifest go together")

    sys.path.insert(0, str(BENCHMARK_DIR.parent))
    truth = load_qualification(args.qualification, args.manifest, args.docs) if args.qualification else {}
    rows = []
    for entry in load_manifest():
        doc_id = entry["doc_id"]
        path = args.docs / f"{doc_id}.pdf"
        if not path.exists():
            rows.append(_blank_row(doc_id, args.label, "not_present"))
            print(f"{doc_id:20} not present in {args.docs}")
            continue
        row = measure(doc_id, path, entry.get("expected_claims", ""), args.label)
        apply_truth(row, truth.get(doc_id))
        rows.append(row)
        print(f"{doc_id:20} {row['claims_extracted'] or '-':>5} extracted "
              f"(expected {row['claims_expected'] or '?'})  "
              f"{row['reconciliation_status']:14} false_clean={row['false_clean'] or '?'}  "
              f"money={row['money_recall'] or '?'}  blocking={row['blocking_rules'] or '-'}")

    append_rows(RESULTS, rows)
    print(f"\n{len(rows)} row(s) appended to {RESULTS}")
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
