"""Stage 3 — column mapping and the carrier profile library (spec section 5).

Two jobs:

* Decide which canonical field each printed column carries.  A deterministic
  vocabulary handles the labels carriers actually use; the LLM is asked only
  about headers the vocabulary cannot place, and only ever sees the header row
  plus three sample rows — never the whole table, and never to read a number.
* Remember the answer.  Once a human confirms a mapping it is saved against a
  fingerprint of the document's letterhead, so the next document from that
  carrier needs zero LLM calls.

A profile stores *structure only*.  :func:`sanitise_profile` enforces that with
an explicit whitelist so claim data can never reach disk (spec section 9).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from core.normalize import normalize_label
from core.schema import CANONICAL_FIELDS

PROFILE_DIR = Path(__file__).resolve().parent.parent / "data" / "profiles"

#: Fraction of page 1 used for the fingerprint — the letterhead block.
FINGERPRINT_TOP_FRACTION = 0.15

PROFILE_VERSION = 1


# --------------------------------------------------------------------------
# Label vocabulary
# --------------------------------------------------------------------------

#: Exact normalised label -> canonical field.  Checked before any heuristic.
LABEL_SYNONYMS: dict[str, str] = {
    # identity
    "claim number": "claim_number", "claim no": "claim_number",
    "claim nbr": "claim_number", "clm nbr": "claim_number",
    "clm no": "claim_number", "claim": "claim_number",
    "claim id": "claim_number", "file number": "claim_number",
    "file no": "claim_number", "claim num": "claim_number",
    "claimno": "claim_number", "occurrence number": "claim_number",
    "claim ref": "claim_number", "claim reference": "claim_number",
    "clm ref": "claim_number", "claim ref no": "claim_number",
    "claim reference number": "claim_number", "file ref": "claim_number",
    # dates
    "date of loss": "date_of_loss", "loss date": "date_of_loss",
    "dol": "date_of_loss", "loss dt": "date_of_loss",
    "date of accident": "date_of_loss", "accident date": "date_of_loss",
    "occurrence date": "date_of_loss", "doi": "date_of_loss",
    "date reported": "date_reported", "reported": "date_reported",
    "rptd": "date_reported", "rpt dt": "date_reported",
    "report date": "date_reported", "reported date": "date_reported",
    "notice date": "date_reported", "date rptd": "date_reported",
    # status and parties
    "status": "claim_status", "stat": "claim_status", "st": "claim_status",
    "claim status": "claim_status", "open closed": "claim_status",
    "claimant": "claimant_name", "claimant name": "claimant_name",
    "injured worker": "claimant_name", "employee": "claimant_name",
    "employee name": "claimant_name", "name": "claimant_name",
    # narrative
    "description of loss": "loss_description", "description": "loss_description",
    "loss description": "loss_description", "desc": "loss_description",
    "accident description": "loss_description",
    "description of accident": "loss_description",
    "cause": "cause_of_loss", "cause of loss": "cause_of_loss",
    "loss cause": "cause_of_loss", "peril": "cause_of_loss",
    "cause desc": "cause_of_loss", "coverage": "cause_of_loss",
    # money
    "paid": "paid_total", "paid total": "paid_total", "total paid": "paid_total",
    "pd tot": "paid_total", "paid to date": "paid_total",
    "reserve": "reserve_total", "reserves": "reserve_total",
    "outstanding": "reserve_total", "rsv tot": "reserve_total",
    "recovery": "recovery_total", "recoveries": "recovery_total",
    "recov": "recovery_total", "subrogation": "recovery_total",
    "subro": "recovery_total", "salvage": "recovery_total",
    "incurred": "incurred_total", "total incurred": "incurred_total",
    "net incurred": "incurred_total", "incurred total": "incurred_total",
    "total": "incurred_total",
    # litigation
    "suit": "litigation_flag", "litigation": "litigation_flag",
    "lit": "litigation_flag", "in suit": "litigation_flag",
    "litigated": "litigation_flag", "attorney": "litigation_flag",
}

_GROUP_TOKENS: dict[str, tuple[str, ...]] = {
    "paid": ("paid", "pd", "pay", "payments"),
    "reserve": ("reserve", "reserves", "res", "rsv", "rsrv", "outstanding", "o s"),
    "recovery": ("recovery", "recoveries", "recov", "subro", "subrogation", "salvage"),
    "incurred": ("incurred", "incur", "inc"),
}

_COMPONENT_TOKENS: dict[str, tuple[str, ...]] = {
    "indemnity": ("indemnity", "indem", "indm", "loss", "losses"),
    "medical": ("medical", "med"),
    "expense": ("expense", "expenses", "exp", "alae", "lae"),
    "total": ("total", "tot", "ttl"),
}


@dataclass(frozen=True)
class FieldGuess:
    field: str | None
    confidence: float
    source: str  # "synonym" | "heuristic" | "unmapped"


def guess_field(label: str) -> FieldGuess:
    """Map one printed column label onto a canonical field.

    Returns ``None`` rather than a bad guess: an unmapped column goes to the
    mapping screen (or the LLM), which is cheap.  A wrong mapping is not.
    """
    normalized = normalize_label(label)
    if not normalized:
        return FieldGuess(None, 0.0, "unmapped")

    if normalized in LABEL_SYNONYMS:
        return FieldGuess(LABEL_SYNONYMS[normalized], 1.0, "synonym")

    tokens = normalized.split()
    token_set = set(tokens)

    # Narrative and identity columns win before any money heuristic, so that
    # "Description of Loss" is not read as an indemnity column.
    if token_set & {"description", "desc", "narrative"}:
        return FieldGuess("loss_description", 0.8, "heuristic")
    if "cause" in token_set or "peril" in token_set:
        return FieldGuess("cause_of_loss", 0.8, "heuristic")
    if "claimant" in token_set or "injured" in token_set:
        return FieldGuess("claimant_name", 0.8, "heuristic")
    if token_set & {"claim", "clm", "file"} and token_set & {
        "number", "no", "nbr", "num", "id", "#", "ref", "reference"
    }:
        return FieldGuess("claim_number", 0.85, "heuristic")
    if token_set & {"status", "stat"}:
        return FieldGuess("claim_status", 0.8, "heuristic")
    if token_set & {"suit", "litigation", "litigated", "attorney"}:
        return FieldGuess("litigation_flag", 0.8, "heuristic")

    if token_set & {"date", "dt"} or token_set & {"dol", "doi"}:
        if token_set & {"report", "reported", "rptd", "rpt", "notice"}:
            return FieldGuess("date_reported", 0.85, "heuristic")
        if token_set & {"loss", "accident", "occurrence", "dol", "doi"}:
            return FieldGuess("date_of_loss", 0.85, "heuristic")

    group = next(
        (name for name, words in _GROUP_TOKENS.items() if token_set & set(words)),
        None,
    )
    component = next(
        (name for name, words in _COMPONENT_TOKENS.items() if token_set & set(words)),
        None,
    )

    if group == "recovery":
        return FieldGuess("recovery_total", 0.8, "heuristic")
    if group == "incurred":
        return FieldGuess("incurred_total", 0.8, "heuristic")
    if group in ("paid", "reserve"):
        if component in ("indemnity", "medical", "expense"):
            return FieldGuess(f"{group}_{component}", 0.85, "heuristic")
        return FieldGuess(f"{group}_total", 0.75, "heuristic")

    return FieldGuess(None, 0.0, "unmapped")


def map_headers(headers: Sequence[str]) -> dict[int, FieldGuess]:
    """Guess a field for every column, refusing to map one field twice.

    When two columns claim the same field the more confident one keeps it; the
    other is left unmapped for a human (or the LLM) to resolve.
    """
    guesses = {index: guess_field(label) for index, label in enumerate(headers)}
    claimed: dict[str, int] = {}
    for index, guess in sorted(
        guesses.items(), key=lambda pair: (-pair[1].confidence, pair[0])
    ):
        if guess.field is None:
            continue
        if guess.field in claimed:
            guesses[index] = FieldGuess(None, 0.0, "unmapped")
        else:
            claimed[guess.field] = index
    return guesses


def header_score(cells: Sequence[str]) -> int:
    """How many cells in this line look like loss-run column labels."""
    return sum(1 for cell in cells if guess_field(cell).field is not None)


def looks_like_header(cells: Sequence[str], minimum: int = 3) -> bool:
    """A header row names at least ``minimum`` recognisable columns."""
    return header_score(cells) >= minimum


# --------------------------------------------------------------------------
# Fingerprinting
# --------------------------------------------------------------------------

_CARRIER_SUFFIXES = (
    "insurance", "insurances", "assurance", "assurances", "casualty",
    "indemnity", "mutual", "underwriters", "company", "companies", "corp",
    "corporation", "group", "marine", "fire", "specialty", "speciality",
    "exchange", "reciprocal", "syndicate", "lloyds", "reinsurance",
    # Public entities self-insure through risk pools and funds, which carry
    # none of the company suffixes above but issue loss runs all the same.
    "fund", "funds", "pool", "authority", "trust",
    "versicherung", "versicherungen", "limited", "ins", "co", "ag", "se",
    "plc", "ltd", "llc", "inc", "nv", "sa",
)

_CARRIER_SUFFIX_RE = re.compile(
    r"\b([A-Z][\w&.\-']*(?:\s+[\w&.\-']+){0,5}\s+(?:%s)\b\.?)"
    % "|".join(_CARRIER_SUFFIXES),
    flags=re.IGNORECASE,
)

#: Lines that are page furniture rather than a carrier name.
_NOT_A_CARRIER = re.compile(
    r"^(loss\s*run|claims?\s*(?:listing|detail|report)|page\s+\d|"
    r"confidential|report\s*date|run\s*date)",
    flags=re.IGNORECASE,
)

#: Reports often print the query that produced them — "All Claims Where Claim
#: Status is Closed or Open As Of ...". That is a sentence, not a letterhead,
#: and it sits above the carrier name on the page. A carrier name is a noun
#: phrase, so the clause words that make this a sentence rule the line out.
_CRITERIA_PROSE = re.compile(
    r"\b(?:where|between|is\s+(?:closed|open|greater|less|equal)|"
    r"all\s+claims|selected\s+by|filtered|criteria)\b",
    flags=re.IGNORECASE,
)

#: The rare document that labels its carrier explicitly.
_CARRIER_LABEL = re.compile(
    r"^(?:carrier|insurer|underwriter|issuing\s+company)\s*[:\-]\s*(.+)",
    flags=re.IGNORECASE,
)

_PAGE_MARKER = re.compile(r"\s*page\s+\d+\s*(?:of\s*\d+)?\s*$", re.IGNORECASE)

#: Letterheads commonly read "CARRIER NAME - DOCUMENT TITLE" on one line.
_DOCUMENT_TITLE = re.compile(
    r"\b(loss\s*run|loss\s*report|claims?\s*(?:listing|report|detail|summary|"
    r"history|experience)|statement|report|listing|summary|experience)\b",
    flags=re.IGNORECASE,
)


def _strip_document_title(line: str) -> str:
    """Drop a trailing document title so the carrier name stands alone.

    "STATE AUTO - HISTORICAL LOSS REPORT" is State Auto. Keeping the title
    would put the report's name into the profile fingerprint and show it to
    the user as the carrier.
    """
    parts = re.split(r"\s+[-–—|]\s+", line)
    while len(parts) > 1 and _DOCUMENT_TITLE.search(parts[-1]):
        parts.pop()
    return " - ".join(parts).strip()


#: Words that belong to a metadata label rather than to a company name.
_LABEL_WORDS = frozenset(
    "printed report run date dated valued valuation page policy insured named "
    "claim claims period as of status location currency number no for".split()
)


def _before_label(line: str) -> str:
    """The text ahead of a trailing label, e.g. "ACME FUND  Printed: 3/1/24".

    Letterheads often share their line with a metadata label. Dropping the
    whole line for containing a colon loses the carrier name; keeping the words
    before the label recovers it, while a line that *starts* with a label —
    "Named Insured: ..." — correctly reduces to nothing.
    """
    head = line.split(":", 1)[0]
    words = head.split()
    while words and words[-1].strip(".,").lower() in _LABEL_WORDS:
        words.pop()
    return " ".join(words)


def detect_carrier(text: str) -> str | None:
    """Best-effort carrier name from the letterhead.

    Lines carrying a label are skipped entirely. "Named Insured: Whitfield
    Engineering Ltd" ends in a company suffix but names the *customer*, and
    mistaking it for the carrier would file every customer under its own
    carrier profile.
    """
    lines = text.splitlines()[:8]

    for line in lines:
        labelled = _CARRIER_LABEL.match(line.strip())
        if labelled:
            return " ".join(_PAGE_MARKER.sub("", labelled.group(1)).split())

    candidates: list[str] = []
    for line in lines:
        candidate = _PAGE_MARKER.sub("", line.strip())
        if ":" in candidate:
            candidate = _before_label(candidate)
        if not candidate or len(candidate) > 120:
            continue
        if _NOT_A_CARRIER.match(candidate) or _CRITERIA_PROSE.search(candidate):
            continue
        candidate = _strip_document_title(candidate)
        if candidate:
            candidates.append(candidate)

    for candidate in candidates:
        match = _CARRIER_SUFFIX_RE.search(candidate)
        if match:
            return " ".join(match.group(1).split())
    return " ".join(candidates[0].split()) if candidates else None


#: Header-block labels a loss run prints. Their *presence* identifies a
#: carrier's template; their values identify a customer, so only the labels
#: are ever hashed.
_TEMPLATE_LABELS = (
    "named insured", "insured", "policy number", "policy no", "policy period",
    "policy term", "valuation date", "valued as of", "evaluation date",
    "as of date", "line of business", "coverage", "currency", "loss run",
    "claims listing", "claim detail", "report date", "run date", "producer",
    "agent", "branch", "underwriter", "total claims", "number of claims",
)


def _template_labels(text: str) -> list[str]:
    """Which known labels appear in the header block."""
    lowered = " ".join(text.lower().split())
    return sorted({label for label in _TEMPLATE_LABELS if label in lowered})


def fingerprint(
    top_text: str, headers: Sequence[str] = (), carrier: str | None = None
) -> str:
    """Identify a carrier's *format*, not a customer's document.

    Hashes the carrier name, the column labels, and which header-block labels
    the template prints — never their values. Two loss runs from the same
    carrier in the same format therefore match even though the insured, the
    policy number and the valuation date all differ, which is the whole point:
    the second document must cost zero LLM calls.
    """
    parts = [
        normalize_label(carrier or ""),
        "|".join(normalize_label(header) for header in headers),
        "|".join(_template_labels(top_text)),
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:32]


def page_top_text(page_text: str, fraction: float = FINGERPRINT_TOP_FRACTION) -> str:
    """The top slice of a page's text, by line count."""
    lines = [line for line in page_text.splitlines() if line.strip()]
    if not lines:
        return ""
    take = max(1, round(len(lines) * fraction))
    return "\n".join(lines[:take])


