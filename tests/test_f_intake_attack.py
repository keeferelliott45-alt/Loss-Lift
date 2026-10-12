"""Stage F — blind adversarial tests for CSV/XLSX intake.

Written from the behaviour contract only, never from the implementation:

(a) each VISIBLE non-empty worksheet is a separate source/run — sheets are
    never combined;
(b) row/cell provenance (sheet + row identity) is preserved and replay is
    deterministic;
(c) formulas are NEVER evaluated — a cached result is used when one exists,
    otherwise the value is null with a finding, never zero;
(d) an ambiguous, missing or unreadable value never silently becomes zero or
    a clean result;
(e) ambiguity fails closed.

The exact intake API is unknown to a blind adversary, so the entry point is
discovered from a list of plausible spellings (a function taking
``(data: bytes, filename: str)`` that returns a parsed result exposing a
document view).  If none exists the tests fail loudly rather than skip.
Every test asserts the SAFE (fail-closed) outcome.  All fixtures are
synthetic, built in memory with openpyxl or hand-rolled OOXML; nothing here
touches the private corpus or the network (LLM keys are removed per-test).
"""

from __future__ import annotations

import csv as _csv
import importlib
import io
import zipfile
from decimal import Decimal

import pytest

pytest.importorskip("openpyxl")
import openpyxl

from core.ingest import MAX_UPLOAD_BYTES


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """The intake must never depend on an LLM; force the offline path."""
    for variable in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(variable, raising=False)


# ---------------------------------------------------------------------------
# Entry-point discovery
# ---------------------------------------------------------------------------

_ENTRY_CANDIDATES = [
    ("core.spreadsheet", ("read_workbook", "read", "parse_workbook", "parse", "ingest", "load")),
    ("core.tabular", ("read_workbook", "read", "parse_workbook", "parse", "ingest")),
    ("core.sheets", ("read_workbook", "read", "parse", "ingest")),
    ("core.workbook", ("read", "parse", "ingest")),
    (
        "core.ingest",
        (
            "ingest_spreadsheet",
            "ingest_workbook",
            "ingest_tabular",
            "ingest_csv",
            "ingest_xlsx",
            "ingest_csv_or_xlsx",
            "read_spreadsheet",
            "parse_spreadsheet",
            "read_csv",
            "read_xlsx",
        ),
    ),
    ("core.intake", ("read_workbook", "read", "parse", "ingest")),
]

_ENTRY = None


def _entry():
    global _ENTRY
    if _ENTRY is not None:
        return _ENTRY
    tried = []
    for module_name, function_names in _ENTRY_CANDIDATES:
        try:
            module = importlib.import_module(module_name)
        except ImportError as error:
            tried.append(f"{module_name}: import failed ({error})")
            continue
        for function_name in function_names:
            function = getattr(module, function_name, None)
            if callable(function):
                _ENTRY = function
                return function
        tried.append(f"{module_name}: none of {', '.join(function_names)}")
    pytest.fail(
        "no CSV/XLSX intake entry point found (contract: bytes+filename in, "
        "parsed result out). Tried: " + "; ".join(tried)
    )


def _read(data: bytes, filename: str):
    return _entry()(data, filename)


def _rejected(data: bytes, filename: str) -> bool:
    """True when the intake refuses the bytes outright (fail closed)."""
    function = _entry()  # resolve outside the try: a discovery failure is a test failure
    try:
        function(data, filename)
    except Exception:
        return True
    return False


# ---------------------------------------------------------------------------
# Tolerant views over the intake result
# ---------------------------------------------------------------------------

_NAMED_CHILDREN = (
    "document",
    "doc",
    "loss_run",
    "parsed",
    "extraction",
    "result",
    "intake",
    "reconciliation",
    "reconcile",
    "review",
    "review_result",
)


def _members(obj, _depth: int = 0):
    if obj is None or _depth > 4:
        return
    yield obj
    if isinstance(obj, (tuple, list)):
        for item in obj:
            yield from _members(item, _depth + 1)
        return
    if isinstance(obj, (str, bytes, int, float, bool, Decimal)):
        return
    for name in _NAMED_CHILDREN:
        child = getattr(obj, name, None)
        if child is not None and child is not obj:
            yield from _members(child, _depth + 1)


def _try_doc(result):
    for member in _members(result):
        if hasattr(member, "claims") and hasattr(member, "refused_claim_rows"):
            return member
    return None


def _doc(result):
    doc = _try_doc(result)
    if doc is None:
        pytest.fail(
            f"intake result {result!r} exposes no document view "
            "(claims + refused_claim_rows) — cannot audit claim accountability"
        )
    return doc


