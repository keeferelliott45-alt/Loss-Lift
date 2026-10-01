"""Small synthetic PDFs for the intake and lifecycle tests.

Every file here is generated in code. A test that expects a PDF to be staged
needs one that opens: since the shared preflight (``core.ingest.preflight_pdf``)
opens every PDF before staging it, bytes that merely start with ``%PDF-`` are
refused as damaged -- which is what the rejection tests use them for.
"""

from __future__ import annotations

import io

import pymupdf


def synthetic_pdf(label: str = "synthetic", pages: int = 1) -> bytes:
    """A valid PDF whose pages print ``label``; distinct labels give distinct bytes."""
    document = pymupdf.open()
    for number in range(1, pages + 1):
        page = document.new_page()
        page.insert_text((72, 72), f"{label} page {number}")
    data = document.tobytes()
    document.close()
    return data


def password_pdf(label: str = "locked", *, user: str = "user", owner: str = "owner") -> bytes:
    """A PDF that needs a password to open."""
    document = pymupdf.open(stream=synthetic_pdf(label), filetype="pdf")
    data = document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256,
                            owner_pw=owner, user_pw=user)
    document.close()
    return data


def owner_only_pdf(label: str = "restricted", *, owner: str = "owner") -> bytes:
    """A PDF with only an owner password: printing and copying restricted,
    opening not."""
    document = pymupdf.open(stream=synthetic_pdf(label), filetype="pdf")
    data = document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256,
                            owner_pw=owner, user_pw="", permissions=0)
    document.close()
    return data


def zero_page_pdf() -> bytes:
    """A well-formed PDF whose page tree is empty.

    Written by hand: PyMuPDF refuses to save a document with no pages.
    """
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [] /Count 0 >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
              % (len(objects) + 1, xref))
    return out.getvalue()


#: Starts like a PDF and is not one.
MALFORMED_PDF = b"%PDF-1.7\nnot really a pdf but it starts right"
