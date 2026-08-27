"""Stage 3 — column mapping, fingerprinting and the carrier profile library."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from core.pipeline import build_mapping, run_pipeline, sample_rows, save_confirmed_mapping
from core.profiles import (
    CarrierProfile,
    FieldGuess,
    LLMUnavailable,
    ProfileError,
    build_mapping_prompt,
    detect_carrier,
    fingerprint,
    guess_field,
    list_profiles,
    load_profile,
    map_headers,
    page_top_text,
    parse_llm_mapping,
    profile_from_mapping,
    resolve_columns,
    sanitise_profile,
    save_profile,
)
from core.schema import CANONICAL_FIELDS


class CountingClient:
    """A stand-in Gemini client that records how often it was called."""

    def __init__(self, response: str = '{"columns": []}') -> None:
        self.response = response
        self.calls = 0
        self.prompts: list[str] = []
        self.models = self

    def generate_content(self, model: str, contents: str):
        self.calls += 1
        self.prompts.append(contents)
        return type("Response", (), {"text": self.response})()


# --- Vocabulary ------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Claim Number", "claim_number"), ("CLM NBR", "claim_number"),
        ("Claim #", "claim_number"), ("Date of Loss", "date_of_loss"),
        ("DOL", "date_of_loss"), ("LOSS DT", "date_of_loss"),
        ("Date Reported", "date_reported"), ("RPT DT", "date_reported"),
        ("Status", "claim_status"), ("ST", "claim_status"),
        ("Claimant", "claimant_name"), ("Description of Loss", "loss_description"),
        ("Cause", "cause_of_loss"), ("Paid Indem", "paid_indemnity"),
        ("PD LOSS", "paid_indemnity"), ("Paid Med", "paid_medical"),
        ("Paid Exp", "paid_expense"), ("PD TOT", "paid_total"),
        ("Paid", "paid_total"), ("RSV LOSS", "reserve_indemnity"),
        ("Res Med", "reserve_medical"), ("RSV EXP", "reserve_expense"),
        ("Reserve", "reserve_total"), ("Outstanding", "reserve_total"),
        ("Recovery", "recovery_total"), ("Subrogation", "recovery_total"),
        ("Total Incurred", "incurred_total"), ("INCURRED", "incurred_total"),
        ("Suit", "litigation_flag"),
    ],
)
def test_known_labels_map_without_an_llm(label, expected):
    assert guess_field(label).field == expected


@pytest.mark.parametrize("label", ["Widget Code", "Adjuster", "", "   ", "Notes"])
def test_unknown_labels_return_none_rather_than_guessing(label):
    assert guess_field(label).field is None


def test_description_of_loss_is_not_read_as_an_indemnity_column():
    assert guess_field("Description of Loss").field == "loss_description"


def test_a_field_is_never_mapped_twice():
    guesses = map_headers(["Paid", "Paid Total", "Incurred"])
    fields = [guess.field for guess in guesses.values()]
    assert fields.count("paid_total") == 1
    assert None in fields


def test_every_mapped_field_is_canonical():
    for label in ("Claim Number", "Paid", "Reserve", "Suit", "Cause"):
        field_name = guess_field(label).field
        assert field_name in CANONICAL_FIELDS


# --- Carrier detection and fingerprinting ----------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Meridian Casualty Company Page 1 of 1", "Meridian Casualty Company"),
        ("ATLANTIC STATES INS CO", "ATLANTIC STATES INS CO"),
        ("Northgate Fire & Marine\nLOSS RUN REPORT", "Northgate Fire & Marine"),
        ("LOSS RUN REPORT\nStatewide Mutual Insurance", "Statewide Mutual Insurance"),
    ],
)
def test_detect_carrier(text, expected):
    assert detect_carrier(text) == expected


def test_page_top_text_takes_the_letterhead():
    text = "\n".join(f"line {i}" for i in range(20))
    assert page_top_text(text, 0.15).splitlines() == ["line 0", "line 1", "line 2"]


def test_fingerprint_survives_a_different_insured():
    """The second document from a carrier must match the first."""
    template = "Named Insured: {insured}\nPolicy Number: {policy}\nValuation Date: {date}"
    headers = ["Claim Number", "Paid", "Incurred"]
    first = fingerprint(
        template.format(insured="Harbor Point LLC", policy="GL-1", date="12/31/2024"),
        headers, "Meridian Casualty Company",
    )
    second = fingerprint(
        template.format(insured="Copper Creek Inc", policy="GL-99", date="06/30/2025"),
        headers, "Meridian Casualty Company",
    )
    assert first == second


def test_fingerprint_changes_with_the_columns():
    top = "Named Insured: X\nPolicy Number: Y"
    assert fingerprint(top, ["Claim Number"], "Acme Ins") != fingerprint(
        top, ["Clm No"], "Acme Ins"
    )


def test_fingerprint_changes_with_the_carrier():
    top = "Named Insured: X\nPolicy Number: Y"
    assert fingerprint(top, ["Claim Number"], "Acme Ins") != fingerprint(
        top, ["Claim Number"], "Beta Casualty"
    )


def test_fingerprint_is_stable():
    top = "Named Insured: X\nPolicy Number: Y"
    assert fingerprint(top, ["A"], "C") == fingerprint(top, ["A"], "C")


# --- Persistence and the data-handling whitelist ---------------------------


def test_round_trip(profiles_dir):
    profile = CarrierProfile(
        fingerprint="abc123",
        carrier="Meridian Casualty Company",
        column_map={"Claim Number": "claim_number", "Paid": "paid_total"},
        date_order="mdy",
        number_locale="us",
        confirmed_by_human=True,
    )
    save_profile(profile, profiles_dir)
    loaded = load_profile("abc123", profiles_dir)
    assert loaded is not None
    assert loaded.carrier == "Meridian Casualty Company"
    assert loaded.column_map == profile.column_map
    assert loaded.date_order == "mdy"
    assert loaded.confirmed_by_human is True
    assert loaded.created_at and loaded.updated_at


def test_missing_profile_is_none(profiles_dir):
    assert load_profile("never-seen", profiles_dir) is None


def test_profile_refuses_to_store_claim_data():
    with pytest.raises(ProfileError, match="format structure"):
        sanitise_profile({"fingerprint": "x", "claims": [{"claimant_name": "Real Person"}]})


def test_profile_refuses_a_non_canonical_target():
    with pytest.raises(ProfileError, match="canonical field"):
        sanitise_profile({"fingerprint": "x", "column_map": {"Paid": "total_paid_amount"}})


def test_saved_profile_contains_only_structure(profiles_dir):
    profile = CarrierProfile(
        fingerprint="struct",
        carrier="Acme Insurance",
        column_map={"Claimant": "claimant_name"},
    )
    path = save_profile(profile, profiles_dir)
    payload = json.loads(path.read_text())
    assert set(payload) <= set(sanitise_profile(payload))
    # The column map holds labels and field names, never values.
    assert payload["column_map"] == {"Claimant": "claimant_name"}
    assert "Alvarez" not in path.read_text()


def test_field_for_tolerates_whitespace_and_case(profiles_dir):
    profile = CarrierProfile(fingerprint="x", column_map={"Claim Number": "claim_number"})
    assert profile.field_for("Claim Number") == "claim_number"
    assert profile.field_for("  claim   number ") == "claim_number"
    assert profile.field_for("Something Else") is None


def test_list_profiles_skips_corrupt_files(profiles_dir):
    save_profile(CarrierProfile(fingerprint="good", carrier="Acme Ins"), profiles_dir)
    (profiles_dir / "broken.json").write_text("{not json")
    profiles = list_profiles(profiles_dir)
    assert [p.carrier for p in profiles] == ["Acme Ins"]


def test_profile_from_mapping_drops_unmapped_columns():
    profile = profile_from_mapping(
        "fp", ["Claim Number", "Junk"], {0: "claim_number", 1: None}
    )
    assert profile.column_map == {"Claim Number": "claim_number"}


# --- LLM mapping -----------------------------------------------------------


def test_prompt_carries_only_headers_and_three_sample_rows():
    rows = [[f"row{i}-cell" for _ in range(3)] for i in range(10)]
    prompt = build_mapping_prompt(["A", "B", "C"], rows)
    assert "row0-cell" in prompt and "row2-cell" in prompt
    assert "row3-cell" not in prompt, "the model must not see the whole table"


def test_parse_llm_mapping_drops_invalid_fields():
    headers = ["A", "B", "C"]
    guesses = parse_llm_mapping(
        json.dumps({"columns": [
            {"index": 0, "field": "claim_number", "confidence": 0.9},
            {"index": 1, "field": "not_a_field"},
            {"index": 2, "field": "claim_number"},      # duplicate
            {"index": 9, "field": "paid_total"},        # out of range
        ]}),
        headers,
    )
    assert guesses[0].field == "claim_number"
    assert guesses[1].field is None
    assert guesses[2].field is None


def test_parse_llm_mapping_tolerates_code_fences():
    guesses = parse_llm_mapping(
        '```json\n{"columns": [{"index": 0, "field": "paid_total"}]}\n```', ["A"]
    )
    assert guesses[0].field == "paid_total"


def test_parse_llm_mapping_rejects_non_json():
    with pytest.raises(LLMUnavailable):
        parse_llm_mapping("I think column A is the claim number.", ["A"])


def test_llm_is_not_called_when_the_vocabulary_suffices():
    client = CountingClient()
    headers = ["Claim Number", "Date of Loss", "Paid", "Reserve", "Total Incurred"]
    guesses, source = resolve_columns(headers, use_llm=True, client=client)
    assert client.calls == 0
    assert source == "heuristic"
    assert guesses[0].field == "claim_number"


def test_llm_fills_only_the_gaps():
    client = CountingClient(json.dumps({"columns": [
        {"index": 0, "field": "claim_number", "confidence": 0.9},
        {"index": 1, "field": "paid_total", "confidence": 0.9},   # already taken
    ]}))
    guesses, source = resolve_columns(
        ["Ref No", "Paid"], [["A1", "100.00"]], use_llm=True, client=client
    )
    assert client.calls == 1
    assert source == "llm"
    assert guesses[0].field == "claim_number"   # the LLM filled the gap
    assert guesses[1].field == "paid_total"     # the vocabulary kept its own


def test_a_failing_llm_falls_back_to_the_vocabulary():
    class Broken(CountingClient):
        def generate_content(self, model, contents):
            self.calls += 1
            raise RuntimeError("503 upstream")

    client = Broken()
    guesses, source = resolve_columns(["Ref No", "Paid"], use_llm=True, client=client)
    assert source == "heuristic"
    assert guesses[1].field == "paid_total"


# --- The milestone 4 done-condition ----------------------------------------


def test_unknown_headers_need_mapping_and_extract_nothing(golden_dir):
    """Fail loud: an unmappable table yields no claims rather than wrong ones."""
    result = run_pipeline(golden_dir / "unknown_format.pdf", use_vision=False)
    assert result.needs_mapping is True
    assert result.document.claims == []


def test_second_document_from_the_same_carrier_costs_zero_llm_calls(
    golden_dir, profiles_dir, tmp_path
):
    """The milestone 4 done-condition, end to end."""
    from tests.golden import fixtures as fx
    from tests.golden.generate import render

    client = CountingClient(json.dumps({"columns": [
        {"index": 0, "field": "claim_number", "confidence": 0.95},
        {"index": 2, "field": "date_reported", "confidence": 0.9},
        {"index": 3, "field": "claim_status", "confidence": 0.9},
        {"index": 4, "field": "claimant_name", "confidence": 0.9},
        {"index": 6, "field": "reserve_total", "confidence": 0.9},
        {"index": 7, "field": "recovery_total", "confidence": 0.9},
    ]}))

    # First document: the vocabulary cannot place these headers, so the LLM is
    # asked once and the confirmed mapping is saved as a profile.
    first = run_pipeline(
        golden_dir / "unknown_format.pdf",
        profiles_dir=profiles_dir,
        use_vision=False,
        use_llm=True,
        llm_client=client,
    )
    assert client.calls == 1
    assert first.mapping.source == "llm"
    assert first.mapping.is_usable()
    assert len(first.document.claims) == 6
    save_confirmed_mapping(first, profiles_dir=profiles_dir)

    # A different document from the same carrier: different insured, different
    # policy number, different valuation date, different claims.
    second_fixture = dataclasses.replace(
        fx.UNKNOWN_FORMAT,
        name="unknown_format_second",
        named_insured="Ravensworth Plant Hire Ltd",
        policy_number="ASL/2025/01477",
        policy_period=(fx.date(2025, 1, 1), fx.date(2025, 12, 31)),
        valuation_date=fx.date(2025, 6, 30),
        claims=fx.UNKNOWN_FORMAT.claims[:4],
    )
    second_pdf = render(second_fixture, tmp_path / "second.pdf")

    calls_before = client.calls
    second = run_pipeline(
        second_pdf,
        profiles_dir=profiles_dir,
        use_vision=False,
        use_llm=True,
        llm_client=client,
    )

    assert client.calls == calls_before, "the saved profile must avoid the LLM"
    assert second.mapping.source == "profile"
    assert second.profile is not None
    assert len(second.document.claims) == 4
    assert second.document.claims[0].claim_number == "GL-2024-0001"
    assert second.document.valuation_date.isoformat() == "2025-06-30"


def test_saving_a_mapping_records_the_formats_it_learned(golden_dir, profiles_dir):
    result = run_pipeline(
        golden_dir / "eu_format.pdf", profiles_dir=profiles_dir, use_vision=False
    )
    profile = save_confirmed_mapping(result, profiles_dir=profiles_dir)
    assert profile.number_locale == "eu"
    assert profile.date_order == "dmy"
    assert profile.currency == "EUR"
    assert profile.confirmed_by_human is True
    assert profile.column_map["Total Incurred"] == "incurred_total"

    reloaded = load_profile(profile.fingerprint, profiles_dir)
    assert reloaded.number_locale == "eu"


def test_a_profile_forces_its_own_number_locale(golden_dir, profiles_dir):
    """A confirmed profile overrides inference, so an all-round-numbers
    document from a known EU carrier is still read the EU way."""
    result = run_pipeline(
        golden_dir / "eu_format.pdf", profiles_dir=profiles_dir, use_vision=False
    )
    save_confirmed_mapping(result, profiles_dir=profiles_dir)
    again = run_pipeline(
        golden_dir / "eu_format.pdf", profiles_dir=profiles_dir, use_vision=False
    )
    assert again.locale.locale == "eu"
    assert again.locale.confident is True
    assert again.mapping.source == "profile"