def _claims(result):
    return list(_doc(result).claims)


def _refused(result):
    return list(_doc(result).refused_claim_rows)


def _unplaced(result):
    return list(getattr(_doc(result), "unplaced_rows", []) or [])


def _accounted(result) -> int:
    return len(_claims(result)) + len(_refused(result)) + len(_unplaced(result))


_MONEY_FIELDS = (
    "paid_total",
    "incurred_total",
    "paid_indemnity",
    "reserve_total",
    "recovery_total",
)


def _amount(claim):
    for name in _MONEY_FIELDS:
        value = getattr(claim, name, None)
        if value is not None:
            return value
    return None


def _prov(claim) -> str:
    return " ".join(
        str(part)
        for part in (
            getattr(claim, "source_page", None),
            getattr(claim, "source_row", None),
            getattr(claim, "row_id", ""),
            tuple(getattr(claim, "source_lines", []) or ()),
            getattr(claim, "source_method", ""),
            getattr(claim, "read_method", ""),
            getattr(claim, "raw_cells", {}),
            getattr(claim, "original_values", {}),
            getattr(claim, "source_bbox", None),
        )
    ).lower()


def _findings(result) -> list[str]:
    texts = []
    for member in _members(result):
        found = getattr(member, "findings", None)
        if isinstance(found, (list, tuple)):
            for finding in found:
                rule_id = getattr(finding, "rule_id", None)
                message = getattr(finding, "message", None)
                if rule_id is not None and message is not None:
                    texts.append(
                        f"{rule_id} {message} "
                        f"{getattr(finding, 'severity', '')} "
                        f"{getattr(finding, 'category', '')}".lower()
                    )
    return texts


def _status(result):
    for member in _members(result):
        status = getattr(member, "status", None)
        findings = getattr(member, "findings", None)
        if status is not None and isinstance(findings, (list, tuple)):
            return str(getattr(status, "value", status)).lower()
    return None


def _is_clean(result) -> bool:
    status = _status(result)
    return status is not None and "clean" in status


def _flagged(result, claims=()) -> bool:
    """A finding, a per-field null reason, or any recorded issue exists."""
    if _findings(result):
        return True
    for claim in claims:
        if getattr(claim, "field_issues", None) or getattr(claim, "original_issues", None):
            return True
    return False


def _dump(result) -> str:
    parts = [repr(result)]
    doc = _try_doc(result)
    if doc is not None and doc is not result:
        parts.append(repr(doc))
    return " ".join(parts)


def _separated_per_sheet(result, names) -> bool:
    """True when the result shows per-sheet source identity somewhere.

    Safe evidence: one run/source per sheet, a sheets collection, the sheet
    names appearing in claim provenance, or at least one distinct
    source_page per sheet.  A single flat claim set with identical page
    provenance for every sheet is the combined (unsafe) shape.
    """
    doc = _try_doc(result)
    if doc is None:
        return False
    runs = list(getattr(doc, "runs", []) or [])
    if len(runs) >= len(names):
        return True
    sheets = getattr(result, "sheets", None)
    if sheets is not None and len(list(sheets)) >= len(names):
        return True
    claims = list(doc.claims)
    named = 0
    for name in names:
        token = name.lower()
        if any(token in _prov(claim) or token in repr(claim).lower() for claim in claims):
            named += 1
    if named == len(names):
        return True
    pages = {getattr(claim, "source_page", None) for claim in claims}
    return len(pages) >= len(names)


# ---------------------------------------------------------------------------
# Synthetic fixtures (in memory only)
# ---------------------------------------------------------------------------

HEADER = ["Claim Number", "Loss Date", "Paid"]


def _std_rows(*data_rows):
    return [HEADER, *data_rows]


def _xlsx(sheets: dict, hidden: frozenset = frozenset(), very_hidden: frozenset = frozenset()) -> bytes:
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets.items():
        sheet = workbook.create_sheet(title=name)
        for row in rows:
            sheet.append(list(row))
        if name in hidden:
            sheet.sheet_state = "hidden"
        if name in very_hidden:
            sheet.sheet_state = "veryHidden"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _csv_bytes(rows) -> bytes:
    buffer = io.StringIO()
    _csv.writer(buffer).writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _xlsx_run(rows) -> bytes:
    """One default sheet named "Run", from a list of rows."""
    return _xlsx({"Run": rows})


_XLSX_AND_CSV = pytest.mark.parametrize(
    "build", [_xlsx_run, _csv_bytes], ids=["xlsx", "csv"]
)


_OLE_BYTES = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512

_OOXML_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
    "</Types>"
)

