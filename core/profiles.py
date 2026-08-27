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
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from core.normalize import Locale, normalize_label
from core.schema import CANONICAL_FIELDS, MONEY_FIELDS

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
        "number", "no", "nbr", "num", "id", "#"
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
    "insurance", "assurance", "casualty", "indemnity", "mutual", "underwriters",
    "company", "corp", "group", "marine", "fire", "specialty", "exchange",
    "reciprocal", "syndicate", "lloyds", "reinsurance", "versicherung",
    "assurances", "ins", "co", "ag", "se", "plc", "ltd", "llc", "inc",
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

_PAGE_MARKER = re.compile(r"\s*page\s+\d+\s*(?:of\s*\d+)?\s*$", re.IGNORECASE)


def detect_carrier(text: str) -> str | None:
    """Best-effort carrier name from the letterhead.

    Loss runs put the carrier on the first line.  A suffix match ("… Casualty
    Company") is preferred; failing that, the first line that is not a label,
    a title or page furniture is taken.
    """
    candidates: list[str] = []
    for line in text.splitlines()[:8]:
        candidate = _PAGE_MARKER.sub("", line.strip())
        if not candidate or len(candidate) > 80:
            continue
        if _NOT_A_CARRIER.match(candidate):
            continue
        candidates.append(candidate)

    for candidate in candidates:
        match = _CARRIER_SUFFIX_RE.search(candidate)
        if match:
            return " ".join(match.group(1).split())

    for candidate in candidates:
        if ":" in candidate:
            continue  # a labelled field, not a letterhead
        return " ".join(candidate.split())
    return None


def _normalise_fingerprint_text(text: str) -> str:
    """Strip everything that varies between documents from the same carrier."""
    lowered = text.lower()
    lowered = re.sub(r"\d", "#", lowered)          # dates, policy numbers, amounts
    lowered = re.sub(r"[^a-z#\s]", " ", lowered)   # punctuation
    lowered = re.sub(r"#+", "#", lowered)
    return " ".join(lowered.split())


def fingerprint(
    top_text: str, headers: Sequence[str] = (), carrier: str | None = None
) -> str:
    """Hash the letterhead plus the column labels.

    Two loss runs from the same carrier in the same format hash the same; a
    different format from the same carrier does not, because the header labels
    are part of the hash.
    """
    parts = [
        _normalise_fingerprint_text(top_text),
        "|".join(normalize_label(header) for header in headers),
        normalize_label(carrier or ""),
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
        dash_means_zero=dash_means_zero,
        currency=currency,
        line_of_business=line_of_business,
        confirmed_by_human=confirmed_by_human,
    )
