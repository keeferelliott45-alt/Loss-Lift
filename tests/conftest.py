"""Shared fixtures.  Golden PDFs are generated, never committed (spec section 9)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def golden_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build every synthetic fixture PDF once per test session."""
    from tests.golden.generate import build_all

    target = tmp_path_factory.mktemp("golden_pdfs")
    build_all(target)
    return target


@pytest.fixture()
def profiles_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the profile library at a throwaway directory."""
    from core import profiles

    target = tmp_path / "profiles"
    target.mkdir()
    monkeypatch.setattr(profiles, "PROFILE_DIR", target)
    return target