# --------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------

#: The only keys a profile file may contain (spec section 9).
PROFILE_WHITELIST: frozenset[str] = frozenset(
    {
        "version",
        "fingerprint",
        "carrier",
        "profile_name",
        "column_map",
        "header_row_index",
        "date_order",
        "number_locale",
        "negative_convention",
        "recovery_convention",
        "dash_means_zero",
        "total_row_pattern",
        "money_tolerance",
        "currency",
        "line_of_business",
        "created_at",
        "updated_at",
        "confirmed_by_human",
        "times_used",
    }
)


class ProfileError(ValueError):
    """A profile is malformed or would carry claim data."""


@dataclass
class CarrierProfile:
    """A saved carrier format: structure only, never claim data."""

    fingerprint: str
    carrier: str | None = None
    profile_name: str | None = None
    #: printed column label -> canonical field name
    column_map: dict[str, str] = field(default_factory=dict)
    header_row_index: int | None = None
    date_order: str | None = None
    number_locale: str | None = None
    negative_convention: str | None = None
    #: "credit" when the carrier prints recoveries as negative amounts.
    recovery_convention: str | None = None
    dash_means_zero: bool = False
    total_row_pattern: str = r"^\s*(grand\s+)?(report\s+)?totals?\b"
    money_tolerance: str = "0.01"
    currency: str = "USD"
    line_of_business: str | None = None
    created_at: str = ""
    updated_at: str = ""
    confirmed_by_human: bool = False
    times_used: int = 0
    version: int = PROFILE_VERSION

    def field_for(self, label: str) -> str | None:
        """Look up a column label, tolerating whitespace and case drift."""
        if label in self.column_map:
            return self.column_map[label]
        target = normalize_label(label)
        for saved_label, saved_field in self.column_map.items():
            if normalize_label(saved_label) == target:
                return saved_field
        return None

    def to_dict(self) -> dict[str, Any]:
        return sanitise_profile(
            {
                "version": self.version,
                "fingerprint": self.fingerprint,
                "carrier": self.carrier,
                "profile_name": self.profile_name,
                "column_map": dict(self.column_map),
                "header_row_index": self.header_row_index,
                "date_order": self.date_order,
                "number_locale": self.number_locale,
                "negative_convention": self.negative_convention,
                "recovery_convention": self.recovery_convention,
                "dash_means_zero": self.dash_means_zero,
                "total_row_pattern": self.total_row_pattern,
                "money_tolerance": self.money_tolerance,
                "currency": self.currency,
                "line_of_business": self.line_of_business,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "confirmed_by_human": self.confirmed_by_human,
                "times_used": self.times_used,
            }
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CarrierProfile":
        clean = sanitise_profile(payload)
        clean.pop("version", None)
        return cls(version=PROFILE_VERSION, **clean)


def sanitise_profile(payload: dict[str, Any]) -> dict[str, Any]:
    """Enforce the structure-only whitelist.

    A profile that carried a claimant name or an amount would be customer data
    at rest, which the spec forbids.  Rather than dropping unexpected keys
    quietly, this raises: a silent drop would hide a bug that leaks data.
    """
    unexpected = set(payload) - PROFILE_WHITELIST
    if unexpected:
        raise ProfileError(
            "A carrier profile may only contain format structure. Refusing to "
            f"write unexpected key(s): {', '.join(sorted(unexpected))}"
        )

    column_map = payload.get("column_map") or {}
    if not isinstance(column_map, dict):
        raise ProfileError("column_map must be a mapping of label -> field")
    for label, field_name in column_map.items():
        if not isinstance(label, str) or not isinstance(field_name, str):
            raise ProfileError("column_map keys and values must both be strings")
        if field_name not in CANONICAL_FIELDS:
            raise ProfileError(
                f"column_map points {label!r} at {field_name!r}, which is not a "
                f"canonical field"
            )

    clean = {key: payload.get(key) for key in payload}
    clean["column_map"] = dict(column_map)
    return clean


def profile_path(fingerprint_value: str, directory: Path | None = None) -> Path:
    return (directory or PROFILE_DIR) / f"{fingerprint_value}.json"


def save_profile(
    profile: CarrierProfile, directory: Path | None = None
) -> Path:
    """Persist a profile.  Called after a human confirms the mapping."""
    target_dir = directory or PROFILE_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not profile.created_at:
        profile.created_at = now
    profile.updated_at = now

    path = profile_path(profile.fingerprint, target_dir)
    path.write_text(json.dumps(profile.to_dict(), indent=2, sort_keys=True) + "\n")
    return path


def load_profile(
    fingerprint_value: str, directory: Path | None = None
) -> CarrierProfile | None:
    """Load a saved profile, or ``None`` if this format has not been seen."""
    path = profile_path(fingerprint_value, directory)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise ProfileError(f"{path.name} is not valid JSON: {error}") from error
    return CarrierProfile.from_dict(payload)


def list_profiles(directory: Path | None = None) -> list[CarrierProfile]:
    target_dir = directory or PROFILE_DIR
    if not target_dir.exists():
        return []
    profiles = []
    for path in sorted(target_dir.glob("*.json")):
        try:
            profiles.append(CarrierProfile.from_dict(json.loads(path.read_text())))
        except (ProfileError, json.JSONDecodeError):
            continue  # a corrupt profile must not break the upload screen
    return profiles


def profile_from_mapping(
    fingerprint_value: str,
    headers: Sequence[str],
    mapping: dict[int, str | None],
    *,
    carrier: str | None = None,
    date_order: str | None = None,
    number_locale: str | None = None,
    recovery_convention: str | None = None,
    dash_means_zero: bool = False,
    currency: str = "USD",
    line_of_business: str | None = None,
    confirmed_by_human: bool = False,
) -> CarrierProfile:
    """Build a profile from the mapping screen's column choices."""
    column_map = {
        headers[index]: field_name
        for index, field_name in mapping.items()
        if field_name and 0 <= index < len(headers)
    }
    return CarrierProfile(
        fingerprint=fingerprint_value,
        carrier=carrier,
        profile_name=carrier or "Unnamed carrier format",
        column_map=column_map,
        date_order=date_order,
        number_locale=number_locale,
        recovery_convention=recovery_convention,
        dash_means_zero=dash_means_zero,
        currency=currency,
        line_of_business=line_of_business,
        confirmed_by_human=confirmed_by_human,
    )


# --------------------------------------------------------------------------
# LLM column mapping — structure only, never numbers
# --------------------------------------------------------------------------

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "map_columns.md"

#: How many sample rows the model is allowed to see (spec section 5, stage 3).
LLM_SAMPLE_ROWS = 3

DEFAULT_MODEL = "gemini-2.0-flash"


class LLMUnavailable(RuntimeError):
    """No API key, no client, or the call failed."""


def llm_enabled() -> bool:
    """The LLM is opt-in: without a key and the flag, mapping stays local."""
    return bool(os.getenv("GEMINI_API_KEY")) and os.getenv(
        "LOSSLIFT_ENABLE_LLM", "0"
    ).strip().lower() in {"1", "true", "yes"}


def build_mapping_prompt(
    headers: Sequence[str], sample_rows: Sequence[Sequence[str]]
) -> str:
    """Fill the committed prompt template.

    Only the header row and at most three sample rows are interpolated. The
    rest of the table never leaves the machine.
    """
    template = PROMPT_PATH.read_text(encoding="utf-8")
    instructions = template.split("### Input", 1)[0]

    rows = [
        " | ".join(str(cell) for cell in row)
        for row in list(sample_rows)[:LLM_SAMPLE_ROWS]
    ]
    return (
        instructions.strip()
        + "\n\n### Input\n\nHeaders: "
        + json.dumps(list(headers))
        + "\n\nSample rows:\n"
        + ("\n".join(rows) if rows else "(none)")
        + "\n"
    )


def parse_llm_mapping(
    payload: str | dict[str, Any], headers: Sequence[str]
) -> dict[int, FieldGuess]:
    """Read the model's JSON, discarding anything that is not a real field.

    The model is treated as a suggestion engine, not an authority: an unknown
    field name, a duplicate, or an out-of-range index is dropped rather than
    trusted.
    """
    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text, flags=re.IGNORECASE)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as error:
            raise LLMUnavailable(f"the model did not return JSON: {error}") from error
    else:
        data = payload

    columns = data.get("columns") if isinstance(data, dict) else None
    if not isinstance(columns, list):
        raise LLMUnavailable("the model's JSON has no 'columns' list")

    guesses: dict[int, FieldGuess] = {
        index: FieldGuess(None, 0.0, "unmapped") for index in range(len(headers))
    }
    claimed: set[str] = set()
    for entry in columns:
        if not isinstance(entry, dict):
            continue
        index = entry.get("index")
        field_name = entry.get("field")
        if not isinstance(index, int) or not 0 <= index < len(headers):
            continue
        if field_name is None:
            continue
        if field_name not in CANONICAL_FIELDS or field_name in claimed:
            continue
        try:
            confidence = float(entry.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        claimed.add(field_name)
        guesses[index] = FieldGuess(field_name, min(max(confidence, 0.0), 1.0), "llm")
    return guesses


def llm_map_columns(
    headers: Sequence[str],
    sample_rows: Sequence[Sequence[str]] = (),
    *,
    client: Any | None = None,
    model: str | None = None,
) -> dict[int, FieldGuess]:
    """Ask Gemini to map the headers the vocabulary could not place.

    Raises :class:`LLMUnavailable` rather than returning a bad mapping, so the
    caller falls back to the mapping screen.
    """
    if client is None:
        if not llm_enabled():
            raise LLMUnavailable(
                "The LLM is switched off. Set GEMINI_API_KEY and "
                "LOSSLIFT_ENABLE_LLM=1, or map the columns on the mapping screen."
            )
        try:
            from google import genai
        except ImportError as error:  # pragma: no cover - dependency missing
            raise LLMUnavailable("google-genai is not installed") from error
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    prompt = build_mapping_prompt(headers, sample_rows)
    target = model or os.getenv("LOSSLIFT_GEMINI_MODEL", DEFAULT_MODEL)
    try:
        response = client.models.generate_content(model=target, contents=prompt)
        text = getattr(response, "text", None) or ""
    except Exception as error:  # noqa: BLE001 - any transport failure is the same
        raise LLMUnavailable(f"the mapping call failed: {error}") from error
    return parse_llm_mapping(text, headers)


def resolve_columns(
    headers: Sequence[str],
    sample_rows: Sequence[Sequence[str]] = (),
    *,
    profile: CarrierProfile | None = None,
    use_llm: bool = False,
    client: Any | None = None,
) -> tuple[dict[int, FieldGuess], str]:
    """Map headers by profile, then vocabulary, then — only if asked — the LLM.

    Returns the guesses and which source settled it.  A saved profile short
    circuits everything, which is the point: the fortieth document from a
    carrier costs nothing.
    """
    if profile is not None:
        guesses = {
            index: (
                FieldGuess(profile.field_for(header), 1.0, "profile")
                if profile.field_for(header)
                else FieldGuess(None, 0.0, "unmapped")
            )
            for index, header in enumerate(headers)
        }
        if any(guess.field for guess in guesses.values()):
            return guesses, "profile"

    guesses = map_headers(headers)
    unmapped = [index for index, guess in guesses.items() if guess.field is None]
    if not unmapped or not use_llm:
        return guesses, "heuristic"

    try:
        llm_guesses = llm_map_columns(headers, sample_rows, client=client)
    except LLMUnavailable:
        return guesses, "heuristic"

    # The vocabulary is deterministic and reviewed; it wins any disagreement.
    claimed = {guess.field for guess in guesses.values() if guess.field}
    for index in unmapped:
        candidate = llm_guesses.get(index)
        if candidate and candidate.field and candidate.field not in claimed:
            guesses[index] = candidate
            claimed.add(candidate.field)
    return guesses, "llm"
