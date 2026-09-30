"""E5: twelve digital loss runs, each with truth authored from its construction.

``build_cases(root)`` writes each case's PDF under ``root`` and returns the
cases (``tools.qualification.cases``). Every claim, field and printed total
in a case's truth is read from the :class:`printed.Sheet` that wrote the page,
never from LossLift's reading of it.

| Case | What it varies | Expectation |
|---|---|---|
| letter-portrait | the plain baseline | supported |
| a4-portrait | A4 instead of Letter | supported |
| landscape-components | landscape page, paid indemnity / expense columns | supported |
| labelled-every-row | "Claim No: X" in every identifier cell | supported |
| labelled-some-rows | labels on two rows only | supported |
| labelled-unreadable | a labelled row whose number cannot be read | review |
| furniture-controls | report, section and count lines inside the table | supported |
| absent-carrier | no letterhead: the page starts at its table | supported |
| wrapped-headers | every column label over two lines | supported |
| multiline-descriptions | descriptions continuing onto a second line | supported |
| signed-and-recovery | negative amounts, trailing minus, a recovery column | supported |
| blank-versus-zero | blank cells beside printed zeros | supported |
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from tests.qualification.packs.digital_formats.printed import (
    A4,
    LETTER,
    LETTER_LANDSCAPE,
    Column,
    Row,
    Sheet,
    amount,
    document_spec,
    total_row,
    write,
)
from tools.qualification.cases import Expectation, QualificationCase, document_truth

CARRIER = "HOLLOWAY MUTUAL INSURANCE COMPANY"
LETTERHEAD = (
    CARRIER,
    "LOSS RUN REPORT",
    "Named Insured: Ferncliff Test Industries Inc",
    "Policy Number: GL-55120",
    "Policy Period: 01/01/2024 to 12/31/2024",
    "Valuation Date: 12/31/2024",
)

STANDARD = (
    Column("Claim Number", "claim_number", 0),
    Column("Loss Date", "date_of_loss", 95),
    Column("Status", "claim_status", 170),
    Column("Paid Total", "paid_total", 240),
    Column("Reserve Total", "reserve_total", 325),
    Column("Incurred Total", "incurred_total", 410),
)

#: (number suffix, loss date, status, paid, reserve)
_BASE = (
    ("001", "01/14/2024", "CLOSED", "4,250.00", "0.00"),
    ("002", "02/03/2024", "OPEN", "1,100.00", "6,900.00"),
    ("003", "03/22/2024", "CLOSED", "780.50", "0.00"),
    ("004", "05/09/2024", "OPEN", "12,400.00", "18,600.00"),
    ("005", "07/30/2024", "CLOSED", "2,015.25", "0.00"),
    ("006", "09/17/2024", "OPEN", "0.00", "3,500.00"),
)


def _incurred(paid: str, reserve: str, recovery: str = "") -> str:
    def value(text: str) -> Decimal:
        text = text.strip()
        if not text:
            return Decimal("0")
        negative = text.startswith("(") or text.endswith("-")
        number = Decimal(text.strip("()-").replace(",", ""))
        return -number if negative else number
    return amount(value(paid) + value(reserve) - value(recovery))


def _standard_rows(prefix: str, *, label: str = "", labelled: set[int] | None = None
                   ) -> tuple[Row, ...]:
    rows = []
    for index, (suffix, loss, status, paid, reserve) in enumerate(_BASE):
        number = f"{prefix}-24{suffix}"
        printed = number if not labelled or index not in labelled else f"{label}{number}"
        rows.append(Row((printed, loss, status, paid, reserve, _incurred(paid, reserve)),
                        number))
    return tuple(rows)


def _sheet(rows, *, columns=STANDARD, size=LETTER, letterhead=LETTERHEAD, **extra) -> Sheet:
    return Sheet(size, letterhead, columns, rows, total_row(columns, rows), **extra)


def _case(root: Path, case_id: str, sheet: Sheet, *, expectation=Expectation.SUPPORTED,
          status="CLEAN", printed_count=None) -> QualificationCase:
    pdf = write(sheet, root / f"{case_id}.pdf")
    spec = document_spec(sheet, status=status, printed_count=printed_count)
    return QualificationCase(case_id, pdf, document_truth(case_id, pdf, spec), expectation)


# --------------------------------------------------------------------------
# The cases
# --------------------------------------------------------------------------


def _landscape() -> Sheet:
    columns = (
        Column("Claim Number", "claim_number", 0),
        Column("Loss Date", "date_of_loss", 85),
        Column("Status", "claim_status", 155),
        Column("Paid Indemnity", "paid_indemnity", 215),
        Column("Paid Expense", "paid_expense", 300),
        Column("Paid Total", "paid_total", 380),
        Column("Reserve Total", "reserve_total", 455),
        Column("Incurred Total", "incurred_total", 540),
    )
    rows = []
    for suffix, loss, status, indemnity, expense, reserve in (
        ("101", "02/11/2024", "CLOSED", "3,000.00", "450.00", "0.00"),
        ("102", "04/02/2024", "OPEN", "8,200.00", "1,300.00", "11,000.00"),
        ("103", "06/19/2024", "CLOSED", "615.00", "85.00", "0.00"),
        ("104", "08/27/2024", "OPEN", "0.00", "250.00", "4,750.00"),
    ):
        paid = amount(Decimal(indemnity.replace(",", "")) + Decimal(expense.replace(",", "")))
        rows.append(Row((f"HL-24{suffix}", loss, status, indemnity, expense, paid, reserve,
                         _incurred(paid, reserve)), f"HL-24{suffix}"))
    return _sheet(tuple(rows), columns=columns, size=LETTER_LANDSCAPE)


def _labelled_unreadable() -> Sheet:
    rows = list(_standard_rows("HU"))
    rows.insert(3, Row(("Claim No: pending", "04/18/2024", "OPEN", "310.00", "1,000.00",
                        "1,310.00"), "?"))
    return _sheet(tuple(rows))


def _furniture() -> Sheet:
    rows = _standard_rows("HF")
    blank = ("", "", "", "", "", "")
    report = Row(("Report: Loss Run Summary",) + blank[1:], None)
    section = Row(("Policy Period: 01/01/2024",) + ("to 12/31/2024",) + blank[2:], None)
    table = (report, section, *rows)
    return Sheet(LETTER, LETTERHEAD, STANDARD, table, total_row(STANDARD, table),
                 after=(f"Claim Count: {len(rows)}",))


def _absent_carrier() -> Sheet:
    rows = _standard_rows("HA")
    return _sheet(rows, letterhead=("Valuation Date: 12/31/2024",))


def _wrapped_headers() -> Sheet:
    columns = tuple(Column(label, c.field, c.x) for label, c in zip(
        ("Claim\nNumber", "Loss\nDate", "Claim\nStatus", "Paid\nTotal", "Reserve\nTotal",
         "Incurred\nTotal"), STANDARD))
    return _sheet(_standard_rows("HW"), columns=columns)


def _multiline() -> Sheet:
    columns = (
        Column("Claim Number", "claim_number", 0),
        Column("Loss Date", "date_of_loss", 80),
        Column("Status", "claim_status", 145),
        Column("Description", None, 200),
        Column("Paid Total", "paid_total", 330),
        Column("Reserve Total", "reserve_total", 405),
        Column("Incurred Total", "incurred_total", 485),
    )
    descriptions = (("Slip on wet floor", "near loading dock"), ("Forklift contact", None),
                    ("Hand laceration", "during press setup"), ("Back strain", None))
    rows = []
    for (suffix, loss, status, paid, reserve), (first, second) in zip(_BASE, descriptions):
        number = f"HD-24{suffix}"
        rows.append(Row((number, loss, status, first, paid, reserve, _incurred(paid, reserve)),
                        number, ((3, second),) if second else ()))
    return _sheet(tuple(rows), columns=columns)


def _signed_and_recovery() -> Sheet:
    columns = (
        Column("Claim Number", "claim_number", 0),
        Column("Loss Date", "date_of_loss", 80),
        Column("Status", "claim_status", 150),
        Column("Paid Total", "paid_total", 215),
        Column("Reserve Total", "reserve_total", 295),
        Column("Recovery", "recovery_total", 380),
        Column("Incurred Total", "incurred_total", 460),
    )
    printed = (
        ("HS-24201", "01/20/2024", "CLOSED", "5,000.00", "0.00", "138.26"),
        ("HS-24202", "03/05/2024", "CLOSED", "(250.00)", "0.00", "0.00"),
        ("HS-24203", "05/14/2024", "OPEN", "2,400.00", "7,600.00", "0.00"),
        ("HS-24204", "08/08/2024", "CLOSED", "75.00-", "0.00", "0.00"),
        ("HS-24205", "10/01/2024", "CLOSED", "9,300.00", "0.00", "1,300.00"),
    )
    rows = tuple(Row((n, d, s, p, r, rec, _incurred(p, r, rec)), n)
                 for n, d, s, p, r, rec in printed)
    return _sheet(rows, columns=columns)


def _blank_versus_zero() -> Sheet:
    columns = (
        Column("Claim Number", "claim_number", 0),
        Column("Loss Date", "date_of_loss", 80),
        Column("Status", "claim_status", 150),
        Column("Paid Total", "paid_total", 215),
        Column("Reserve Total", "reserve_total", 295),
        Column("Recovery", "recovery_total", 380),
        Column("Incurred Total", "incurred_total", 460),
    )
    printed = (
        ("HB-24301", "01/09/2024", "CLOSED", "1,850.00", "", ""),
        ("HB-24302", "02/27/2024", "CLOSED", "620.00", "0.00", "0.00"),
        ("HB-24303", "04/16/2024", "OPEN", "3,100.00", "4,900.00", ""),
        ("HB-24304", "06/30/2024", "CLOSED", "940.00", "", "0.00"),
        ("HB-24305", "09/12/2024", "OPEN", "0.00", "2,250.00", ""),
    )
    rows = tuple(Row((n, d, s, p, r, rec, _incurred(p, r, rec)), n)
                 for n, d, s, p, r, rec in printed)
    return _sheet(rows, columns=columns)


def build_cases(root: Path) -> list[QualificationCase]:
    root = Path(root)
    return [
        _case(root, "letter-portrait", _sheet(_standard_rows("HP"))),
        _case(root, "a4-portrait", _sheet(_standard_rows("H4"), size=A4)),
        _case(root, "landscape-components", _landscape()),
        _case(root, "labelled-every-row",
              _sheet(_standard_rows("HE", label="Claim No: ", labelled=set(range(6))))),
        _case(root, "labelled-some-rows",
              _sheet(_standard_rows("HM", label="Claim No: ", labelled={1, 4}))),
        _case(root, "labelled-unreadable", _labelled_unreadable(),
              expectation=Expectation.REVIEW, status="NEEDS_REVIEW"),
        _case(root, "furniture-controls", _furniture(), printed_count=6),
        _case(root, "absent-carrier", _absent_carrier()),
        _case(root, "wrapped-headers", _wrapped_headers()),
        _case(root, "multiline-descriptions", _multiline()),
        _case(root, "signed-and-recovery", _signed_and_recovery()),
        _case(root, "blank-versus-zero", _blank_versus_zero()),
    ]
