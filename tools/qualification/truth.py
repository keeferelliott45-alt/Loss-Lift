"""Private truth, version 1: what a person verified a document actually says.

A truth file is written by hand, from the printed page, and never from what
LossLift produced. It lives outside the repository beside the manifest: it
holds claim numbers and amounts from real documents.

Every label has one of four states, and none stands in for another:

* ``known`` -- the document prints this value (``"value"`` holds it);
* ``absent`` -- the document prints nothing here (a blank, not a zero);
* ``ambiguous`` -- the page could be read more than one way;
* ``unscorable`` -- the reviewer could not establish it.

A bare JSON string is shorthand for a known value. Money is a finite decimal
*string*: a JSON number is refused, because a float cannot say 0.10. A count
may also be a bare integer.

Truth is complete or it is not qualification. Every page belongs to a run or
is marked outside one; every claim occurrence labels every critical field
(:data:`CRITICAL_FIELDS`) explicitly; the document and each run state the
canonical status a correct reading would have. Validation stops at the first
defect, and its message names positions and field names only -- never a value,
a claim number or a file name.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

TRUTH_VERSION = 1
SHA256 = re.compile(r"[0-9a-f]{64}")
DOCUMENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")

#: Money a loss run is priced from. Scored separately as "money".
MONEY_FIELDS = ("paid_total", "reserve_total", "recovery_total", "incurred_total")
#: Every complete truth labels these on every claim occurrence.
CRITICAL_FIELDS = ("date_of_loss", "claim_status", *MONEY_FIELDS)
#: Further fields a truth may label; scored as "other" when it does.
OPTIONAL_FIELDS = (
    "date_reported", "close_date",
    "paid_indemnity", "paid_medical", "paid_expense",
    "reserve_indemnity", "reserve_medical", "reserve_expense",
)
SCORABLE_FIELDS = (*CRITICAL_FIELDS, *OPTIONAL_FIELDS)
_MONEY_KIND = {*MONEY_FIELDS, "paid_indemnity", "paid_medical", "paid_expense",
               "reserve_indemnity", "reserve_medical", "reserve_expense"}
_DATE_KIND = {"date_of_loss", "date_reported", "close_date"}
CLAIM_STATUSES = ("OPEN", "CLOSED", "CLOSED_PAID", "REOPENED", "REPORT_ONLY", "UNKNOWN")
STATUSES = ("CLEAN", "NEEDS_REVIEW")
FORMAT_FAMILIES = ("digital", "scanned", "mixed")
ADJUDICATIONS = ("adjudicated", "provisional")
PAGE_ROLES = ("claims", "claim_free", "not_loss_run")


class TruthError(ValueError):
    """The truth file cannot be used. The message carries no document content."""


class LabelState(str, Enum):
    KNOWN = "known"
    ABSENT = "absent"
    AMBIGUOUS = "ambiguous"
    UNSCORABLE = "unscorable"


@dataclass(frozen=True)
class Label:
    state: LabelState
    #: Decimal for money and counts, date for dates, str otherwise; None
    #: unless ``state`` is KNOWN.
    value: Any = None

    @property
    def scorable(self) -> bool:
        return self.state in (LabelState.KNOWN, LabelState.ABSENT)


@dataclass(frozen=True)
class Anchor:
    """Where an occurrence is printed. ``row`` breaks ties on one page."""

    page: int
    row: int | None = None


@dataclass(frozen=True)
class ClaimTruth:
    position: int
    claim_number: str | None
    identity: LabelState  # KNOWN, or AMBIGUOUS when the identifier cannot be read
    anchor: Anchor
    fields: Mapping[str, Label]


@dataclass(frozen=True)
class PrintedTruth:
    """What a document, run or section prints about itself."""

    claim_count: Label
    totals: Mapping[str, Label] = field(default_factory=dict)


@dataclass(frozen=True)
class SectionTruth:
    page: int
    printed: PrintedTruth


@dataclass(frozen=True)
class RunTruth:
    run_id: str
    pages: tuple[int, ...]
    status: str
    printed: PrintedTruth
    sections: tuple[SectionTruth, ...] = ()


@dataclass(frozen=True)
class PageTruth:
    page: int
    run_id: str | None
    role: str


@dataclass(frozen=True)
class DocumentTruth:
    document_id: str
    sha256: str
    format_family: str
    adjudication: str
    page_count: int
    pages: tuple[PageTruth, ...]
    runs: tuple[RunTruth, ...]
    status: str
    printed: PrintedTruth
    claims: tuple[ClaimTruth, ...]
    sections: tuple[SectionTruth, ...] = ()

    @property
    def qualifies(self) -> bool:
        """Only adjudicated truth is qualification evidence."""
        return self.adjudication == "adjudicated"

    def run_of_page(self, page: int) -> RunTruth | None:
        for run in self.runs:
            if page in run.pages:
                return run
        return None


@dataclass(frozen=True)
class TruthSet:
    documents: tuple[DocumentTruth, ...]

    def get(self, document_id: str) -> DocumentTruth | None:
        return next((d for d in self.documents if d.document_id == document_id), None)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def _money(raw: Any, where: str) -> Decimal:
    if not isinstance(raw, str):
        raise TruthError(f"{where}: money must be a decimal string, not a JSON number")
    try:
        value = Decimal(raw.strip())
    except InvalidOperation:
        raise TruthError(f"{where}: money is not a decimal") from None
    if not value.is_finite():
        raise TruthError(f"{where}: money must be finite")
    return value


def _count(raw: Any, where: str) -> Decimal:
    if isinstance(raw, bool):
        raise TruthError(f"{where}: a count must be a whole number")
    if isinstance(raw, int):
        text = str(raw)
    elif isinstance(raw, str) and raw.strip().isdigit():
        text = raw.strip()
    else:
        raise TruthError(f"{where}: a count must be a whole number")
    return Decimal(text)


def _date(raw: Any, where: str) -> date:
    if not isinstance(raw, str):
        raise TruthError(f"{where}: a date must be an ISO string")
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        raise TruthError(f"{where}: a date must be ISO (YYYY-MM-DD)") from None


def _label(raw: Any, kind: str, where: str) -> Label:
    """One label, in the long form or the known-value shorthand."""
    if isinstance(raw, dict):
        state = raw.get("state")
        try:
            state = LabelState(state)
        except ValueError:
            raise TruthError(f"{where}: unknown label state") from None
        if state is not LabelState.KNOWN:
            if "value" in raw:
                raise TruthError(f"{where}: only a known label carries a value")
            return Label(state)
        if "value" not in raw:
            raise TruthError(f"{where}: a known label needs a value")
        raw = raw["value"]
    elif raw is None:
        raise TruthError(f"{where}: null is not a label; say absent, ambiguous or unscorable")
    if isinstance(raw, float):
        raise TruthError(f"{where}: floats are refused; write the value as a string")
    if kind == "money":
        return Label(LabelState.KNOWN, _money(raw, where))
    if kind == "count":
        return Label(LabelState.KNOWN, _count(raw, where))
    if kind == "date":
        return Label(LabelState.KNOWN, _date(raw, where))
    if kind == "status":
        if raw not in CLAIM_STATUSES:
            raise TruthError(f"{where}: unknown claim status")
        return Label(LabelState.KNOWN, raw)
    raise TruthError(f"{where}: unknown field kind")  # pragma: no cover


def _kind(name: str) -> str:
    if name in _MONEY_KIND:
        return "money"
    if name in _DATE_KIND:
        return "date"
    return "status"


def _printed(raw: Any, where: str) -> PrintedTruth:
    if not isinstance(raw, dict):
        raise TruthError(f"{where}: printed facts must be an object")
    if "claim_count" not in raw:
        raise TruthError(f"{where}: the printed claim count must be labelled (absent if none)")
    totals_raw = raw.get("totals", {})
    if not isinstance(totals_raw, dict):
        raise TruthError(f"{where}: printed totals must be an object")
    totals: dict[str, Label] = {}
    for name, value in totals_raw.items():
        if name not in _MONEY_KIND:
            raise TruthError(f"{where}: unknown printed total field")
        totals[name] = _label(value, "money", f"{where} total {name}")
    return PrintedTruth(_label(raw["claim_count"], "count", f"{where} claim count"), totals)


def _int(raw: Any, where: str, minimum: int = 1) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < minimum:
        raise TruthError(f"{where}: must be a whole number of at least {minimum}")
    return raw


def _sections(raw: Any, page_count: int, where: str) -> tuple[SectionTruth, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise TruthError(f"{where}: sections must be a list")
    sections = []
    for index, item in enumerate(raw, start=1):
        spot = f"{where} section {index}"
        if not isinstance(item, dict):
            raise TruthError(f"{spot}: must be an object")
        page = _int(item.get("page"), f"{spot} page")
        if page > page_count:
            raise TruthError(f"{spot}: page is beyond the document")
        sections.append(SectionTruth(page, _printed(item.get("printed"), spot)))
    return tuple(sections)


def parse_document(raw: Any, position: int) -> DocumentTruth:
    where = f"document {position}"
    if not isinstance(raw, dict):
        raise TruthError(f"{where}: must be an object")
    doc_id = raw.get("id")
    if not isinstance(doc_id, str) or not DOCUMENT_ID.fullmatch(doc_id):
        raise TruthError(f"{where}: unusable id")
    where = f"document {doc_id}"
    sha = raw.get("sha256")
    if not isinstance(sha, str) or not SHA256.fullmatch(sha):
        raise TruthError(f"{where}: sha256 must be 64 lowercase hex characters")
    family = raw.get("format_family")
    if family not in FORMAT_FAMILIES:
        raise TruthError(f"{where}: unknown format family")
    adjudication = raw.get("adjudication")
    if adjudication not in ADJUDICATIONS:
        raise TruthError(f"{where}: adjudication must be adjudicated or provisional")
    page_count = _int(raw.get("page_count"), f"{where} page_count")
    status = raw.get("status")
    if status not in STATUSES:
        raise TruthError(f"{where}: the document's canonical status must be labelled")

    runs_raw = raw.get("runs")
    if not isinstance(runs_raw, list):
        raise TruthError(f"{where}: runs must be a list (empty for none)")
    runs: list[RunTruth] = []
    for index, item in enumerate(runs_raw, start=1):
        spot = f"{where} run {index}"
        if not isinstance(item, dict):
            raise TruthError(f"{spot}: must be an object")
        run_id = item.get("id")
        if not isinstance(run_id, str) or not DOCUMENT_ID.fullmatch(run_id):
            raise TruthError(f"{spot}: unusable id")
        if any(run.run_id == run_id for run in runs):
            raise TruthError(f"{spot}: repeats another run's id")
        pages = item.get("pages")
        if not isinstance(pages, list) or not pages:
            raise TruthError(f"{spot}: must list its pages")
        pages_t = tuple(_int(p, f"{spot} page") for p in pages)
        if len(set(pages_t)) != len(pages_t) or max(pages_t) > page_count:
            raise TruthError(f"{spot}: pages repeat or lie beyond the document")
        run_status = item.get("status")
        if run_status not in STATUSES:
            raise TruthError(f"{spot}: the run's canonical status must be labelled")
        runs.append(RunTruth(run_id, tuple(sorted(pages_t)), run_status,
                             _printed(item.get("printed"), spot),
                             _sections(item.get("sections"), page_count, spot)))

    pages_raw = raw.get("pages")
    if not isinstance(pages_raw, list):
        raise TruthError(f"{where}: every page's membership must be listed")
    pages: dict[int, PageTruth] = {}
    for index, item in enumerate(pages_raw, start=1):
        spot = f"{where} page entry {index}"
        if not isinstance(item, dict):
            raise TruthError(f"{spot}: must be an object")
        page = _int(item.get("page"), f"{spot} page")
        if page > page_count or page in pages:
            raise TruthError(f"{spot}: page repeats or lies beyond the document")
        role = item.get("role")
        if role not in PAGE_ROLES:
            raise TruthError(f"{spot}: unknown page role")
        run_id = item.get("run")
        if run_id is not None and not any(run.run_id == run_id for run in runs):
            raise TruthError(f"{spot}: names a run the truth does not define")
        pages[page] = PageTruth(page, run_id, role)
    if sorted(pages) != list(range(1, page_count + 1)):
        raise TruthError(f"{where}: pages are missing from the membership list")
    for run in runs:
        members = tuple(sorted(p for p, entry in pages.items() if entry.run_id == run.run_id))
        if members != run.pages:
            raise TruthError(f"{where}: run {run.run_id} and the page list disagree")
    if runs and any(entry.role == "claims" and entry.run_id is None for entry in pages.values()):
        raise TruthError(f"{where}: a page holding claims belongs to no run")

    claims_raw = raw.get("claims")
    if not isinstance(claims_raw, list):
        raise TruthError(f"{where}: claims must be a list (empty for none)")
    claims: list[ClaimTruth] = []
    seen_anchors: set[tuple[str, int, int]] = set()
    for index, item in enumerate(claims_raw, start=1):
        spot = f"{where} claim occurrence {index}"
        if not isinstance(item, dict):
            raise TruthError(f"{spot}: must be an object")
        try:
            identity = LabelState(item.get("identity", "known"))
        except ValueError:
            raise TruthError(f"{spot}: identity must be known or ambiguous") from None
        if identity not in (LabelState.KNOWN, LabelState.AMBIGUOUS):
            raise TruthError(f"{spot}: identity must be known or ambiguous")
        number = item.get("claim_number")
        if identity is LabelState.KNOWN:
            if not isinstance(number, str) or not number.strip():
                raise TruthError(f"{spot}: a known identity needs its printed claim number")
            number = " ".join(number.split())
        elif number is not None:
            raise TruthError(f"{spot}: an ambiguous identity carries no claim number")
        anchor_raw = item.get("anchor")
        if not isinstance(anchor_raw, dict):
            raise TruthError(f"{spot}: needs a source anchor")
        page = _int(anchor_raw.get("page"), f"{spot} anchor page")
        if page > page_count:
            raise TruthError(f"{spot}: anchor page is beyond the document")
        if pages[page].role != "claims":
            raise TruthError(f"{spot}: anchored on a page the truth says holds no claims")
        row = anchor_raw.get("row")
        if row is not None:
            row = _int(row, f"{spot} anchor row", minimum=0)
            if identity is LabelState.KNOWN:
                key = (number, page, row)
                if key in seen_anchors:
                    raise TruthError(f"{spot}: repeats another occurrence's anchor")
                seen_anchors.add(key)
        fields_raw = item.get("fields")
        if not isinstance(fields_raw, dict):
            raise TruthError(f"{spot}: needs its field labels")
        labels: dict[str, Label] = {}
        for name, value in fields_raw.items():
            if name not in SCORABLE_FIELDS:
                raise TruthError(f"{spot}: unknown field")
            labels[name] = _label(value, _kind(name), f"{spot} field {name}")
        missing = [name for name in CRITICAL_FIELDS if name not in labels]
        if missing:
            raise TruthError(f"{spot}: critical fields not labelled: {', '.join(missing)}")
        claims.append(ClaimTruth(index, number, identity, Anchor(page, row), labels))

    return DocumentTruth(
        document_id=doc_id, sha256=sha, format_family=family, adjudication=adjudication,
        page_count=page_count, pages=tuple(pages[p] for p in sorted(pages)),
        runs=tuple(runs), status=status, printed=_printed(raw.get("printed"), where),
        claims=tuple(claims), sections=_sections(raw.get("sections"), page_count, where),
    )


def parse(data: Any) -> TruthSet:
    if not isinstance(data, dict) or data.get("version") != TRUTH_VERSION:
        raise TruthError(f"the truth file must be a version {TRUTH_VERSION} object")
    documents = data.get("documents")
    if not isinstance(documents, list) or not documents:
        raise TruthError("the truth file lists no documents")
    parsed = [parse_document(item, index) for index, item in enumerate(documents, start=1)]
    ids = [d.document_id for d in parsed]
    if len(set(ids)) != len(ids):
        raise TruthError("a document id appears twice in the truth file")
    return TruthSet(tuple(parsed))


def load(path: Path) -> TruthSet:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError:
        raise TruthError("the truth file cannot be read") from None
    except (ValueError, UnicodeDecodeError):
        raise TruthError("the truth file is not valid JSON") from None
    return parse(data)


def check_against_manifest(truth: TruthSet, entries: Iterable[Any]) -> None:
    """Every truth document is a manifest document, with the same bytes."""
    by_id = {entry.id: entry for entry in entries}
    for document in truth.documents:
        entry = by_id.get(document.document_id)
        if entry is None:
            raise TruthError(f"document {document.document_id}: not in the manifest")
        if entry.sha256 != document.sha256:
            raise TruthError(
                f"document {document.document_id}: truth was written for other bytes "
                f"than the manifest lists")
