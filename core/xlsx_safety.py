"""One policy for every value written into an exported workbook.

A loss run is somebody else's document. The text LossLift copies out of it —
carrier, insured, claimant, narrative, a reviewer's note — arrives with no
promise that it is safe to hand to a spreadsheet program, and openpyxl will
faithfully store a string beginning ``=`` as a **formula**. A workbook that
opens by running a calculation is not a spreadsheet; it is a delivery
mechanism, and the broker sends it on.

Two rules apply to every cell in every sheet, without exception:

* **A string is always a string.** The data type is set explicitly rather than
  left to openpyxl's inference, so ``=cmd|...`` is stored as the text it is and
  never as ``<f>``.
* **A string that could be read as a formula is marked as literal text** with
  ``quotePrefix``, so that if the recipient later edits the cell Excel does not
  suddenly start evaluating what the carrier printed. The characters are kept
  byte for byte: no apostrophe is added to the visible value.

Control characters XML forbids are replaced with U+FFFD and counted, because
one stray ``\\x07`` in a narrative must not abort a hundred-row export. The
count is reported on Source Info, so a reader can see that something was
repaired rather than quietly changed.

Redaction is expressed here too, as :class:`RedactionPolicy`: the set of
sensitive values is scrubbed from every remaining text cell as defence in
depth, and the specific cells that must not be soft-scrubbed (reviewer notes,
correction values on a redacted field) are replaced outright.

Numbers, dates and booleans are untouched: negative money stays numeric, dates
stay dates, and text that merely looks numeric — ``-0001`` — stays text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Iterable, Sequence

from openpyxl.cell.cell import Cell

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.schema import LossRunDocument

#: What a control character XML forbids becomes.
REPLACEMENT_CHARACTER = "\ufffd"

#: The characters Excel treats as the start of a formula. A leading TAB, CR or
#: LF counts too: a program that pastes text in may strip it and leave a
#: formula behind.
FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r", "\n")

#: XML 1.0 forbids these outright, and openpyxl raises ``IllegalCharacterError``
#: on every one of them. TAB, LF and CR are *not* here: they are legal and kept.
_ILLEGAL_XML_CHARACTERS = re.compile(r"[\000-\010]|[\013-\014]|[\016-\037]")

#: What a reviewer note becomes when claimant-data redaction is on.
WITHHELD_NOTE = "[withheld: claimant data redaction on]"

#: What a value on a redacted field becomes when it must not be soft-scrubbed.
REDACTED_VALUE = "[redacted]"

#: The fields redaction removes. Kept in step with core.schema.REDACTED_FIELDS.
_REDACTED_FIELDS = ("claimant_name", "loss_description")


def sanitize_control_characters(text: str) -> tuple[str, int]:
    """Replace the control characters XML forbids; return the text and a count."""
    if not _ILLEGAL_XML_CHARACTERS.search(text):
        return text, 0
    return _ILLEGAL_XML_CHARACTERS.sub(REPLACEMENT_CHARACTER, text), len(
        _ILLEGAL_XML_CHARACTERS.findall(text)
    )


def needs_quote_prefix(text: str) -> bool:
    """Whether Excel could read this text as the start of a formula.

    Leading spaces are skipped — ``" =1"`` is still a formula Excel will
    evaluate when the cell is edited — but a leading TAB, CR or LF is itself a
    reason to mark the cell, since a consumer may strip it.
    """
    if not text:
        return False
    if text[0] in ("\t", "\r", "\n"):
        return True
    stripped = text.lstrip(" \t\r\n")
    if not stripped:
        return False
    return stripped[0] in ("=", "+", "-", "@")


def _name_tokens(value: str) -> set[str]:
    """Distinctive parts of a name, so a partial mention is scrubbed too.

    A finding message or a carrier string can quote a surname without the full
    "Surname, Given" the claim carries. Scrubbing only the whole value would
    leave that behind, so each word of length >= 3 is treated as sensitive
    too. This can over-redact a name that is also an ordinary word; that cost
    is paid only while redaction is on, and privacy wins.
    """
    return {
        token
        for token in re.split(r"[^0-9A-Za-z\u00c0-\u024f]+", value)
        # Alphabetic only, so a name that contains a number does not make the
        # scrub redact every year or claim number it appears in.
        if len(token) >= 3 and token.isalpha()
    }


@dataclass(frozen=True)
class RedactionPolicy:
    """The sensitive values to keep out of a workbook, and how to keep them out.

    ``values`` are the claimant names and loss descriptions (current and
    originally extracted) plus reviewer before/after values on those fields.
    They are scrubbed from every remaining text cell as a substring, longest
    first, so ``Alvarez, Marisol`` does not survive inside a longer sentence.

    ``name_tokens`` are the distinctive parts of a claimant name (see
    :func:`_name_tokens`). They are applied only to cells that transcribe the
    document's own words — a finding message, an expected/actual value, a
    review message — never to LossLift's own labels or headers, so a partial
    surname quoted in a message does not survive while the disclosure line
    "Claimant data redacted" is not itself scrubbed.

    The soft scrub is defence in depth, not the guarantee: the guarantee is
    that the redacted columns are dropped, reviewer notes are withheld whole,
    and before/after on a redacted field is replaced whole.
    """

    values: tuple[str, ...] = ()
    name_tokens: tuple[str, ...] = ()
    _pattern: re.Pattern[str] | None = field(default=None, compare=False, repr=False)
    _token_pattern: re.Pattern[str] | None = field(
        default=None, compare=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_pattern", _compile(self.values))
        object.__setattr__(self, "_token_pattern", _compile(self.name_tokens))

    @classmethod
    def from_document(cls, document: "LossRunDocument") -> "RedactionPolicy":
        return cls.from_documents([document])

    @classmethod
    def from_documents(
        cls, documents: Iterable["LossRunDocument"]
    ) -> "RedactionPolicy":
        found: set[str] = set()
        tokens: set[str] = set()

        def collect_name(value: object) -> None:
            if isinstance(value, str) and value.strip():
                tokens.update(_name_tokens(value.strip()))

        for document in documents:
            for claim in document.claims:
                for name in _REDACTED_FIELDS:
                    value = getattr(claim, name, None)
                    original = claim.original_values.get(name)
                    for candidate in (value, original):
                        if isinstance(candidate, str) and candidate.strip():
                            found.add(candidate.strip())
                    if name == "claimant_name":
                        collect_name(value)
                        collect_name(original)
            for entry in document.review_log.entries:
                if entry.field in _REDACTED_FIELDS:
                    for value in (entry.before, entry.after):
                        if isinstance(value, str) and value.strip():
                            found.add(value.strip())
                    if entry.field == "claimant_name":
                        collect_name(entry.before)
                        collect_name(entry.after)
        # Longest first so a full name masks before its surname does.
        return cls(
            values=tuple(sorted(found, key=len, reverse=True)),
            name_tokens=tuple(sorted(tokens, key=len, reverse=True)),
        )

    def scrub(self, text: str) -> str:
        """Replace every occurrence of a sensitive value with ``[redacted]``."""
        if not text or self._pattern is None:
            return text
        return self._pattern.sub(REDACTED_VALUE, text)

    def scrub_name_tokens(self, text: str) -> str:
        """Scrub partial claimant names from a transcribed cell."""
        if not text or self._token_pattern is None:
            return text
        return self._token_pattern.sub(REDACTED_VALUE, text)

    @property
    def withheld_note(self) -> str:
        return WITHHELD_NOTE


def _compile(values: tuple[str, ...]) -> re.Pattern[str] | None:
    if not values:
        return None
    return re.compile("|".join(re.escape(value) for value in values), re.IGNORECASE)


@dataclass
class TextPolicy:
    """The single writer every workbook value passes through.

    ``redaction`` is present only when the export is redacted. The replacement
    count is accumulated across the whole workbook and read once by Source Info.
    """

    redaction: RedactionPolicy | None = None
    replaced_control_characters: int = 0

    def prepare(self, value: Any) -> Any:
        """The value as it should be stored, before any cell formatting."""
        if isinstance(value, bool):
            # A bool is a fact about the claim, not a spreadsheet boolean; the
            # workbook reads "Yes"/"No" so a recipient cannot mistake it for a
            # computed flag.
            return "Yes" if value else "No"
        if isinstance(value, Decimal):
            # openpyxl has no Decimal; float is presentation only and never
            # feeds a reconciliation.
            return float(value)
        if isinstance(value, str):
            clean, replaced = sanitize_control_characters(value)
            self.replaced_control_characters += replaced
            if self.redaction is not None:
                clean = self.redaction.scrub(clean)
            return clean
        return value

    def write(self, cell: Cell, value: Any) -> Any:
        """Store ``value`` in ``cell`` under the policy; return what was stored."""
        prepared = self.prepare(value)
        cell.value = prepared
        if isinstance(prepared, str):
            # Never rely on openpyxl's inference: a leading '=' would be stored
            # as a formula. Force the string type explicitly.
            cell.data_type = "s"
            if needs_quote_prefix(prepared):
                cell.quotePrefix = True
        return prepared

    def prepare_text(self, value: str) -> str:
        """A text value as it would be stored, without touching a cell."""
        prepared = self.prepare(value)
        return prepared if isinstance(prepared, str) else str(prepared)


def safe_member_name(name: str, policy: TextPolicy | None = None) -> str:
    """A ZIP entry name that cannot smuggle a control character or a payload.

    Zipped names become files on a recipient's disk, and an exported workbook's
    name derives from text the carrier printed, so the same care applies as to
    a cell. Path separators are collapsed so a crafted filename cannot write
    outside the folder the archive is extracted into.
    """
    clean, _ = sanitize_control_characters(name)
    if policy is not None:
        clean = policy.prepare_text(clean)
    clean = clean.replace("\\", "/")
    clean = clean.replace("/", "-")
    clean = clean.strip().strip(".")
    return clean or "losslift-export.xlsx"
