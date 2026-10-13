"""Regression coverage for the TrueType font resolution in
``tests/golden/generate.py``.

The generator embeds a TrueType face whenever a fixture prints a non-ASCII
currency symbol, because PyMuPDF's base-14 fonts cannot encode one (see the
comment above ``font_files`` in generate.py). Historically the search only
ever looked at Unix/macOS install paths, so on a Windows machine with none of
those paths present, rendering a fixture like ``qa_european_credit`` (which
prints "EUR") raised ``RuntimeError`` -- worked around locally by copying
Arial into a fake ``/usr/share/fonts/truetype/dejavu`` directory. This file
proves the resolver finds a suitable face through the real Windows Fonts
directory instead, without that workaround.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

from tests.golden import generate
from tests.golden.fixtures import Column, Fixture


def _euro_fixture() -> Fixture:
    return Fixture(
        name="test_euro_font_fixture",
        description="test-only fixture for font resolution",
        carrier="Test Carrier",
        named_insured="Test Insured",
        policy_number="TEST-1",
        policy_period=(date(2024, 1, 1), date(2024, 12, 31)),
        valuation_date=date(2024, 6, 30),
        line_of_business="GL",
        columns=(Column("Claim #", "claim_number"),),
        claims=(),
        currency_symbol="€",
    )


def test_windows_fonts_dir_reads_the_environment_not_a_user_path(monkeypatch, tmp_path):
    """The Windows Fonts directory must come from WINDIR/SystemRoot -- a font
    install is machine-wide, so nothing here may hard-code an invoking user's
    profile path."""
    monkeypatch.setenv("WINDIR", str(tmp_path))
    monkeypatch.delenv("SystemRoot", raising=False)
    assert generate._windows_fonts_dir() == tmp_path / "Fonts"


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="exercises the real, installed Windows Fonts directory",
)
def test_font_files_resolves_euro_face_without_unix_paths(monkeypatch, tmp_path):
    """With none of the Unix/macOS fallback paths present on disk, font_files()
    must still resolve a currency-capable TrueType pair by searching the real
    Windows Fonts directory. Read-only: nothing under it is copied, moved, or
    modified."""
    missing_unix_dir = tmp_path / "no-such-unix-fonts"
    monkeypatch.setattr(
        generate,
        "_platform_font_dirs",
        lambda: (missing_unix_dir, generate._windows_fonts_dir()),
    )

    regular, bold = generate.font_files(_euro_fixture())

    assert regular is not None and Path(regular).is_file()
    assert bold is not None and Path(bold).is_file()
    assert Path(regular).parent == generate._windows_fonts_dir()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only failure mode")
def test_font_files_raises_a_clear_error_when_nothing_covers_the_symbol(monkeypatch, tmp_path):
    """A directory with no glyph-capable face must fail loudly, not silently
    substitute a font lacking the required currency glyph."""
    monkeypatch.setattr(generate, "_platform_font_dirs", lambda: (tmp_path,))

    with pytest.raises(RuntimeError, match="TrueType"):
        generate.font_files(_euro_fixture())