_OOXML_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
    "</Relationships>"
)

_OOXML_WORKBOOK = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    '<sheets><sheet name="Run" sheetId="1" r:id="rId1"/></sheets></workbook>'
)

_OOXML_WORKBOOK_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
    "</Relationships>"
)


def _raw_ooxml_with_cached_formula() -> bytes:
    """An .xlsx whose B2 holds formula ``=1+1`` with a *cached* result of 42.

    openpyxl cannot write a cached value, so the OOXML is hand-rolled: if the
    intake evaluates the expression it reads 2; if it uses the cache it reads
    42 (this is exactly what openpyxl's ``data_only=True`` returns).
    """

    def cell(ref: str, text: str) -> str:
        return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'

    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        '<row r="1">' + cell("A1", "Claim Number") + cell("B1", "Paid") + "</row>"
        '<row r="2">' + cell("A2", "F-100") + '<c r="B2"><f>1+1</f><v>42</v></c></row>'
        "</sheetData></worksheet>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", _OOXML_CONTENT_TYPES)
        archive.writestr("_rels/.rels", _OOXML_RELS)
        archive.writestr("xl/workbook.xml", _OOXML_WORKBOOK)
        archive.writestr("xl/_rels/workbook.xml.rels", _OOXML_WORKBOOK_RELS)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# (c) Formulas are never evaluated
# ---------------------------------------------------------------------------


def test_a_formula_cell_without_a_cached_result_is_never_evaluated_or_zeroed():
    data = _xlsx({"Run": _std_rows(["F-100", "2024-03-01", "=1+1"])})
    result = _read(data, "formula-no-cache.xlsx")
    claims, refused, unplaced = _claims(result), _refused(result), _unplaced(result)
    assert claims or refused or unplaced, "the formula row silently disappeared"
    for claim in claims:
        amount = _amount(claim)
        assert amount is None or amount != 0, (
            f"a formula cell with no cached result became zero: {amount!r}"
        )
        assert amount != 2, "the formula was evaluated instead of being flagged"
    if claims and not refused and not unplaced:
        assert _flagged(result, claims), (
            "a formula cell with no cached result was read without a finding, "
            "a null reason, or any other flag"
        )


def test_a_formula_cell_uses_the_cached_result_not_the_expression():
    data = _raw_ooxml_with_cached_formula()
    result = _read(data, "formula-cached.xlsx")
    claims = _claims(result)
    assert claims, "the row with a cached formula result silently disappeared"
    amounts = [_amount(claim) for claim in claims]
    assert any(amount == 42 for amount in amounts), (
        f"the cached result 42 was not used: {amounts!r}"
    )
    assert not any(amount == 2 for amount in amounts), (
        "the formula expression was evaluated instead of using the cached result"
    )


# ---------------------------------------------------------------------------
# (a) Sheets are separate sources, never combined, never silently dropped
# ---------------------------------------------------------------------------


def test_two_visible_sheets_are_never_combined_into_one_source():
    data = _xlsx(
        {
            "Jan": _std_rows(["J-1", "2024-01-15", 100], ["J-2", "2024-01-20", 50]),
            "Feb": _std_rows(["F-1", "2024-02-15", 200], ["F-2", "2024-02-20", 75]),
        }
    )
    result = _read(data, "two-sheets.xlsx")
    assert _accounted(result) >= 4, (
        f"sheets combined or rows lost: {_accounted(result)} rows accounted for"
    )
    claims = _claims(result)
    assert len(claims) == 4, f"expected 4 claims across the two sheets, got {len(claims)}"
    assert _separated_per_sheet(result, ("Jan", "Feb")), (
        "two visible sheets were collapsed into a single source with no "
        "per-sheet identity (no runs/sheets split, no sheet name or distinct "
        "source page anywhere in the claims' provenance)"
    )


@pytest.mark.parametrize("state", ["hidden", "veryHidden"])
def test_a_hidden_sheet_is_never_silently_dropped(state):
    data = _xlsx(
        {
            "Main": _std_rows(["M-1", "2024-03-01", 100]),
            "Ghost": _std_rows(["G-9", "2024-03-02", 999]),
        },
        hidden={"Ghost"} if state == "hidden" else frozenset(),
        very_hidden={"Ghost"} if state == "veryHidden" else frozenset(),
    )
    result = _read(data, f"hidden-sheet-{state}.xlsx")
    dump = _dump(result)
    assert "M-1" in dump, "the visible sheet's claim was lost"
    ghost = next(
        (
            claim
            for claim in _claims(result)
            if "g-9" in repr(claim).lower() or "g-9" in _prov(claim)
        ),
        None,
    )
    if ghost is None and "G-9" not in dump:
        texts = _findings(result)
        assert any("hidden" in text or "ghost" in text for text in texts), (
            f"the {state} sheet's claim vanished without being read or recorded"
        )
    if ghost is not None:
        assert _amount(ghost) == 999, "the hidden sheet's amount was altered"


