"""The roof covering is a major surface and must be a selectable, renderable
category -- it was previously invisible to detection and had no catalog
material at all."""

from __future__ import annotations

import pytest

from app.enums import RegionCategory
from app.services.catalog import load_catalog
from app.services.textures import AVAILABLE_TEXTURES, build_surface


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


def test_roof_is_its_own_area_category_not_an_opening():
    assert RegionCategory.ROOF.measure.value == "area"
    assert RegionCategory.ROOF.is_opening is False
    assert RegionCategory.ROOF.label == "Roof covering"


def test_catalog_has_roof_materials(catalog):
    roof_materials = [m for m in catalog.assignable() if "roof" in [c.value for c in m.applies_to]]
    assert len(roof_materials) >= 3
    ids = {m.id for m in roof_materials}
    assert {"roof_tile_clay", "roof_metal_sheet", "roof_shingle_asphalt"} <= ids


def test_shingle_texture_is_registered_and_renders():
    assert "shingle" in AVAILABLE_TEXTURES
    out = build_surface(
        {"texture": "shingle", "base_color": "#4A4038", "course_mm": [1000, 140]},
        width=200,
        height=150,
        px_per_mm=0.3,
    )
    assert out.shape == (150, 200, 3)
    assert out.min() >= 0 and out.max() <= 255
