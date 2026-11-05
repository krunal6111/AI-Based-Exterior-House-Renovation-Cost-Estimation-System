"""Quantity and cost maths -- the numbers a homeowner would budget against."""

from __future__ import annotations

import pytest

from app.services.catalog import load_catalog
from app.services.costing import blended_error_band, compute_cost
from app.services.quantities import compute_quantities, solve_packs


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


# --------------------------------------------------------------- packs ----


def test_packs_prefer_a_tight_top_up():
    packs = {p.size: p.count for p in solve_packs(33.3, [20, 10, 4, 1])}
    assert packs == {20: 1, 10: 1, 4: 1}
    assert sum(s * c for s, c in packs.items()) == 34


def test_packs_avoid_buying_a_second_large_tin_for_a_litre():
    """21 L should be 20+1, never 2x20."""
    packs = {p.size: p.count for p in solve_packs(21.0, [20, 10, 4, 1])}
    assert packs == {20: 1, 1: 1}


def test_packs_exact_fit():
    packs = {p.size: p.count for p in solve_packs(20.0, [20, 10, 4, 1])}
    assert packs == {20: 1}


def test_packs_always_cover_the_requirement():
    for need in (0.4, 3.1, 9.9, 17.5, 44.2, 101.0):
        total = sum(p.total for p in solve_packs(need, [20, 10, 4, 1]))
        assert total >= need


def test_no_packs_for_zero():
    assert solve_packs(0, [20, 10, 4, 1]) == []


# ---------------------------------------------------------- quantities ----


def test_paint_litres_from_coverage(catalog):
    """1000 sqft, 2 coats at 120 sqft/L/coat, +5% wastage = 17.5 L."""
    lines = compute_quantities(
        [{"material_id": "paint_premium", "area_sqft": 1000.0, "length_ft": 0.0,
          "region_name": "Front wall"}],
        catalog,
    )
    paint = next(ln for ln in lines if ln.material_id == "paint_premium")
    assert paint.unit == "litre"
    assert paint.quantity == pytest.approx(17.5, rel=1e-3)
    assert "1,000 sqft" in paint.formula

    # Packed as 10 + 4 + 4 = 18 L rather than a single 20 L tin: the solver
    # minimises litres bought, so 2.5 L of paint is not left on the shelf.
    assert paint.purchase_quantity == 18.0
    assert {p.size: p.count for p in paint.packs} == {10: 1, 4: 2}


def test_tile_pieces_and_boxes(catalog):
    """200 sqft of 600x300 mm tile, +10% wastage."""
    lines = compute_quantities(
        [{"material_id": "tile_elevation", "area_sqft": 200.0, "length_ft": 0.0,
          "region_name": "Feature"}],
        catalog,
    )
    tile = next(ln for ln in lines if ln.material_id == "tile_elevation")
    assert tile.quantity == pytest.approx(220.0)
    # 600x300 mm = 1.938 sqft -> ceil(220 / 1.938) = 114 pieces, 23 boxes of 5
    assert tile.pieces == 114
    assert tile.boxes == 23


def test_railing_measured_in_running_feet(catalog):
    lines = compute_quantities(
        [{"material_id": "railing_glass", "area_sqft": 0.0, "length_ft": 24.0,
          "region_name": "Balcony"}],
        catalog,
    )
    rail = next(ln for ln in lines if ln.material_id == "railing_glass")
    assert rail.unit == "rft"
    assert rail.quantity == pytest.approx(25.2)  # 24 x 1.05


def test_consumables_are_derived_from_the_finish(catalog):
    lines = compute_quantities(
        [{"material_id": "paint_premium", "area_sqft": 1000.0, "length_ft": 0.0,
          "region_name": "Wall"}],
        catalog,
    )
    ids = {ln.material_id for ln in lines}
    assert {"primer_exterior", "putty_exterior"} <= ids
    assert all(ln.derived for ln in lines if ln.material_id != "paint_premium")


def test_unknown_material_is_ignored(catalog):
    lines = compute_quantities(
        [{"material_id": "does_not_exist", "area_sqft": 100.0, "length_ft": 0,
          "region_name": "x"}],
        catalog,
    )
    assert lines == []


def test_zero_area_produces_no_line(catalog):
    lines = compute_quantities(
        [{"material_id": "paint_premium", "area_sqft": 0.0, "length_ft": 0.0,
          "region_name": "x"}],
        catalog,
    )
    assert lines == []


# -------------------------------------------------------------- costing ----


