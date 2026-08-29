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
    row["reconciliation_status"] = (
        "NEEDS_MAPPING" if result.needs_mapping
        else result.reconciliation.status.value
    )
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
    if not expected.isdigit():
        row["false_clean"] = "" if clean else "no"
    else:
        row["false_clean"] = "yes" if clean and extracted != int(expected) else "no"
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs", required=True, type=Path,
                        help="directory holding the corpus PDFs, named <doc_id>.pdf")
    parser.add_argument("--label", default="", help="a name for this run")
    args = parser.parse_args()

    sys.path.insert(0, str(BENCHMARK_DIR.parent))
    rows = []
    for entry in load_manifest():
        doc_id = entry["doc_id"]
        path = args.docs / f"{doc_id}.pdf"
        if not path.exists():
            rows.append(_blank_row(doc_id, args.label, "not_present"))
            print(f"{doc_id:20} not present in {args.docs}")
            continue
        row = measure(doc_id, path, entry.get("expected_claims", ""), args.label)
        rows.append(row)
        print(f"{doc_id:20} {row['claims_extracted'] or '-':>5} extracted "
              f"(expected {row['claims_expected'] or '?'})  "
              f"{row['reconciliation_status']:14} false_clean={row['false_clean'] or '?'}")

    exists = RESULTS.exists()
    with RESULTS.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerows(rows)
    print(f"\n{len(rows)} row(s) appended to {RESULTS}")
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
