"""The frozen interface for synthetic qualification cases.

A pack is a module exporting::

    build_cases(root: Path) -> list[QualificationCase]

It writes each case's PDF under ``root`` (a temporary directory the caller
owns) and returns the cases. Its truth comes from how the document was
built -- never from what LossLift reads back. A pack never edits a discovery
registry; the lead lists packs centrally.

A case is either:

* ``SUPPORTED`` -- LossLift should read it exactly: every claim, every
  critical field, the runs, and the canonical status the truth states;
* ``REVIEW`` -- a correct reading is NEEDS_REVIEW. It must not be
  auto-accepted, and its claim and field errors are still measured: a
  review case is not a perfect extraction by definition.

PDF bytes need not be identical from one build to the next; what is printed,
the truth and the evaluated result must be. The truth's ``sha256`` is the
hash of the bytes this build wrote, so a case is always scored against the
file it describes.

Scanned pages are read only offline: through a directory of synthetic
replay recordings or an injected extractor, never a live model.
"""

from __future__ import annotations

import hashlib
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from tools.qualification.score import QualificationMetrics, score_result
from tools.qualification.truth import DocumentTruth, TruthError, parse_document

CASE_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
#: The variables a live model call would need; hidden while a case runs.
LIVE_MODEL_VARIABLES = ("GEMINI_API_KEY", "GOOGLE_API_KEY")


class Expectation(str, Enum):
    SUPPORTED = "supported"
    REVIEW = "review"


VisionExtractor = Callable[[Any, Sequence[int]], Any]


@dataclass(frozen=True)
class QualificationCase:
    case_id: str
    pdf: Path
    truth: DocumentTruth
    expectation: Expectation
    #: A directory of synthetic vision recordings (``core.extract_vision``
    #: replay format), or an offline extractor. At most one.
    replay: Path | None = None
    extractor: VisionExtractor | None = None

    def __post_init__(self) -> None:
        if not CASE_ID.fullmatch(self.case_id):
            raise TruthError("a case id must be lowercase letters, digits, '.', '_' or '-'")
        if self.truth.document_id != self.case_id:
            raise TruthError(f"case {self.case_id}: its truth names another document")
        if self.replay is not None and self.extractor is not None:
            raise TruthError(f"case {self.case_id}: give a replay directory or an "
                             f"extractor, not both")
        if not isinstance(self.expectation, Expectation):
            raise TruthError(f"case {self.case_id}: unknown expectation")
        if self.expectation is Expectation.REVIEW and self.truth.status != "NEEDS_REVIEW":
            raise TruthError(f"case {self.case_id}: a review case's truth must be NEEDS_REVIEW")


CaseBuilder = Callable[[Path], list[QualificationCase]]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def document_truth(case_id: str, pdf: Path, spec: Mapping[str, Any]) -> DocumentTruth:
    """Truth for a generated PDF: ``spec`` is a truth document without its
    ``id`` and ``sha256``, which are taken from the case and the bytes."""
    data = dict(spec)
    data["id"] = case_id
    data["sha256"] = sha256_file(pdf)
    return parse_document(data, 1)


@contextmanager
def offline() -> Iterator[None]:
    """Hide live-model credentials for the duration of a run."""
    saved = {name: os.environ.pop(name) for name in LIVE_MODEL_VARIABLES
             if name in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)


def vision_arguments(replay: Path | None, extractor: VisionExtractor | None) -> dict[str, Any]:
    """Pipeline keyword arguments that read scanned pages offline or not at all."""
    if extractor is not None:
        return {"use_vision": True, "vision_extractor": extractor}
    if replay is not None:
        from core.extract_vision import replay_extractor

        return {"use_vision": True, "vision_extractor": replay_extractor(replay)}
    return {"use_vision": False}


def run_case(case: QualificationCase, work: Path) -> Any:
    """Read a case's PDF with the current checkout, offline, with its own profiles."""
    from core.pipeline import run_pipeline

    if sha256_file(case.pdf) != case.truth.sha256:
        raise TruthError(f"case {case.case_id}: the PDF is not the file its truth describes")
    profiles = Path(work) / "profiles" / case.case_id
    profiles.mkdir(parents=True, exist_ok=True)
    with offline():
        return run_pipeline(case.pdf, use_llm=False, profiles_dir=profiles,
                            **vision_arguments(case.replay, case.extractor))


@dataclass(frozen=True)
class CaseOutcome:
    case_id: str
    expectation: Expectation
    metrics: QualificationMetrics
    met: bool
    #: Fixed categories naming each way the expectation was not met.
    shortfalls: tuple[str, ...]


def check_expectation(case: QualificationCase, metrics: QualificationMetrics
                      ) -> tuple[str, ...]:
    """Every way a case's result falls short of what it declares; empty if none."""
    shortfalls: list[str] = []
    status = metrics.status
    if status.false_clean_documents or status.false_clean_runs:
        shortfalls.append("false_clean")
    if case.expectation is Expectation.REVIEW:
        if status.documents_auto_accepted:
            shortfalls.append("auto_accepted_review_case")
        return tuple(shortfalls)
    claims = metrics.overall.claims
    critical = metrics.overall.critical
    for name in ("missing", "invented", "wrong_run", "ambiguous_truth"):
        if getattr(claims, name):
            shortfalls.append(f"claims_{name}")
    for name in ("incorrect", "missing", "extra", "null_as_zero"):
        if getattr(critical, name):
            shortfalls.append(f"critical_{name}")
    if metrics.accounting.runs_matched != metrics.accounting.truth_runs:
        shortfalls.append("runs_differ")
    if status.documents_agree != status.documents:
        shortfalls.append("status_differs")
    return tuple(shortfalls)


def evaluate_case(case: QualificationCase, work: Path) -> CaseOutcome:
    result = run_case(case, work)
    metrics = score_result(result, case.truth)
    shortfalls = check_expectation(case, metrics)
    return CaseOutcome(case.case_id, case.expectation, metrics, not shortfalls, shortfalls)


def build_all(builders: Iterable[CaseBuilder], root: Path) -> list[QualificationCase]:
    """Every case from every builder, each builder in its own directory."""
    cases: list[QualificationCase] = []
    for position, build in enumerate(builders, start=1):
        target = Path(root) / f"pack-{position}"
        target.mkdir(parents=True, exist_ok=True)
        cases.extend(build(target))
    ids = [case.case_id for case in cases]
    if len(set(ids)) != len(ids):
        raise TruthError("two cases share an id")
    return cases
