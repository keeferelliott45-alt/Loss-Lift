"""Every qualification pack, listed once, by the lead (packs never edit this).

A pack is a module under ``tests/qualification/packs`` exporting
``build_cases(root) -> list[QualificationCase]`` (``tools.qualification.cases``).
"""

from __future__ import annotations

PACKS = (
    "tests.qualification.packs.digital_formats",
    "tests.qualification.packs.mixed_accounting",
)