def test_a_visible_blank_sheet_is_recorded_not_silently_ignored():
    data = _xlsx({"Mar": _std_rows(["M3-1", "2024-03-01", 100]), "Apr": []})
    result = _read(data, "blank-sheet.xlsx")
    dump = _dump(result)
    assert "M3-1" in dump, "the populated sheet was lost"
    assert "apr" in dump.lower() or _findings(result), (
        "a visible blank worksheet was silently ignored: never read, never "
        "recorded, and never flagged"
    )


# ---------------------------------------------------------------------------
# (d) Null is never a zero, and a printed zero is never absent
# ---------------------------------------------------------------------------


@_XLSX_AND_CSV
def test_a_blank_amount_cell_is_never_zero(build):
    data = build(_std_rows(["N-1", "2024-03-01", None]))
    result = _read(data, f"blank-amount.{build.__name__}")
    claims, refused, unplaced = _claims(result), _refused(result), _unplaced(result)
    assert claims or refused or unplaced, "the row with a blank amount silently disappeared"
    for claim in claims:
        amount = _amount(claim)
        assert amount is None or amount != 0, f"blank cell became zero: {amount!r}"
    if claims and not refused and not unplaced:
        assert _flagged(result, claims), (
            "a blank amount was read into a claim with no null reason or finding"
        )


def test_a_printed_zero_is_preserved_not_turned_absent():
    data = _xlsx(
        {"Run": _std_rows(["Z-1", "2024-03-01", 0], ["Z-2", "2024-03-02", "0.00"])}
    )
    result = _read(data, "printed-zero.xlsx")
    claims = _claims(result)
    assert len(claims) == 2, (
        f"printed zeros made rows disappear: {len(claims)} claims, "
        f"{len(_refused(result))} refused, {len(_unplaced(result))} unplaced"
    )
    amounts = [_amount(claim) for claim in claims]
    assert all(amount is not None for amount in amounts), (
        f"a printed zero was read as absent/null: {amounts!r}"
    )
    assert all(amount == 0 for amount in amounts), f"printed zeros were altered: {amounts!r}"


# ---------------------------------------------------------------------------
# (d)/(e) Headers: malformed, duplicate, missing — never guessed silently
# ---------------------------------------------------------------------------


def test_a_garbled_amount_header_is_never_silently_guessed():
    data = _xlsx(
        {"Run": [["Claim Number", "Loss Date", "Totl Pa1d"], ["X-1", "2024-03-01", 500]]}
    )
    result = _read(data, "garbled-header.xlsx")
    claims, refused, unplaced = _claims(result), _refused(result), _unplaced(result)
    assert claims or refused or unplaced or _findings(result), (
        "the row vanished when its amount header could not be read"
    )
    if any(_amount(claim) is not None for claim in claims):
        assert _flagged(result, claims), (
            "an amount was read under a garbled header with no mapping finding"
        )
        assert not _is_clean(result), "a garbled-header document reconciled clean"


def test_a_missing_header_row_is_never_a_silent_positional_guess():
    data = _xlsx(
        {"Run": [["X-1", "2024-03-01", 500], ["X-2", "2024-03-02", 250]]}
    )
    result = _read(data, "no-header-row.xlsx")
    accounted = _accounted(result)
    assert accounted == 2 or _findings(result), (
        f"a claim-like row vanished (likely consumed as a header): "
        f"{accounted} rows accounted for"
    )
    if accounted == 2 and any(_amount(claim) is not None for claim in _claims(result)):
        assert _flagged(result, _claims(result)) or not _is_clean(result), (
            "a header-less sheet was silently read positionally into a clean result"
        )


def test_duplicate_headers_are_never_silently_arbitrated():
    data = _xlsx(
        {
            "Run": [
                ["Claim Number", "Claim Number", "Paid"],
                ["D-1", "identity-pollution", 100],
            ]
        }
    )
    result = _read(data, "duplicate-headers.xlsx")
    claims, refused, unplaced = _claims(result), _refused(result), _unplaced(result)
    assert claims or refused or unplaced or _findings(result), "the row vanished"
    assert _findings(result) or not _is_clean(result), (
        "two columns sharing one header were silently arbitrated into a "
        "clean result"
    )
    for claim in claims:
        if claim.claim_number == "identity-pollution":
            assert _flagged(result, claims), (
                "the wrong column was taken as the claim number without a flag"
            )


