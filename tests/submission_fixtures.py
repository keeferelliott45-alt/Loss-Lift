"""Synthetic saved emails and loss-run PDFs for the submission tests.

Everything here is generated in code: invented carriers, insureds, claim
numbers and senders. No real message or document is used.
"""

from __future__ import annotations

import email
import email.policy
from email.message import EmailMessage
from pathlib import Path
from typing import Sequence

import pymupdf

from core.pipeline import run_pipeline
from tests.test_packet_adversarial import LETTER, _sheet, _total
from tests.test_packet_claim_series import LARGE_CARRIER, _large_run

INSURED = "Harbor Test Fabrication LLC"
OTHER_INSURED = "Quarry Road Test Bakery Inc"
SENDER = "Broker Tester <broker.tester@example.test>"
SUBJECT = "Loss runs for Harbor Test Fabrication renewal"
BODY_SECRET = "BODY-SENTINEL-do-not-keep-this-text"


def claim_rows(count: int, *, start: int = 71004410, big: int | None = None) -> list[tuple]:
    """Claim rows; ``big`` gives the first claim a large incurred amount."""
    rows = [
        (f"{start + n}", f"{1 + n % 9:02d}/1{n % 9}/2022", "CLOSED",
         "1,000.00", "0.00", "1,000.00")
        for n in range(count)
    ]
    if big is not None and rows:
        rows[0] = (rows[0][0], rows[0][1], "OPEN", f"{big:,.2f}", "0.00", f"{big:,.2f}")
    return rows


def loss_run_pdf(
    *,
    insured: str = INSURED,
    rows: Sequence[tuple] | None = None,
    carrier: str = LARGE_CARRIER,
    policy: str = "GL-100",
    valuation: str = "12/31/2022",
    pages: int = 1,
    period: str | None = None,
) -> bytes:
    """A clean single-report loss run: letterhead, claims, printed total."""
    rows = list(rows if rows is not None else _large_run(6))
    document = pymupdf.open()
    per_page = max(1, -(-len(rows) // pages))
    chunks = [rows[i:i + per_page] for i in range(0, len(rows), per_page)] or [[]]
    for index, chunk in enumerate(chunks, start=1):
        _sheet(
            document,
            top=(carrier, *((f"Named Insured: {insured}",) if insured else ()),
                 f"Policy Number: {policy}",
                 *((f"Policy Period: {period}",) if period else ()),
                 "LOSS RUN REPORT", f"Valuation Date: {valuation}"),
            rows=chunk,
            total=_total(rows) if index == len(chunks) else None,
            bottom=(f"Page {index} of {len(chunks)}",),
        )
    data = document.tobytes()
    document.close()
    return data


def packet_pdf() -> bytes:
    """Two carriers' loss runs bound into one PDF (two logical runs)."""
    first, second = _large_run(4), _large_run(3)
    document = pymupdf.open()
    _sheet(document, top=(LARGE_CARRIER, f"Named Insured: {INSURED}", *LETTER),
           rows=first, total=_total(first), bottom=("Page 1 of 1",))
    _sheet(document, top=("Other Mutual Insurance Company", f"Named Insured: {INSURED}",
                          "LOSS RUN REPORT", "Valuation Date: 12/31/2022"),
           rows=second, total=_total(second), bottom=("Page 1 of 1",))
    data = document.tobytes()
    document.close()
    return data


def eml(
    attachments: Sequence[tuple[str | None, str, bytes]],
    *,
    subject: str = SUBJECT,
    sender: str = SENDER,
    html_body: bool = True,
) -> bytes:
    """A saved email. Each attachment is (filename, "type/subtype", bytes)."""
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "uw.assistant@example.test"
    message["Subject"] = subject
    message["Date"] = "Tue, 29 Sep 2026 10:15:00 -0400"
    message.set_content(f"Please find loss runs attached. {BODY_SECRET}")
    if html_body:
        message.add_alternative(
            f'<p>{BODY_SECRET}<img src="https://tracker.example.test/pixel.png"></p>',
            subtype="html",
        )
    for name, mime, data in attachments:
        maintype, subtype = mime.split("/", 1)
        if maintype == "message":
            inner = email.message_from_bytes(data, policy=email.policy.default)
            message.add_attachment(inner, filename=name)
            continue
        message.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return message.as_bytes()


def run(profiles_dir: Path):
    """The pipeline call a direct upload makes, with an isolated profile store."""
    return lambda staged: run_pipeline(staged, use_vision=False, profiles_dir=profiles_dir)
