"""Project-level orchestration: measure -> quantify -> cost -> render layers.

Keeps the routers thin and gives the PDF report and the API exactly the same
numbers, computed once in one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from ..enums import AreaMethod, MeasureMode, RegionCategory
from ..models import Project, Region
from . import geometry as geo
from .catalog import Catalog, default_cost_settings, load_catalog
from .costing import CostEstimate, blended_error_band, compute_cost
from .quantities import QuantityLine, compute_quantities
from .renderer import RenderLayer
from .scale import RegionMeasurement, ScaleSolution, fallback_scale, measure_region


@dataclass
class ProjectMeasurements:
    scale: ScaleSolution | None
    by_region: dict[str, RegionMeasurement] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def area_for(self, region_id: str) -> float:
        m = self.by_region.get(region_id)
        return m.area_sqft if m else 0.0

    def length_for(self, region_id: str) -> float:
        m = self.by_region.get(region_id)
        return m.length_ft if m else 0.0


def overlapping_openings(
    project: Project, wall: Region, overlap_threshold: float = 0.25
) -> list[Region]:
    """Windows/doors/gates that should be cut out of this wall.

    An explicit `parent_id` link is always honoured. But requiring a user to
    discover the "cut out of which surface?" dropdown before an opening is
    excluded from a wall's area or its render is asking too much -- in
    practice almost nobody sets it, which is exactly how a repaint ends up
    painted straight over a window. So any opening-category region that
    geometrically overlaps this wall by a large enough share of its own area
    is treated as cut out of it, whether or not anyone linked it by hand.
    """
    if len(wall.polygon) < 3:
        return []

    from shapely.geometry import Polygon as ShapelyPolygon
    from shapely.validation import make_valid

    wall_shape = make_valid(ShapelyPolygon(wall.polygon))
    if wall_shape.is_empty or wall_shape.area <= 0:
        return []

    found: dict[str, Region] = {}
    for other in project.regions:
        if other.id == wall.id:
            continue
        if other.parent_id == wall.id:
            found[other.id] = other
            continue
        if not other.category_enum.is_opening or len(other.polygon) < 3:
            continue

        shape = make_valid(ShapelyPolygon(other.polygon))
        if shape.is_empty or shape.area <= 0:
            continue
        try:
            overlap = shape.intersection(wall_shape).area / shape.area
        except Exception:  # pragma: no cover - shapely robustness guard
            continue
        if overlap >= overlap_threshold:
            found[other.id] = other

    return list(found.values())


def resolve_scale(
    project: Project,
    image_width: int,
    image_height: int,
    building_box: tuple[int, int, int, int] | None = None,
) -> tuple[ScaleSolution | None, list[str]]:
    """Calibration if the user set one, otherwise the Tier C assumption."""
    from .scale import solve_scale

    warnings: list[str] = []
    cal = project.calibration
    if cal and cal.ref_line and cal.ref_real_ft:
        scale = solve_scale(
            cal.ref_line,
            cal.ref_real_ft,
            image_width,
            image_height,
            ref_kind=cal.ref_kind,
            confirmed=bool(cal.confirmed),
        )
        if scale:
            if not cal.confirmed:
                scale.method = AreaMethod.PIXEL_RATIO
                scale.confidence = 0.35
            return scale, warnings

    warnings.append(
        "No reference object set. Areas use an assumed building height and "
        "should be treated as rough. Set a reference using \"Set scale\" on "
        "the Surfaces step."
    )
    return (
        fallback_scale(building_box, image_width, image_height),
        warnings,
    )


def measure_project(
    project: Project,
    image_width: int,
    image_height: int,
    building_box: tuple[int, int, int, int] | None = None,
) -> ProjectMeasurements:
    scale, warnings = resolve_scale(project, image_width, image_height, building_box)
    result = ProjectMeasurements(scale=scale, warnings=list(warnings))

    for region in project.regions:
        category = region.category_enum
        kids = overlapping_openings(project, region)

        retained = 1.0
        if kids and category.measure is MeasureMode.AREA and len(region.polygon) >= 3:
            child_polys = [k.polygon for k in kids if len(k.polygon) >= 3]
            if child_polys:
                _, retained = geo.subtract_children(region.polygon, child_polys)

        measurement = measure_region(
            region.polygon,
            category,
            image_width,
            image_height,
            scale,
            override_width_ft=region.override_width_ft,
            override_height_ft=region.override_height_ft,
            override_area_sqft=region.override_area_sqft,
            retained_fraction=retained,
        )
        result.by_region[region.id] = measurement

    return result


# ---------------------------------------------------------------------------
# assignments -> estimate
# ---------------------------------------------------------------------------


def active_variant(project: Project):
    for v in project.variants:
        if v.is_active:
            return v
    return project.variants[0] if project.variants else None


def assignment_rows(
    project: Project, measurements: ProjectMeasurements, catalog: Catalog
) -> list[dict[str, Any]]:
    variant = active_variant(project)
    if variant is None:
        return []

    regions = {r.id: r for r in project.regions}
    rows: list[dict[str, Any]] = []

    for a in variant.assignments:
        region = regions.get(a.region_id)
        if region is None:
            continue
        material = catalog.get(a.material_id)
        if material is None:
            continue
        m = measurements.by_region.get(region.id)
        if m is None:
            continue
        rows.append(
            {
                "material_id": a.material_id,
                "region_id": region.id,
                "region_name": region.name or region.category_enum.label,
                "area_sqft": m.area_sqft,
                "length_ft": m.length_ft,
                "coats": a.params.get("coats"),
            }
        )
    return rows


def rate_override_map(project: Project) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for o in project.rate_overrides:
        entry: dict[str, Any] = {}
        if o.material_rate is not None:
            entry["material_rate"] = o.material_rate
        if o.labour_rate is not None:
            entry["labour_rate"] = o.labour_rate
        if o.wastage_pct is not None:
            entry["wastage_pct"] = o.wastage_pct
        if entry:
            out[o.material_id] = entry
    return out


def build_estimate(
    project: Project,
    measurements: ProjectMeasurements,
    catalog: Catalog | None = None,
) -> tuple[list[QuantityLine], CostEstimate, list[str]]:
    catalog = catalog or load_catalog()
    rows = assignment_rows(project, measurements, catalog)
    overrides = rate_override_map(project)
    warnings = list(measurements.warnings)

    wastage_overrides = {
        mid: o["wastage_pct"] for mid, o in overrides.items() if "wastage_pct" in o
    }
    quantities = compute_quantities(rows, catalog, wastage_overrides)

    # Weight each region's error band by how much surface it contributes.
    bands: list[tuple[float, float, float]] = []
    for row in rows:
        m = measurements.by_region.get(row["region_id"])
        if not m:
            continue
        weight = m.area_sqft or m.length_ft
        if weight > 0:
            bands.append((weight, m.error_low, m.error_high))
    band = blended_error_band(bands)

    settings = dict(default_cost_settings())
    settings.update(project.cost_settings or {})

    estimate = compute_cost(
        quantities, catalog, overrides, settings, area_error_band=band
    )

    if not rows:
        warnings.append(
            "No materials assigned yet - pick materials on the Design step to "
            "see a cost."
        )
    return quantities, estimate, warnings


# ---------------------------------------------------------------------------
# render layers
# ---------------------------------------------------------------------------


def render_layers(
    project: Project, measurements: ProjectMeasurements, catalog: Catalog | None = None
) -> list[RenderLayer]:
    catalog = catalog or load_catalog()
    variant = active_variant(project)
    if variant is None:
        return []

    regions = {r.id: r for r in project.regions}
    assigned_ids = {a.region_id for a in variant.assignments}
    layers: list[RenderLayer] = []

    for a in variant.assignments:
        region = regions.get(a.region_id)
        material = catalog.get(a.material_id)
        if region is None or material is None or not material.render:
            continue

        m = measurements.by_region.get(region.id)
        height_ft = 10.0
        if m:
            if m.height_ft:
                height_ft = m.height_ft
            elif m.meta.get("height_ft"):
                height_ft = float(m.meta["height_ft"])

        # Openings are only cut out when they are not themselves being
        # re-clad -- a door getting a new finish must still be painted. Found
        # by geometric overlap, not just an explicit parent link, so a window
        # is excluded from a wall's render whether or not anyone linked it.
        occluders = [
            k.polygon
            for k in overlapping_openings(project, region)
            if len(k.polygon) >= 3 and k.id not in assigned_ids
        ]

        layers.append(
            RenderLayer(
                region_id=region.id,
                polygon=region.polygon,
                category=region.category_enum,
                material=material,
                height_ft=height_ft,
                params=dict(a.params or {}),
                occluders=occluders,
            )
        )

    # Paint walls before the things that sit on them.
    def depth(layer: RenderLayer) -> int:
        if layer.category is RegionCategory.WALL:
            return 0
        if layer.category.is_opening:
            return 2
        return 1

    layers.sort(key=depth)
    return layers