def test_cost_is_material_plus_labour(catalog):
    """Cladding: 100 sqft at the catalog defaults, no add-ons."""
    lines = compute_quantities(
        [{"material_id": "cladding_sandstone", "area_sqft": 100.0, "length_ft": 0,
          "region_name": "Pillar"}],
        catalog,
    )
    estimate = compute_cost(lines, catalog, cost_settings={})

    material = catalog.require("cladding_sandstone")
    # 100 sqft + 10% wastage = 110 sqft purchased; labour on the measured 100.
    expected_material = 110.0 * material.material_rate.default
    expected_labour = 100.0 * material.labour_rate.default

    line = estimate.lines[0]
    assert line.material_cost == pytest.approx(expected_material)
    assert line.labour_cost == pytest.approx(expected_labour)
    assert estimate.subtotal == pytest.approx(expected_material + expected_labour)
    assert estimate.grand_total == pytest.approx(estimate.subtotal)


def test_rate_override_changes_the_total(catalog):
    lines = compute_quantities(
        [{"material_id": "cladding_sandstone", "area_sqft": 100.0, "length_ft": 0,
          "region_name": "Pillar"}],
        catalog,
    )
    base = compute_cost(lines, catalog, cost_settings={})
    tweaked = compute_cost(
        lines,
        catalog,
        overrides={"cladding_sandstone": {"material_rate": 100.0, "labour_rate": 50.0}},
        cost_settings={},
    )
    assert tweaked.lines[0].material_cost == pytest.approx(110.0 * 100.0)
    assert tweaked.lines[0].labour_cost == pytest.approx(100.0 * 50.0)
    assert tweaked.lines[0].overridden is True
    assert tweaked.grand_total != base.grand_total


def test_addons_apply_in_documented_order(catalog):
    lines = compute_quantities(
        [{"material_id": "paint_premium", "area_sqft": 1000.0, "length_ft": 0,
          "region_name": "Wall"}],
        catalog,
    )
    settings = {
        "scaffolding_enabled": True,
        "scaffolding_rate_per_sqft": 10.0,
        "transport_lumpsum": 1000.0,
        "contractor_overhead_pct": 10.0,
        "contingency_pct": 0.0,
        "gst_enabled": True,
        "gst_pct": 18.0,
    }
    estimate = compute_cost(lines, catalog, cost_settings=settings)

    running = estimate.subtotal
    running += 1000.0 * 10.0   # scaffolding on the measured area only
    running += 1000.0          # transport
    running *= 1.10            # overhead
    running *= 1.18            # GST last

    assert estimate.grand_total == pytest.approx(running, rel=1e-6)
    assert [a.key for a in estimate.addons] == [
        "scaffolding", "transport", "overhead", "gst",
    ]


def test_scaffolding_counts_each_surface_once(catalog):
    """Derived consumables must not inflate the scaffolding area."""
    lines = compute_quantities(
        [{"material_id": "paint_premium", "area_sqft": 500.0, "length_ft": 0,
          "region_name": "Wall"}],
        catalog,
    )
    estimate = compute_cost(
        lines,
        catalog,
        cost_settings={"scaffolding_enabled": True, "scaffolding_rate_per_sqft": 8.0},
    )
    assert estimate.total_area_sqft == pytest.approx(500.0)
    scaffolding = next(a for a in estimate.addons if a.key == "scaffolding")
    assert scaffolding.amount == pytest.approx(4000.0)


def test_gst_can_be_switched_off(catalog):
    lines = compute_quantities(
        [{"material_id": "paint_premium", "area_sqft": 100.0, "length_ft": 0,
          "region_name": "Wall"}],
        catalog,
    )
    estimate = compute_cost(lines, catalog, cost_settings={"gst_enabled": False})
    assert all(a.key != "gst" for a in estimate.addons)


def test_error_band_is_area_weighted():
    """A large well-measured wall should dominate a small guessed one."""
    low, high = blended_error_band([(900, 0.05, 0.08), (100, 0.25, 0.40)])
    assert low == pytest.approx(0.07, abs=0.005)
    assert high == pytest.approx(0.112, abs=0.005)


def test_error_band_defaults_to_pessimistic_when_empty():
    assert blended_error_band([]) == (0.25, 0.40)


def test_range_brackets_the_total(catalog):
    lines = compute_quantities(
        [{"material_id": "paint_premium", "area_sqft": 400.0, "length_ft": 0,
          "region_name": "Wall"}],
        catalog,
    )
    estimate = compute_cost(lines, catalog, area_error_band=(0.12, 0.20))
    assert estimate.range_low < estimate.grand_total < estimate.range_high
    assert estimate.range_low == pytest.approx(estimate.grand_total * 0.88, rel=1e-6)
    assert estimate.range_high == pytest.approx(estimate.grand_total * 1.20, rel=1e-6)


# -------------------------------------------------------------- catalog ----


def test_every_material_has_a_source_note(catalog):
    for material in catalog.materials.values():
        assert material.source_note, f"{material.id} has no rate provenance"


def test_rate_defaults_sit_inside_their_band(catalog):
    for material in catalog.materials.values():
        for band in (material.material_rate, material.labour_rate):
            assert band.min <= band.default <= band.max, material.id


def test_consumables_are_not_user_assignable(catalog):
    for material in catalog.materials.values():
        if material.kind.value == "consumable":
            assert not material.assignable
