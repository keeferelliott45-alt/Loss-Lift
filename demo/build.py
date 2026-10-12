"""Build the synthetic demo pack for discovery calls and live demos.

    python -m demo.build [--out DIR] [--check]

Every document is synthetic, rendered from the same generators the test
suite uses (``tests/golden``, the packet sheet builders): no real loss run,
name or amount is involved (spec section 9). The output is deterministic, so
the same command always writes byte-identical PDFs (the saved email's MIME
boundary varies run to run; its contents do not). ``--check`` also reads
each one through the pipeline (no LLM, no vision) and confirms it still
behaves as ``demo/README.md`` says.

PDFs are never committed (``*.pdf`` is git-ignored): build them before a call.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.pipeline import run_pipeline  # noqa: E402
from core.review import canonical_status  # noqa: E402
from core.schema import DocumentStatus  # noqa: E402
from tests.golden.fixtures import BY_NAME  # noqa: E402
from tests.submission_fixtures import claim_rows, eml, loss_run_pdf  # noqa: E402
from tests.golden.generate import render as render_golden  # noqa: E402
from tests.test_packet_adversarial import LETTER, _sheet, _total  # noqa: E402
from tests.test_packet_claim_series import (  # noqa: E402
    LARGE_CARRIER,
    SMALL_CARRIER,
    SMALL_HEADERS,
    SMALL_RUN,
    _large_run,
)
from tests.test_run_metadata import AUTO_TERM, GL_TERM, RUN_ONE, RUN_TWO  # noqa: E402
from tests.fuzz.generator import render as render_fuzz  # noqa: E402
from tests.fuzz.run import build_case  # noqa: E402

DEFAULT_OUT = Path(__file__).resolve().parent / "out"


@dataclass(frozen=True)
class DemoDocument:
    filename: str
    build: Callable[[Path], Path]
    #: What a reviewer should see, checked by ``--check`` and the tests.
    status: DocumentStatus
    runs: int
    #: Rule ids that must be among the findings (the demo's talking point).
    rules: tuple[str, ...]
    beat: str
    #: Read scanned pages from the recorded answers in ``recordings/`` (never a
    #: live model). Only the scanned-replay item sets this.
    replay: bool = False


def _save(document: pymupdf.Document, path: Path) -> Path:
    # No /ID and no timestamps: the same build is the same bytes.
    document.set_metadata({})
    if document.xref_get_key(-1, "ID")[0] != "null":
        document.xref_set_key(-1, "ID", "null")
    document.save(path, garbage=3, deflate=True, no_new_id=True)
    document.close()
    return path


def _sheets(path: Path, sheets: list[dict]) -> Path:
    document = pymupdf.open()
    for sheet in sheets:
        _sheet(document, **sheet)
    return _save(document, path)


def _two_carrier_packet(path: Path) -> Path:
    """Two carriers' reports bound in one PDF, each with its own letterhead,
    policy, term, valuation date and printed total."""
    return _sheets(path, [
        {"top": ("Page 1 of 1", LARGE_CARRIER, "LOSS RUN REPORT", *GL_TERM,
                 "Valuation Date: 12/31/2022"),
         "rows": RUN_ONE, "total": _total(RUN_ONE)},
        {"top": ("Page 1 of 1", SMALL_CARRIER, "LOSS RUN REPORT", *AUTO_TERM,
                 "Valuation Date: 06/30/2021"),
         "headers": SMALL_HEADERS, "rows": RUN_TWO, "total": _total(RUN_TWO)},
    ])


def _foreign_page_packet(path: Path) -> Path:
    """One carrier's three-page report with another carrier's unnumbered page
    bound between its pages."""
    large = _large_run(12)
    return _sheets(path, [
        {"top": ("Page 1 of 3", LARGE_CARRIER, *LETTER), "rows": large[:4]},
        {"top": ("HARBOR CREST SPECIALTY INSURANCE COMPANY", *LETTER),
         "headers": SMALL_HEADERS, "rows": list(SMALL_RUN[:1])},
        {"top": ("Page 2 of 3", LARGE_CARRIER, *LETTER), "rows": large[4:8]},
        {"top": ("Page 3 of 3", LARGE_CARRIER, *LETTER), "rows": large[8:],
         "total": _total(large)},
    ])


#: A seeded hostile case from the fuzz harness: a third carrier's unnumbered
#: page slipped into a two-carrier packet. Same seed and operators, same
#: bytes (``python -m tests.fuzz.run --case 5:add_unnumbered_run``).
HOSTILE_SEED, HOSTILE_OPERATORS = 5, ("add_unnumbered_run",)


def _hostile_packet(path: Path) -> Path:
    rendered = render_fuzz(build_case(HOSTILE_SEED, HOSTILE_OPERATORS), path)
    document = pymupdf.open(rendered)
    data = document.tobytes()
    document.close()
    return _save(pymupdf.open("pdf", data), path)


#: Where the scanned-replay item's recorded vision answers are written.
RECORDINGS = "recordings"


def _scanned_replay(path: Path) -> Path:
    """The golden scanned page, plus a recorded vision answer for it.

    The answer is what a stand-in model transcribes off the synthetic page
    (``tests/test_extract_vision.py``), recorded under the key the app's own
    vision reader uses. Replaying it reads the page exactly as a live model
    that transcribed it correctly would, with no key and no network.
    """
    from core.extract_vision import recording_extractor
    from tests.golden import fixtures as fx
    from tests.test_extract_vision import StandInModel, transcription

    built = _golden("scanned")(path)
    model = StandInModel(transcription(fx.SCANNED))
    with tempfile.TemporaryDirectory() as scratch:
        run_pipeline(built, use_vision=True, profiles_dir=Path(scratch),
                     vision_extractor=recording_extractor(path.parent / RECORDINGS,
                                                          client=model))
    return built


def _golden(name: str) -> Callable[[Path], Path]:
    def build(path: Path) -> Path:
        rendered = render_golden(BY_NAME[name], path)
        document = pymupdf.open(rendered)
        data = document.tobytes()
        document.close()
        return _save(pymupdf.open("pdf", data), path)
    return build


DEMO: tuple[DemoDocument, ...] = (
    DemoDocument("01-clean-single-report.pdf", _golden("qa_travelers_clean"),
                 DocumentStatus.CLEAN, 1, (),
                 "A clean digital loss run: every row ties to the printed totals."),
    DemoDocument("02-two-carrier-packet.pdf", _two_carrier_packet,
                 DocumentStatus.CLEAN, 2, (),
                 "Two carriers in one PDF: two runs, each judged on its own facts."),
    DemoDocument("03-mainframe-credits.pdf", _golden("qa_mainframe_credit"),
                 DocumentStatus.CLEAN, 1, (),
                 "Messy numbers (trailing minus, CR credits) read exactly."),
    DemoDocument("04-arithmetic-error.pdf", _golden("arithmetic_error"),
                 DocumentStatus.NEEDS_REVIEW, 1, ("R-01",),
                 "The carrier's own arithmetic does not add up: flagged, not hidden."),
    DemoDocument("05-foreign-page-packet.pdf", _foreign_page_packet,
                 DocumentStatus.CLEAN, 2, (),
                 "Another carrier's page bound inside a report: read as its own run, "
                 "never folded in."),
    DemoDocument("06-scanned.pdf", _golden("scanned"),
                 DocumentStatus.NEEDS_REVIEW, 1, ("R-22",),
                 "A scanned page with vision off: the page is named as unread."),
    DemoDocument("08-hostile-packet.pdf", _hostile_packet,
                 DocumentStatus.NEEDS_REVIEW, 3, ("R-23", "R-28"),
                 "A third carrier's unnumbered page slipped in: its money is named "
                 "and the boundary flagged, not guessed."),
    DemoDocument("09-scanned-replay.pdf", _scanned_replay,
                 DocumentStatus.NEEDS_REVIEW, 1, ("R-27",),
                 "A scanned page read by vision (recorded answer): values marked "
                 "vision, the count left to a person.", replay=True),
)


#: A broker's saved email: two valuations of one insured's loss run, a
#: duplicate copy and an attachment that is not a loss run. It drives the
#: submission screen (accounts, claims that moved, large claims, evidence).
EMAIL_FILENAME = "07-broker-submission.eml"


def _email(path: Path) -> Path:
    run_2022 = loss_run_pdf(rows=claim_rows(4, big=38000), valuation="12/31/2022",
                            period="01/01/2022 to 12/31/2022")
    run_2023 = loss_run_pdf(rows=claim_rows(4, big=52000), valuation="12/31/2023",
                            period="01/01/2022 to 12/31/2022")
    path.write_bytes(eml([
        ("GL loss run valued 2022.pdf", "application/pdf", run_2022),
        ("GL loss run valued 2023.pdf", "application/pdf", run_2023),
        ("GL loss run valued 2022 (copy).pdf", "application/pdf", run_2022),
        ("schedule of values.xlsx", "application/vnd.ms-excel", b"PK\x03\x04" + b"\0" * 64),
    ]))
    return path


def build(out: Path) -> dict[str, str]:
    """Write the demo pack to ``out``; return filename -> SHA-256."""
    out.mkdir(parents=True, exist_ok=True)
    digests = {}
    for item in DEMO:
        path = item.build(out / item.filename)
        digests[item.filename] = hashlib.sha256(path.read_bytes()).hexdigest()
    email = _email(out / EMAIL_FILENAME)
    digests[EMAIL_FILENAME] = hashlib.sha256(email.read_bytes()).hexdigest()
    for recording in sorted((out / RECORDINGS).glob("*.json")):
        digests[f"{RECORDINGS}/{recording.name}"] = hashlib.sha256(
            recording.read_bytes()).hexdigest()
    return digests


def read(path: Path, *, replay: bool = False):
    """Read one PDF the way the app does with the LLM off.

    Vision is off too, except for the scanned-replay item, which replays its
    recorded answer and never reaches a model."""
    vision: dict = {"use_vision": False}
    if replay:
        from core.extract_vision import replay_extractor

        vision = {"use_vision": True,
                  "vision_extractor": replay_extractor(path.parent / RECORDINGS)}
    with tempfile.TemporaryDirectory() as scratch:
        return run_pipeline(path, profiles_dir=Path(scratch), **vision)


def observe(path: Path, *, replay: bool = False) -> tuple[DocumentStatus, int, set[str]]:
    result = read(path, replay=replay)
    findings = result.reconciliation.findings if result.reconciliation else []
    return (canonical_status(result.reconciliation), len(result.document.runs) or 1,
            {finding.rule_id for finding in findings})


def check(out: Path) -> list[str]:
    """Every way the built pack differs from what the runbook promises."""
    problems = []
    for item in DEMO:
        status, runs, rules = observe(out / item.filename, replay=item.replay)
        if status is not item.status:
            problems.append(f"{item.filename}: status {status.value}, expected {item.status.value}")
        if runs != item.runs:
            problems.append(f"{item.filename}: {runs} run(s), expected {item.runs}")
        missing = set(item.rules) - rules
        if missing:
            problems.append(f"{item.filename}: missing finding(s) {sorted(missing)}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--scan-workbook", action="store_true",
        help="also write the scanned-replay item's workbook (vision cells marked)")
    args = parser.parse_args(argv)
    digests = build(args.out)
    if args.scan_workbook:
        from core.export import write_xlsx

        item = next(entry for entry in DEMO if entry.replay)
        result = read(args.out / item.filename, replay=True)
        target = write_xlsx(result.document, args.out / item.filename.replace(".pdf", ".xlsx"),
                            result.reconciliation)
        print(f"wrote {target.name}")
    for item in DEMO:
        print(f"{item.filename:32} {digests[item.filename][:12]}  {item.beat}")
    print(f"{EMAIL_FILENAME:32} {digests[EMAIL_FILENAME][:12]}  "
          "A broker's email: two valuations of one account, a duplicate, a non-loss-run.")
    if args.check:
        problems = check(args.out)
        for problem in problems:
            print(f"MISMATCH {problem}")
        print("check: OK" if not problems else f"check: {len(problems)} mismatch(es)")
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
