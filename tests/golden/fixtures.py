"""Synthetic loss runs (spec section 10).

Real customer documents are never committed.  Each fixture is defined once as
*canonical* values — Decimals and dates — and rendered into a PDF through a
carrier's formatting convention (US or EU separators, parentheses or trailing
minus, spelled or numeric dates).

The expected CSV holds the canonical values.  The round trip therefore tests
exactly what the product does: read a carrier's formatting and give back
canonical numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

D = Decimal


@dataclass(frozen=True)
class Column:
    """One printed column and the canonical field it carries."""

    label: str
    field: str | None
    align: str = "left"
    width: float = 58.0


@dataclass(frozen=True)
class Fixture:
    name: str
    description: str
    carrier: str
    named_insured: str
    policy_number: str
    policy_period: tuple[date, date]
    valuation_date: date
    line_of_business: str
    columns: tuple[Column, ...]
    claims: tuple[dict[str, Any], ...]
    number_format: str = "us"
    date_format: str = "mdy"
    #: Printed after the carrier on the letterhead: "STATE AUTO - LOSS REPORT".
    report_title: str | None = None
    #: Currency symbol printed on every amount, and which side it sits.
    currency_symbol: str | None = None
    currency_position: str = "prefix"
    #: The carrier prints recoveries as credits ("250.00-") even though the
    #: amount recovered is positive. R-01 has to work this out for itself.
    recovery_as_credit: bool = False
    #: How the footer states the row count.
    claim_count_label: str = "Total Claims:"
    claim_count_in_totals_row: bool = False
    currency: str = "USD"
    locale_hint: str = "us"
    style: str = "positioned"
    font: str = "helv"
    font_size: float = 7.5
    rows_per_page: int = 40
    scanned: bool = False
    needs_mapping: bool = False
    #: Claim numbers whose printed arithmetic is deliberately wrong.
    deliberate_r01_errors: tuple[str, ...] = ()
    #: True for a fixture whose entire purpose is to be hard: genuinely
    #: unresolvable ambiguity, real currency conflicts, or a field that
    #: cannot be read at all. These are excluded from the blanket "every
    #: digital fixture must hit the accuracy threshold and tie" checks --
    #: not because the pipeline is allowed to get them wrong, but because
    #: "wrong" is not well-defined for a document built to be indeterminate.
    #: Each one gets its own exact-findings assertions instead.
    is_stress: bool = False
    print_totals: bool = True
    print_claim_count: bool = True
    total_label: str = "TOTALS"
    landscape: bool = True

    @property
    def money_fields(self) -> tuple[str, ...]:
        from core.schema import MONEY_FIELDS

        return tuple(
            column.field
            for column in self.columns
            if column.field in MONEY_FIELDS
        )

    def printed_totals(self) -> dict[str, Decimal]:
        """Footer totals, summed from the canonical values."""
        totals: dict[str, Decimal] = {}
        for field_name in self.money_fields:
            values = [
                claim[field_name]
                for claim in self.claims
                if claim.get(field_name) is not None
            ]
            if values:
                totals[field_name] = sum(values, D("0"))
        return totals


def _totalled(
    claim_number: str,
    dol: date,
    reported: date,
    status: str,
    *,
    indemnity: Decimal | None = None,
    medical: Decimal | None = None,
    expense: Decimal | None = None,
    res_indemnity: Decimal | None = None,
    res_medical: Decimal | None = None,
    res_expense: Decimal | None = None,
    recovery: Decimal | None = None,
    claimant: str | None = None,
    description: str | None = None,
    cause: str | None = None,
    litigation: bool | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """Build a row whose totals agree with its components by construction."""
    paid_parts = [v for v in (indemnity, medical, expense) if v is not None]
    reserve_parts = [
        v for v in (res_indemnity, res_medical, res_expense) if v is not None
    ]
    paid_total = sum(paid_parts, D("0")) if paid_parts else None
    reserve_total = sum(reserve_parts, D("0")) if reserve_parts else None
    incurred = (
        (paid_total or D("0")) + (reserve_total or D("0")) - (recovery or D("0"))
        if paid_parts or reserve_parts
        else None
    )
    row: dict[str, Any] = {
        "claim_number": claim_number,
        "date_of_loss": dol,
        "date_reported": reported,
        "claim_status": status,
        "claimant_name": claimant,
        "loss_description": description,
        "cause_of_loss": cause,
        "paid_indemnity": indemnity,
        "paid_medical": medical,
        "paid_expense": expense,
        "paid_total": paid_total,
        "reserve_indemnity": res_indemnity,
        "reserve_medical": res_medical,
        "reserve_expense": res_expense,
        "reserve_total": reserve_total,
        "recovery_total": recovery,
        "incurred_total": incurred,
        "litigation_flag": litigation,
    }
    row.update(overrides)
    return row


# --------------------------------------------------------------------------
# Column layouts
# --------------------------------------------------------------------------

_GL_COLUMNS = (
    Column("Claim Number", "claim_number", width=76),
    Column("Date of Loss", "date_of_loss", width=62),
    Column("Date Reported", "date_reported", width=66),
    Column("Status", "claim_status", width=44),
    Column("Claimant", "claimant_name", width=96),
    Column("Description of Loss", "loss_description", width=132),
    Column("Paid", "paid_total", align="right", width=66),
    Column("Reserve", "reserve_total", align="right", width=66),
    Column("Recovery", "recovery_total", align="right", width=62),
    Column("Total Incurred", "incurred_total", align="right", width=72),
)

_WC_COLUMNS = (
    Column("Claim No", "claim_number", width=68),
    Column("DOL", "date_of_loss", width=56),
    Column("Rptd", "date_reported", width=56),
    Column("Stat", "claim_status", width=32),
    Column("Claimant", "claimant_name", width=78),
    Column("Paid Indem", "paid_indemnity", align="right", width=58),
    Column("Paid Med", "paid_medical", align="right", width=58),
    Column("Paid Exp", "paid_expense", align="right", width=54),
    Column("Paid Total", "paid_total", align="right", width=60),
    Column("Res Indem", "reserve_indemnity", align="right", width=58),
    Column("Res Med", "reserve_medical", align="right", width=56),
    Column("Res Exp", "reserve_expense", align="right", width=54),
    Column("Res Total", "reserve_total", align="right", width=58),
    Column("Total Incurred", "incurred_total", align="right", width=66),
)

_MAINFRAME_COLUMNS = (
    Column("CLM NBR", "claim_number", width=74),
    Column("LOSS DT", "date_of_loss", width=60),
    Column("RPT DT", "date_reported", width=60),
    Column("ST", "claim_status", width=26),
    Column("PD LOSS", "paid_indemnity", align="right", width=64),
    Column("PD EXP", "paid_expense", align="right", width=64),
    Column("PD TOT", "paid_total", align="right", width=66),
    Column("RSV LOSS", "reserve_indemnity", align="right", width=64),
    Column("RSV EXP", "reserve_expense", align="right", width=64),
    Column("RSV TOT", "reserve_total", align="right", width=66),
    Column("RECOV", "recovery_total", align="right", width=58),
    Column("INCURRED", "incurred_total", align="right", width=70),
)

_AUTO_COLUMNS = (
    Column("Claim #", "claim_number", width=72),
    Column("Loss Date", "date_of_loss", width=62),
    Column("Reported", "date_reported", width=62),
    Column("Status", "claim_status", width=48),
    Column("Cause", "cause_of_loss", width=88),
    Column("Suit", "litigation_flag", width=34),
    Column("Paid", "paid_total", align="right", width=68),
    Column("Reserves", "reserve_total", align="right", width=68),
    Column("Recovery", "recovery_total", align="right", width=66),
    Column("Incurred", "incurred_total", align="right", width=72),
)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _gl_claims() -> tuple[dict[str, Any], ...]:
    return (
        _totalled("GL-2024-0001", date(2024, 1, 17), date(2024, 1, 22), "Open",
                  indemnity=D("12500.00"), expense=D("3200.50"),
                  res_indemnity=D("40000.00"), res_expense=D("7500.00"),
                  claimant="Alvarez, Marisol", description="Slip and fall in lobby",
                  cause="Slip/Fall"),
        _totalled("GL-2024-0002", date(2024, 2, 3), date(2024, 2, 5), "Closed",
                  indemnity=D("4750.25"), expense=D("890.00"),
                  recovery=D("1250.00"),
                  claimant="Bennett, Ray", description="Product defect claim",
                  cause="Products"),
        _totalled("GL-2024-0003", date(2024, 3, 28), date(2024, 4, 2), "Open",
                  indemnity=D("0.00"), expense=D("1425.75"),
                  res_indemnity=D("25000.00"), res_expense=D("6000.00"),
                  claimant="Chen, Wei", description="Water damage to tenant space",
                  cause="Water"),
        _totalled("GL-2024-0004", date(2024, 5, 14), date(2024, 5, 14), "Closed",
                  indemnity=D("18900.00"), expense=D("5600.40"),
                  claimant="Dubois, Henri", description="Vehicle struck storefront",
                  cause="Impact"),
        _totalled("GL-2024-0005", date(2024, 7, 9), date(2024, 7, 30), "Reopened",
                  indemnity=D("2200.00"), expense=D("450.00"),
                  res_indemnity=D("15000.00"), res_expense=D("2500.00"),
                  claimant="Egan, Patricia", description="Alleged premises defect",
                  cause="Premises"),
        _totalled("GL-2024-0006", date(2024, 9, 21), date(2024, 10, 1), "Open",
                  indemnity=D("750.00"), expense=D("125.00"),
                  res_indemnity=D("9500.00"), res_expense=D("1800.00"),
                  recovery=D("500.00"),
                  claimant="Fitzgerald, Dana", description="Trip on uneven pavement",
                  cause="Slip/Fall"),
    )


US_BASIC = Fixture(
    name="us_basic",
    description="US separators, mm/dd/yyyy dates, positioned text (no ruled lines)",
    carrier="Meridian Casualty Company",
    named_insured="Harbor Point Property Group LLC",
    policy_number="GL-4471902-24",
    policy_period=(date(2024, 1, 1), date(2024, 12, 31)),
    valuation_date=date(2024, 12, 31),
    line_of_business="GL",
    columns=_GL_COLUMNS,
    claims=_gl_claims(),
)


EU_FORMAT = Fixture(
    name="eu_format",
    description="EU separators (1.234,56), dd/mm/yyyy dates, EUR",
    carrier="Rheinland Allgemeine Versicherung AG",
    named_insured="Nordwind Logistik GmbH",
    policy_number="EU-GL-88213",
    policy_period=(date(2024, 1, 1), date(2024, 12, 31)),
    valuation_date=date(2024, 12, 31),
    line_of_business="GL",
    columns=_GL_COLUMNS,
    claims=_gl_claims(),
    number_format="eu",
    date_format="dmy",
    currency="EUR",
    locale_hint="eu",
)


WC_MEDICAL = Fixture(
    name="wc_medical",
    description="Workers comp with paid/reserve medical columns",
    carrier="Statewide Mutual Insurance",
    named_insured="Cascade Millwork Inc",
    policy_number="WC-778312-24",
    policy_period=(date(2024, 4, 1), date(2025, 3, 31)),
    valuation_date=date(2025, 3, 31),
    line_of_business="WC",
    font_size=7.0,
    columns=_WC_COLUMNS,
    claims=(
        _totalled("WC24-1001", date(2024, 4, 18), date(2024, 4, 19), "OPEN",
                  indemnity=D("8400.00"), medical=D("14250.75"), expense=D("1100.00"),
                  res_indemnity=D("22000.00"), res_medical=D("31500.00"),
                  res_expense=D("4000.00"), claimant="Novak, Peter"),
        _totalled("WC24-1002", date(2024, 5, 2), date(2024, 5, 2), "CLOSED",
                  indemnity=D("1250.00"), medical=D("3480.25"), expense=D("0.00"),
                  claimant="Ortiz, Lucia"),
        _totalled("WC24-1003", date(2024, 6, 27), date(2024, 7, 3), "OPEN",
                  indemnity=D("0.00"), medical=D("925.50"), expense=D("250.00"),
                  res_indemnity=D("5000.00"), res_medical=D("12000.00"),
                  res_expense=D("1500.00"), claimant="Park, Hyun"),
        _totalled("WC24-1004", date(2024, 8, 15), date(2024, 8, 21), "CLOSED",
                  indemnity=D("16750.00"), medical=D("28900.00"), expense=D("3250.00"),
                  claimant="Quinn, Sean"),
        _totalled("WC24-1005", date(2024, 11, 6), date(2024, 11, 8), "OPEN",
                  indemnity=D("3100.00"), medical=D("7625.00"), expense=D("800.00"),
                  res_indemnity=D("18000.00"), res_medical=D("26500.00"),
                  res_expense=D("2200.00"), claimant="Rossi, Elena"),
        _totalled("WC24-1006", date(2025, 1, 23), date(2025, 1, 24), "OPEN",
                  indemnity=D("450.00"), medical=D("1875.25"), expense=D("125.00"),
                  res_indemnity=D("9000.00"), res_medical=D("15000.00"),
                  res_expense=D("1000.00"), claimant="Salazar, Diego"),
    ),
)


ACCOUNTING_NEGATIVES = Fixture(
    name="accounting_negatives",
    description="Parentheses negatives, recoveries exceeding paid on one row",
    carrier="Great Basin Indemnity",
    named_insured="Ridgeline Transport Co",
    policy_number="AUTO-556201-24",
    policy_period=(date(2024, 1, 1), date(2024, 12, 31)),
    valuation_date=date(2024, 12, 31),
    line_of_business="AUTO",
    columns=_AUTO_COLUMNS,
    number_format="paren",
    claims=(
        _totalled("AU-0001", date(2024, 2, 14), date(2024, 2, 15), "Closed",
                  indemnity=D("22400.00"), expense=D("1800.00"),
                  recovery=D("6500.00"), cause="Collision", litigation=False),
        _totalled("AU-0002", date(2024, 3, 30), date(2024, 4, 4), "Open",
                  indemnity=D("5600.00"), expense=D("950.00"),
                  res_indemnity=D("31000.00"), res_expense=D("4500.00"),
                  cause="Bodily Injury", litigation=True),
        # Subrogation recovered more than was paid: incurred goes negative.
        _totalled("AU-0003", date(2024, 6, 11), date(2024, 6, 12), "Closed",
                  indemnity=D("3200.00"), expense=D("400.00"),
                  recovery=D("9800.00"), cause="Comprehensive", litigation=False),
        _totalled("AU-0004", date(2024, 8, 25), date(2024, 9, 2), "Open",
                  indemnity=D("14750.50"), expense=D("2300.00"),
                  res_indemnity=D("18000.00"), res_expense=D("3000.00"),
                  recovery=D("2500.00"), cause="Collision", litigation=True),
        _totalled("AU-0005", date(2024, 10, 17), date(2024, 10, 18), "Closed",
                  indemnity=D("890.00"), expense=D("110.00"),
                  cause="Glass", litigation=False),
        # Paid net of a large recovery is itself negative.
        _totalled("AU-0006", date(2024, 12, 3), date(2024, 12, 5), "Closed",
                  indemnity=D("-4200.00"), expense=D("600.00"),
                  cause="Comprehensive", litigation=False),
    ),
)


MAINFRAME_TRAILING_MINUS = Fixture(
    name="mainframe_trailing_minus",
    description="Trailing-minus negatives, -0- zeros, Courier, all caps",
    carrier="ATLANTIC STATES INS CO",
    named_insured="PIEDMONT FOODS INC",
    policy_number="CPP 7719340",
    policy_period=(date(2023, 7, 1), date(2024, 6, 30)),
    valuation_date=date(2024, 6, 30),
    line_of_business="PROP",
    columns=_MAINFRAME_COLUMNS,
    number_format="trailing_minus",
    font="cour",
    font_size=7.0,
    total_label="REPORT TOTALS",
    claims=(
        _totalled("CPP0071193", date(2023, 8, 14), date(2023, 8, 16), "O",
                  indemnity=D("34200.00"), expense=D("2750.00"),
                  res_indemnity=D("15000.00"), res_expense=D("3000.00"),
                  recovery=D("0.00")),
        _totalled("CPP0071194", date(2023, 9, 29), date(2023, 10, 2), "C",
                  indemnity=D("8750.50"), expense=D("1200.00"),
                  recovery=D("2000.00")),
        _totalled("CPP0071195", date(2023, 11, 7), date(2023, 11, 7), "C",
                  indemnity=D("0.00"), expense=D("0.00"), recovery=D("0.00")),
        _totalled("CPP0071196", date(2024, 1, 19), date(2024, 1, 25), "O",
                  indemnity=D("12300.00"), expense=D("4100.00"),
                  res_indemnity=D("28000.00"), res_expense=D("5500.00"),
                  recovery=D("1500.00")),
        # Reversed reserve: a credit entry printed with a trailing minus.
        _totalled("CPP0071197", date(2024, 3, 22), date(2024, 3, 26), "C",
                  indemnity=D("6400.00"), expense=D("800.00"),
                  res_indemnity=D("-3500.00"), res_expense=D("0.00"),
                  recovery=D("0.00")),
        _totalled("CPP0071198", date(2024, 5, 30), date(2024, 6, 4), "O",
                  indemnity=D("2100.00"), expense=D("350.00"),
                  res_indemnity=D("18500.00"), res_expense=D("2200.00"),
                  recovery=D("0.00")),
    ),
)


MULTIPAGE = Fixture(
    name="multipage_repeat_header",
    description="Three pages, column headers repeated on every page",
    carrier="Northgate Fire & Marine",
    named_insured="Summit Retail Holdings LP",
    policy_number="PKG-330199-24",
    policy_period=(date(2024, 1, 1), date(2024, 12, 31)),
    valuation_date=date(2024, 12, 31),
    line_of_business="PROP",
    columns=_GL_COLUMNS,
    rows_per_page=5,
    claims=tuple(
        _totalled(
            f"NG-24-{index:04d}",
            date(2024, 1 + (index % 12), 1 + (index * 3) % 27),
            date(2024, 1 + (index % 12), 2 + (index * 3) % 26),
            ("Open", "Closed", "Reopened")[index % 3],
            indemnity=D(str(1000 + index * 337)) + D("0.25"),
            expense=D(str(200 + index * 71)) + D("0.50"),
            res_indemnity=(D(str(5000 + index * 911)) if index % 3 != 1 else None),
            res_expense=(D(str(800 + index * 133)) if index % 3 != 1 else None),
            recovery=(D(str(index * 150)) if index % 4 == 0 else None),
            claimant=f"Claimant {index:02d}",
            description=f"Loss event number {index:02d}",
            cause="Various",
        )
        for index in range(1, 15)
    ),
)


RULED_TABLE = Fixture(
    name="ruled_table",
    description="Ruled grid so pdfplumber's table detector is exercised",
    carrier="Cornerstone Specialty Insurance",
    named_insured="Bayside Marina Services",
    policy_number="SPC-119837-24",
    policy_period=(date(2024, 1, 1), date(2024, 12, 31)),
    valuation_date=date(2024, 12, 31),
    line_of_business="GL",
    columns=_GL_COLUMNS,
    style="ruled",
    claims=_gl_claims(),
)


ARITHMETIC_ERROR = Fixture(
    name="arithmetic_error",
    description="Row 3 incurred is overstated by 10,000 — R-01 must catch it",
    carrier="Fairmount Underwriters",
    named_insured="Copper Creek Brewing Co",
    policy_number="GL-901244-24",
    policy_period=(date(2024, 1, 1), date(2024, 12, 31)),
    valuation_date=date(2024, 12, 31),
    line_of_business="GL",
    columns=_GL_COLUMNS,
    print_totals=False,  # the footer would disagree with the broken row
    deliberate_r01_errors=("FM-0003",),
    claims=(
        _totalled("FM-0001", date(2024, 1, 8), date(2024, 1, 12), "Open",
                  indemnity=D("5000.00"), expense=D("1000.00"),
                  res_indemnity=D("10000.00"), res_expense=D("2000.00"),
                  claimant="Adams, Cole", description="Kitchen fire"),
        _totalled("FM-0002", date(2024, 4, 22), date(2024, 4, 25), "Closed",
                  indemnity=D("3200.00"), expense=D("450.00"),
                  claimant="Brooks, Nia", description="Customer injury"),
        # Deliberate defect: incurred is 10,000 more than paid + reserve.
        _totalled("FM-0003", date(2024, 6, 30), date(2024, 7, 2), "Open",
                  indemnity=D("7500.00"), expense=D("900.00"),
                  res_indemnity=D("20000.00"), res_expense=D("3000.00"),
                  claimant="Cortez, Ana", description="Equipment failure",
                  incurred_total=D("41400.00")),
        _totalled("FM-0004", date(2024, 9, 14), date(2024, 9, 19), "Open",
                  indemnity=D("1800.00"), expense=D("300.00"),
                  res_indemnity=D("6000.00"), res_expense=D("900.00"),
                  claimant="Dunn, Elliot", description="Slip in restroom"),
    ),
)


NULLS_NOT_ZEROS = Fixture(
    name="nulls_not_zeros",
    description="N/A and blank cells that must stay null, plus -0- true zeros",
    carrier="Keystone Regional Insurance",
    named_insured="Lakeshore Equipment Rental",
    policy_number="GL-224417-24",
    policy_period=(date(2024, 1, 1), date(2024, 12, 31)),
    valuation_date=date(2024, 12, 31),
    line_of_business="GL",
    columns=_GL_COLUMNS,
    print_totals=False,  # a column with nulls has no meaningful printed total
    claims=(
        _totalled("KR-0001", date(2024, 2, 19), date(2024, 2, 20), "Open",
                  indemnity=D("9400.00"), expense=D("1200.00"),
                  res_indemnity=D("14000.00"), res_expense=D("2000.00"),
                  claimant="Hale, Morgan", description="Forklift damage"),
        # Recovery is "N/A": no subrogation pursued, which is not zero recovery.
        _totalled("KR-0002", date(2024, 5, 8), date(2024, 5, 9), "Closed",
                  indemnity=D("2750.00"), expense=D("300.00"),
                  claimant="Iverson, Blake", description="Damaged rental unit",
                  recovery_total_display="N/A"),
        # Blank recovery cell: also null, also not zero.
        _totalled("KR-0003", date(2024, 7, 26), date(2024, 8, 1), "Open",
                  indemnity=D("600.00"), expense=D("150.00"),
                  res_indemnity=D("8000.00"), res_expense=D("1100.00"),
                  claimant="Jansen, Ruth", description="Customer trip",
                  recovery_total_display=""),
        # A genuine zero recovery, printed the mainframe way.
        _totalled("KR-0004", date(2024, 10, 13), date(2024, 10, 15), "Closed",
                  indemnity=D("4100.00"), expense=D("520.00"),
                  recovery=D("0.00"), claimant="Koval, Sam",
                  description="Equipment theft", recovery_total_display="-0-"),
    ),
)


SCANNED = Fixture(
    name="scanned",
    description="Image-only page: no text layer, must classify as scanned",
    carrier="Union Standard Insurance Group",
    named_insured="Delta Fabrication LLC",
    policy_number="GL-660812-24",
    policy_period=(date(2024, 1, 1), date(2024, 12, 31)),
    valuation_date=date(2024, 12, 31),
    line_of_business="GL",
    columns=_GL_COLUMNS,
    claims=_gl_claims()[:4],
    scanned=True,
    font_size=8.5,
)



_UNKNOWN_COLUMNS = (
    Column("Ref No", "claim_number", width=72),
    Column("Occurrence Dt", "date_of_loss", width=66),
    Column("Ntfn Dt", "date_reported", width=62),
    Column("Cond", "claim_status", width=44),
    Column("Pty", "claimant_name", width=94),
    Column("Net Pd", "paid_total", align="right", width=66),
    Column("O/S", "reserve_total", align="right", width=66),
    Column("Recovered", "recovery_total", align="right", width=66),
    Column("Gross Inc", "incurred_total", align="right", width=70),
)


UNKNOWN_FORMAT = Fixture(
    name="unknown_format",
    description="Headers the built-in vocabulary cannot place — needs mapping",
    carrier="Ardmore Speciality Lines Limited",
    named_insured="Whitfield Engineering Ltd",
    policy_number="ASL/2024/00918",
    policy_period=(date(2024, 1, 1), date(2024, 12, 31)),
    valuation_date=date(2024, 12, 31),
    line_of_business="GL",
    columns=_UNKNOWN_COLUMNS,
    claims=_gl_claims(),
    needs_mapping=True,
)



# --------------------------------------------------------------------------
# Carrier layouts that broke the pipeline in QA (bug report, fixtures 1-4)
#
# Each of these reproduces a document that produced a false finding or lost
# every row, so the defect stays fixed.
# --------------------------------------------------------------------------

_SIMPLE_RESERVE_COLUMNS = (
    Column("Claim #", "claim_number", width=76),
    Column("Loss Date", "date_of_loss", width=66),
    Column("Status", "claim_status", width=52),
    Column("Paid Total", "paid_total", align="right", width=80),
    Column("Reserve Total", "reserve_total", align="right", width=84),
    Column("Incurred Total", "incurred_total", align="right", width=86),
)

_SIMPLE_RECOVERY_COLUMNS = (
    Column("Claim Ref", "claim_number", width=76),
    Column("Loss Date", "date_of_loss", width=66),
    Column("Status", "claim_status", width=52),
    Column("Paid Total", "paid_total", align="right", width=80),
    Column("Recovery Total", "recovery_total", align="right", width=84),
    Column("Incurred Total", "incurred_total", align="right", width=86),
)


QA_TRAVELERS = Fixture(
    name="qa_travelers_clean",
    description="Currency-prefixed amounts, count stated as 'Claims: N' in the footer",
    carrier="TRAVELERS INSURANCE",
    report_title="LOSS RUN REPORT",
    named_insured="Acme Logistics LLC",
    policy_number="TR-4471902-23",
    policy_period=(date(2023, 1, 1), date(2024, 1, 1)),
    valuation_date=date(2024, 6, 30),
    line_of_business="AUTO",
    columns=_SIMPLE_RESERVE_COLUMNS,
    currency_symbol="$",
    claim_count_label="Claims:",
    claim_count_in_totals_row=True,
    claims=(
        _totalled("CA-88392", date(2023, 5, 12), date(2023, 5, 12), "CLOSED",
                  indemnity=D("13750.50")),
        _totalled("CA-90112", date(2023, 8, 20), date(2023, 8, 20), "OPEN",
                  indemnity=D("3600.00"), res_indemnity=D("5000.00")),
    ),
)


QA_MAINFRAME_CREDIT = Fixture(
    name="qa_mainframe_credit",
    description="Recoveries printed as credits (250.00-) with -0- zeros; 'Claim Ref' header",
    carrier="STATE AUTO",
    report_title="HISTORICAL LOSS REPORT",
    named_insured="Acme Logistics LLC",
    policy_number="SA-7719340",
    policy_period=(date(2023, 1, 1), date(2024, 1, 1)),
    valuation_date=date(2024, 6, 30),
    line_of_business="AUTO",
    columns=_SIMPLE_RECOVERY_COLUMNS,
    recovery_as_credit=True,
    claim_count_label="Claims:",
    claim_count_in_totals_row=True,
    claims=(
        _totalled("SA-101", date(2023, 3, 15), date(2023, 3, 15), "CLOSED",
                  indemnity=D("1250.50"), recovery=D("0.00")),
        _totalled("SA-102", date(2023, 6, 20), date(2023, 6, 20), "OPEN",
                  indemnity=D("500.00"), recovery=D("250.00")),
    ),
)


QA_EUROPEAN_CREDIT = Fixture(
    name="qa_european_credit",
    description="EUR amounts, dd.mm.yyyy dates and credit recoveries in one document",
    carrier="ALLIANZ GLOBAL",
    report_title="LOSS RUN STATEMENT",
    named_insured="Acme Logistics LLC",
    policy_number="AZ-88213",
    policy_period=(date(2023, 1, 1), date(2024, 1, 1)),
    valuation_date=date(2024, 6, 30),
    line_of_business="GL",
    columns=_SIMPLE_RECOVERY_COLUMNS,
    number_format="eu",
    # The claim dates are written day-first with dots while the header block
    # above them is written month-first with slashes, which is what the real
    # document does.
    date_format="dmy_dots",
    currency="EUR",
    locale_hint="eu",
    currency_symbol="€",
    currency_position="suffix",
    recovery_as_credit=True,
    claim_count_label="Claims:",
    claim_count_in_totals_row=True,
    claims=(
        _totalled("EU-901", date(2023, 3, 12), date(2023, 3, 12), "CLOSED",
                  indemnity=D("5700.50"), recovery=D("200.00")),
    ),
)


QA_DIRTY = Fixture(
    name="qa_dirty_errors",
    description="Three real defects at once: broken arithmetic, closed-with-reserve, loss before inception",
    carrier="TEST CARRIER",
    report_title="INTENTIONAL ERROR REPORT",
    named_insured="Acme Logistics LLC",
    policy_number="TC-901244",
    policy_period=(date(2023, 1, 1), date(2024, 1, 1)),
    valuation_date=date(2024, 6, 30),
    line_of_business="GL",
    columns=_SIMPLE_RESERVE_COLUMNS,
    currency_symbol="$",
    claim_count_label="Claims:",
    claim_count_in_totals_row=True,
    deliberate_r01_errors=("ERR-001",),
    claims=(
        _totalled("ERR-001", date(2021, 11, 15), date(2021, 11, 20), "CLOSED",
                  indemnity=D("50000.00"), res_indemnity=D("10000.00"),
                  incurred_total=D("55000.00")),
    ),
)



# --------------------------------------------------------------------------
# Stress fixtures — harder combinations for deliberate torture-testing
# --------------------------------------------------------------------------

_STRESS_WC_COLUMNS = (
    Column("Claim No", "claim_number", width=68),
    Column("DOL", "date_of_loss", width=56),
    Column("Rptd", "date_reported", width=56),
    Column("Stat", "claim_status", width=36),
    Column("Claimant", "claimant_name", width=82),
    Column("Suit", "litigation_flag", width=32),
    Column("Paid Indem", "paid_indemnity", align="right", width=58),
    Column("Paid Med", "paid_medical", align="right", width=58),
    Column("Paid Total", "paid_total", align="right", width=62),
    Column("Res Indem", "reserve_indemnity", align="right", width=58),
    Column("Res Med", "reserve_medical", align="right", width=56),
    Column("Res Total", "reserve_total", align="right", width=60),
    Column("Recovery", "recovery_total", align="right", width=58),
    Column("Incurred", "incurred_total", align="right", width=68),
)


def _stress_wc_claims() -> tuple[dict[str, Any], ...]:
    rows = []
    for index in range(1, 41):
        status = ("OPEN", "CLOSED", "REOPENED")[index % 3]
        rows.append(
            _totalled(
                f"WC-STR-{index:04d}",
                date(2024, 1 + (index % 12), 1 + (index * 7) % 27),
                date(2024, 1 + (index % 12), 2 + (index * 7) % 26),
                status,
                indemnity=D(str(400 + index * 83)) + D("0.25"),
                medical=D(str(150 + index * 41)) + D("0.75"),
                res_indemnity=(D(str(2000 + index * 213)) if status != "CLOSED" else D("0.00")),
                res_medical=(D(str(900 + index * 97)) if status != "CLOSED" else D("0.00")),
                recovery=(D(str(index * 40)) if index % 5 == 0 else None),
                claimant=f"Claimant {index:03d}",
                litigation=(True if index % 9 == 0 else (False if index % 4 == 0 else None)),
            )
        )
    # Planted findings, appended after the generated body so the loop above
    # stays a clean baseline. Each one exercises exactly one warn-level rule.
    # R-11: an exact duplicate of a row already on the same rendered page
    # (rows_per_page=9 puts rows 37-44 on page 5 together with these four
    # appended rows). Same claim number *and* same values, the way a genuine
    # copy-paste duplicate looks on a real report -- a duplicate with
    # different amounts under the same number would be its own defect
    # (silently overwriting one claim's data), which is a different bug than
    # the one R-11 exists to catch.
    rows.append(dict(rows[37]))  # a second copy of WC-STR-0038
    rows.append(  # R-13: two orders of magnitude above the column median
        _totalled("WC-STR-9001", date(2024, 6, 1), date(2024, 6, 2), "OPEN",
                  indemnity=D("410000.00"), medical=D("0.00"),
                  res_indemnity=D("50000.00"), res_medical=D("0.00"))
    )
    rows.append(  # R-08: closed but still carrying a reserve balance
        _totalled("WC-STR-9002", date(2024, 7, 10), date(2024, 7, 11), "CLOSED",
                  indemnity=D("1200.00"), medical=D("400.00"),
                  res_indemnity=D("5000.00"), res_medical=D("1500.00"))
    )
    rows.append(  # R-14: net paid negative after a recovery, legitimate
        _totalled("WC-STR-9003", date(2024, 8, 20), date(2024, 8, 21), "CLOSED",
                  indemnity=D("-1800.00"), medical=D("0.00"),
                  recovery=None, res_indemnity=D("0.00"), res_medical=D("0.00"))
    )
    return tuple(rows)


STRESS_MEGA_WC = Fixture(
    name="stress_mega_wc",
    description=(
        "44 claims across 5 pages: same-page duplicate (R-11), a 2-order-of-"
        "magnitude outlier (R-13), closed-with-reserve (R-08), a legitimate "
        "negative paid after recovery (R-14), and several report-before-loss "
        "orderings from the date arithmetic (R-10) -- all WARN/INFO, so the "
        "badge should still read Reconciled despite a busy Exceptions panel"
    ),
    carrier="Continental Benefit Mutual",
    named_insured="Ferrous Metal Works Inc",
    policy_number="WC-990214-24",
    policy_period=(date(2024, 1, 1), date(2025, 1, 1)),
    valuation_date=date(2024, 12, 31),
    line_of_business="WC",
    columns=_STRESS_WC_COLUMNS,
    rows_per_page=9,
    font_size=6.5,
    claims=_stress_wc_claims(),
)


_MIXED_CCY_COLUMNS = (
    Column("Claim Number", "claim_number", width=78),
    Column("Date of Loss", "date_of_loss", width=64),
    Column("Status", "claim_status", width=48),
    Column("Currency", "currency_label", width=48),
    Column("Paid", "paid_total", align="right", width=76),
    Column("Recovery", "recovery_total", align="right", width=72),
    Column("Incurred", "incurred_total", align="right", width=80),
)


def _mixed_currency_claims() -> tuple[dict[str, Any], ...]:
    def usd_row(number, dol, status, paid, recovery=None, incurred=None):
        return _totalled(
            number, dol, dol, status,
            indemnity=paid, expense=None, recovery=recovery,
            currency_label="USD",
            paid_total_display=f"${paid:,.2f}",
            recovery_total_display=(f"${recovery:,.2f}" if recovery else ""),
            incurred_total_display=f"${incurred:,.2f}",
            incurred_total=incurred,
        )

    def eur_row(number, dol, status, paid, recovery=None, incurred=None):
        def eu(value):
            return f"{value:,.2f}".translate(str.maketrans({",": ".", ".": ","}))

        return _totalled(
            number, dol, dol, status,
            indemnity=paid, expense=None, recovery=recovery,
            currency_label="EUR",
            paid_total_display=f"{eu(paid)} €",
            recovery_total_display=(f"{eu(recovery)} €" if recovery else ""),
            incurred_total_display=f"{eu(incurred)} €",
            incurred_total=incurred,
        )

    return (
        usd_row("US-001", date(2024, 2, 10), "CLOSED", D("4200.00"), None, D("4200.00")),
        eur_row("EU-101", date(2024, 3, 5), "OPEN", D("3100.50"), None, D("3100.50")),
        usd_row("US-002", date(2024, 4, 18), "CLOSED", D("900.00"), D("2500.00"), D("-1600.00")),
        eur_row("EU-102", date(2024, 5, 22), "CLOSED", D("7600.00"), D("500.00"), D("7100.00")),
        usd_row("US-003", date(2024, 7, 9), "OPEN", D("15400.75"), None, D("15400.75")),
    )


STRESS_MIXED_CURRENCY = Fixture(
    is_stress=True,
    name="stress_true_mixed_currency",
    description=(
        "Genuinely two currencies on real rows (not a default-vs-symbol false "
        "positive) — R-16 should actually fire — plus a recovery that "
        "legitimately exceeds paid on one USD row and one EUR row"
    ),
    carrier="Meridian International Underwriters",
    named_insured="Halcyon Freight Group",
    policy_number="INTL-550218-24",
    policy_period=(date(2024, 1, 1), date(2024, 12, 31)),
    valuation_date=date(2024, 12, 31),
    line_of_business="GL",
    columns=_MIXED_CCY_COLUMNS,
    currency_symbol="€",  # forces the TrueType embed; rows still print their
                          # own literal text via the *_display overrides
    print_totals=False,  # a footer sum across two currencies means nothing
    claims=_mixed_currency_claims(),
)


_AMBIGUOUS_COLUMNS = (
    Column("Claim Number", "claim_number", width=80),
    Column("Loss Date", "date_of_loss", width=64),
    Column("Reported", "date_reported", width=64),
    Column("Paid", "paid_total", align="right", width=70),
    Column("Reserve", "reserve_total", align="right", width=70),
    Column("Incurred", "incurred_total", align="right", width=76),
)


def _ambiguous_row(number: str, dol_text: str, reported_text: str,
                   paid_text: str, reserve_text: str, incurred_text: str) -> dict:
    """A row with no canonical value: every field's shape is undecidable, and
    a correct parser must return null with a reason for every one of them —
    never a guessed number."""
    return {
        "claim_number": number,
        "date_of_loss": None, "date_of_loss_display": dol_text,
        "date_reported": None, "date_reported_display": reported_text,
        "claim_status": None,
        "claimant_name": None, "loss_description": None, "cause_of_loss": None,
        "paid_indemnity": None, "paid_medical": None, "paid_expense": None,
        "paid_total": None, "paid_total_display": paid_text,
        "reserve_indemnity": None, "reserve_medical": None, "reserve_expense": None,
        "reserve_total": None, "reserve_total_display": reserve_text,
        "recovery_total": None,
        "incurred_total": None, "incurred_total_display": incurred_text,
        "litigation_flag": None,
    }


STRESS_TRUE_AMBIGUITY = Fixture(
    is_stress=True,
    name="stress_true_ambiguity",
    description=(
        "Every amount is a lone-comma whole-dollar figure ('12,345') and "
        "every date has both components <=12 ('03/04/2024') — genuinely "
        "undecidable with zero disambiguating evidence anywhere in the "
        "document. A correct system returns null with a reason for nearly "
        "every field rather than defaulting to US/mdy and guessing wrong"
    ),
    carrier="Undecided Mutual Assurance",
    named_insured="Ambiguity Testing Corp",
    policy_number="AMB-000001-24",
    policy_period=(date(2024, 1, 1), date(2024, 12, 31)),
    # Even the valuation date is written in the ambiguous shape, so it too
    # must come back null — R-06 should fire for real, not as a false alarm.
    valuation_date=date(2024, 6, 5),
    line_of_business="GL",
    columns=_AMBIGUOUS_COLUMNS,
    print_totals=False,
    print_claim_count=False,
    claims=(
        _ambiguous_row("AMB-001", "03/04/2024", "04/05/2024",
                       "12,345", "8,901", "21,246"),
        _ambiguous_row("AMB-002", "06/07/2024", "07/08/2024",
                       "3,450", "1,200", "4,650"),
        _ambiguous_row("AMB-003", "09/10/2024", "10/11/2024",
                       "22,100", "5,600", "27,700"),
    ),
)


_DIRTY_COLUMNS = (
    Column("Claim #", "claim_number", width=76),
    Column("Loss Date", "date_of_loss", width=66),
    Column("Status", "claim_status", width=52),
    Column("Paid Total", "paid_total", align="right", width=78),
    Column("Recovery", "recovery_total", align="right", width=70),
    Column("Incurred Total", "incurred_total", align="right", width=84),
)


DIRTY_AVALANCHE = Fixture(
    is_stress=True,
    name="stress_dirty_avalanche",
    description=(
        "A realistic messy export: N/A recovery, an invalid calendar date, a "
        "blank claim number (row dropped), a same-page duplicate, and one "
        "amount rendered as unreadable text so the printed footer total and "
        "claim count both genuinely fail to tie (real R-04 and R-05, not a "
        "parser bug)"
    ),
    carrier="Pinebrook Casualty Exchange",
    named_insured="Rustbelt Manufacturing Co",
    policy_number="PC-661820-24",
    policy_period=(date(2024, 1, 1), date(2024, 12, 31)),
    valuation_date=date(2024, 12, 31),
    line_of_business="GL",
    claim_count_label="Total Claims:",
    deliberate_r01_errors=("DRT-006",),
    columns=_DIRTY_COLUMNS,
    claims=(
        _totalled("DRT-001", date(2024, 2, 1), date(2024, 2, 3), "Open",
                  indemnity=D("2200.00"), expense=D("300.00")),
        # N/A recovery: no subrogation pursued, not a zero.
        _totalled("DRT-002", date(2024, 3, 12), date(2024, 3, 14), "Closed",
                  indemnity=D("980.00"), expense=D("120.00"),
                  recovery_total_display="N/A"),
        # Invalid calendar date -- date_of_loss required by R-07. The
        # printed text is invalid on its face, so a correct parser returns
        # null; the canonical value must say the same or the harness would
        # wrongly score a correct null as a miss.
        _totalled("DRT-003", date(2024, 4, 1), date(2024, 4, 2), "Open",
                  indemnity=D("1500.00"), expense=D("200.00"),
                  date_of_loss=None, date_of_loss_display="02/30/2024"),
        # Blank claim number: dropped during extraction, not counted.
        _totalled("", date(2024, 5, 1), date(2024, 5, 2), "Open",
                  indemnity=D("700.00"), expense=D("50.00")),
        # Same-page duplicate of DRT-001.
        _totalled("DRT-001", date(2024, 2, 1), date(2024, 2, 3), "Open",
                  indemnity=D("2200.00"), expense=D("300.00")),
        # Real arithmetic defect: R-01 must still catch this among the noise.
        _totalled("DRT-006", date(2024, 8, 1), date(2024, 8, 3), "Open",
                  indemnity=D("4000.00"), expense=D("600.00"),
                  incurred_total=D("9000.00")),
        # A real amount the extractor cannot read at all -- footer total and
        # claim-count totals are computed from the canonical value below, so
        # this genuinely undercounts both R-04 and R-05.
        _totalled("DRT-007", date(2024, 9, 5), date(2024, 9, 6), "Open",
                  indemnity=D("450.00"), expense=D("0.00"),
                  paid_total_display="TBD - pending adjuster"),
    ),
)


_OBSCURE_EU_COLUMNS = (
    Column("Ref#", "claim_number", width=64),
    Column("Occ Date", "date_of_loss", width=64),
    Column("Adv Date", "date_reported", width=64),
    Column("Claim Stat", "claim_status", width=52),
    Column("Party", "claimant_name", width=90),
    Column("Amt Paid", "paid_total", align="right", width=68),
    Column("Res Amt", "reserve_total", align="right", width=68),
    Column("Recov (Cr)", "recovery_total", align="right", width=70),
    Column("Net Loss", "incurred_total", align="right", width=72),
)


STRESS_OBSCURE_EU_CREDIT = Fixture(
    name="stress_obscure_headers_eu_credit",
    description=(
        "5 of 9 column labels the vocabulary cannot place (claim number, both "
        "dates, claimant, net loss), EUR amounts, dd/mm/yyyy dates, "
        "recoveries printed as credits, and three pages -- the mapping "
        "screen, EU parsing, the credit-sign inference and repeated headers "
        "all have to work together at once"
    ),
    carrier="Nordic Assurance Partners",
    named_insured="Baltic Cold Chain Logistics",
    policy_number="NAP/2024/03318",
    policy_period=(date(2024, 1, 1), date(2024, 12, 31)),
    valuation_date=date(2024, 12, 31),
    line_of_business="GL",
    columns=_OBSCURE_EU_COLUMNS,
    number_format="eu",
    date_format="dmy",
    currency="EUR",
    locale_hint="eu",
    currency_symbol="€",
    currency_position="suffix",
    recovery_as_credit=True,
    needs_mapping=True,
    rows_per_page=4,
    claims=tuple(
        _totalled(
            f"NAP-{index:03d}",
            date(2024, 1 + (index % 12), 1 + (index * 5) % 27),
            date(2024, 1 + (index % 12), 2 + (index * 5) % 26),
            ("Open", "Closed", "Reopened")[index % 3],
            indemnity=D(str(600 + index * 211)) + D("0.50"),
            expense=D(str(90 + index * 33)) + D("0.25"),
            res_indemnity=(D(str(3000 + index * 405)) if index % 3 != 1 else None),
            recovery=(D(str(index * 60)) if index % 3 == 0 else None),
            claimant=f"Party {index:02d}",
        )
        for index in range(1, 10)
    ),
)


ALL_FIXTURES: tuple[Fixture, ...] = (
    US_BASIC,
    EU_FORMAT,
    WC_MEDICAL,
    ACCOUNTING_NEGATIVES,
    MAINFRAME_TRAILING_MINUS,
    MULTIPAGE,
    RULED_TABLE,
    ARITHMETIC_ERROR,
    NULLS_NOT_ZEROS,
    QA_TRAVELERS,
    QA_MAINFRAME_CREDIT,
    QA_EUROPEAN_CREDIT,
    QA_DIRTY,
    STRESS_MEGA_WC,
    STRESS_MIXED_CURRENCY,
    STRESS_TRUE_AMBIGUITY,
    DIRTY_AVALANCHE,
    STRESS_OBSCURE_EU_CREDIT,
    UNKNOWN_FORMAT,
    SCANNED,
)

BY_NAME: dict[str, Fixture] = {fixture.name: fixture for fixture in ALL_FIXTURES}

#: Fixtures with a real text layer whose columns the vocabulary can place —
#: the digital accuracy target applies to these. Deliberately adversarial
#: fixtures (is_stress) are excluded: their whole point is a field, a rule
#: tie, or a convention that cannot resolve, so "must hit 99.5%" is not a
#: standard that applies to them.
DIGITAL_FIXTURES: tuple[Fixture, ...] = tuple(
    fixture
    for fixture in ALL_FIXTURES
    if not fixture.scanned and not fixture.needs_mapping and not fixture.is_stress
)

#: The adversarial fixtures, for their own dedicated exact-findings tests.
STRESS_FIXTURES: tuple[Fixture, ...] = tuple(
    fixture for fixture in ALL_FIXTURES if fixture.is_stress
)


def printed_r01_violations(fixture: Fixture) -> list[str]:
    """Claims whose incurred cannot be derived from the columns actually printed.

    A value that exists in the canonical data but has no column on the page is
    invisible to a reader and to the reconciler, so leaving one in a fixture
    that is meant to be clean plants an error nobody intended.
    """
    printed = {column.field for column in fixture.columns}
    violations = []
    for claim in fixture.claims:
        incurred = claim.get("incurred_total")
        if incurred is None or "incurred_total" not in printed:
            continue
        expected = Decimal("0")
        for name, sign in (
            ("paid_total", 1), ("reserve_total", 1), ("recovery_total", -1)
        ):
            if name in printed and claim.get(name) is not None:
                expected += sign * claim[name]
        if expected != incurred:
            violations.append(claim["claim_number"])
    return violations