# ---------------------------------------------------------------------------
# (Invariant 1) A claim-like row is never silently dropped
# ---------------------------------------------------------------------------


@_XLSX_AND_CSV
def test_a_claim_like_row_with_an_unreadable_amount_is_never_silently_dropped(build):
    data = build(
        _std_rows(["A-1", "2024-03-01", 100], ["A-2", "2024-03-02", "see appendix"])
    )
    result = _read(data, f"unreadable-amount.{build.__name__}")
    dump = _dump(result)
    assert "A-2" in dump or any("a-2" in _prov(claim) for claim in _claims(result)), (
        "the claim-like row with an unreadable amount disappeared without "
        "being read or recorded"
    )
    for claim in _claims(result):
        if "a-2" in repr(claim).lower():
            amount = _amount(claim)
            assert amount is None or amount != 0, "an unreadable amount became zero"
            assert _flagged(result, _claims(result)), (
                "an unreadable amount was read into a claim without a finding"
            )


# ---------------------------------------------------------------------------
# (b) Provenance and deterministic replay
# ---------------------------------------------------------------------------


def test_every_claim_keeps_sheet_and_row_provenance():
    data = _xlsx(
        {
            "Alpha": _std_rows(["P-1", "2024-03-01", 100], ["P-2", "2024-03-02", 200]),
            "Beta": _std_rows(["P-3", "2024-04-01", 300]),
        }
    )
    result = _read(data, "provenance.xlsx")
    claims = _claims(result)
    assert len(claims) == 3, f"claims lost across sheets: {len(claims)} read"
    identities = set()
    for claim in claims:
        identity = (
            getattr(claim, "source_page", None),
            getattr(claim, "source_row", None),
            getattr(claim, "row_id", ""),
            tuple(getattr(claim, "source_lines", []) or ()),
        )
        assert any(part not in (None, "", ()) for part in identity), (
            f"claim {claim.claim_number!r} carries no sheet/row provenance"
        )
        identities.add(identity)
    assert len(identities) == len(claims), "two claims share one provenance identity"
    assert _separated_per_sheet(result, ("Alpha", "Beta")), (
        "the two sheets were combined without per-sheet identity"
    )


def test_reading_the_same_bytes_twice_is_deterministic():
    data = _xlsx(
        {"Run": _std_rows(["T-1", "2024-03-01", 100], ["T-2", "2024-03-02", 250])}
    )

    def snapshot(result):
        claims = sorted(
            getattr(claim, "model_dump_json", lambda: repr(claim))()
            for claim in _claims(result)
        )
        return claims, sorted(_findings(result)), _status(result), _accounted(result)

    first = snapshot(_read(data, "determinism.xlsx"))
    second = snapshot(_read(data, "determinism.xlsx"))
    assert first == second, "the same bytes read twice gave different results"


# ---------------------------------------------------------------------------
# (e) Oversized, mislabelled and legacy files fail closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename, data, label",
    [
        (
            "table.csv",
            _xlsx({"Run": _std_rows(["K-1", "2024-03-01", 1])}),
            "xlsx bytes named .csv",
        ),
        (
            "table.xlsx",
            _csv_bytes(_std_rows(["K-2", "2024-03-02", 2])),
            "csv bytes named .xlsx",
        ),
        ("legacy.xls", _OLE_BYTES, "legacy OLE .xls"),
        ("table.xlsx", _OLE_BYTES, "OLE bytes named .xlsx"),
    ],
)
def test_extension_and_magic_mismatches_are_refused_not_clean(filename, data, label):
    function = _entry()  # resolve outside the try: discovery failure is a test failure
    try:
        result = function(data, filename)
    except Exception:
        return  # refused outright — fail closed
    claims = _claims(result)
    assert not (_is_clean(result) and claims), f"{label} was accepted as a clean document"
    assert not claims or _flagged(result, claims), (
        f"{label} was parsed into claims without any finding"
    )


def test_an_oversized_file_is_refused_not_truncated():
    data = b"Claim Number,Paid\n" + b"X-1,1\n" * (MAX_UPLOAD_BYTES // 6 + 1024)
    assert len(data) > MAX_UPLOAD_BYTES
    if _rejected(data, "oversized.csv"):
        return
    claims = _claims(_read(data, "oversized.csv"))
    assert not claims, "an oversized file was truncated and parsed into claims"


@pytest.mark.parametrize("filename", ["empty.csv", "empty.xlsx"])
def test_an_empty_file_is_refused(filename):
    assert _rejected(b"", filename), "an empty file was accepted"
