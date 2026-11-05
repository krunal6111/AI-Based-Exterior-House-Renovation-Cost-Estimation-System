"""Surface area estimation (requirement 5.5).

Turning one photograph into square feet without a tape measure or a drawing
means leaning on assumptions. The design rule here is that the system never
hides which assumption it used: every measurement carries its method and the
error band that method honestly deserves.

Three tiers, best first:

* **Tier A** -- the user typed a real dimension for this surface.
* **Tier B** -- scaled from a reference object of known size (a door, a floor
  height), with perspective removed by rectifying the surface's own quad.
* **Tier C** -- no reference at all; the building's height is assumed. Loud
  warning, wide band.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

from ..enums import AreaMethod, MeasureMode, RegionCategory
from . import geometry as geo

#: Used only by Tier C, when the user has confirmed no reference whatsoever.
#: Two storeys plus parapet for a typical low-rise Indian house.
FALLBACK_BUILDING_HEIGHT_FT = 20.0


@dataclass
class ScaleSolution:
    """Pixels -> feet at the reference object's depth."""

    ft_per_px: float
    method: AreaMethod
    confidence: float
    note: str
    ref_kind: str = ""
    ref_real_ft: float = 0.0
    ref_px: float = 0.0

    @property
    def valid(self) -> bool:
        return self.ft_per_px > 0 and np.isfinite(self.ft_per_px)


@dataclass
class RegionMeasurement:
    area_sqft: float = 0.0
    length_ft: float = 0.0
    width_ft: float = 0.0
    height_ft: float = 0.0
    aspect_ratio: float = 1.0
    fill_fraction: float = 1.0
    method: AreaMethod = AreaMethod.PIXEL_RATIO
    confidence: float = 0.3
    error_low: float = 0.25
    error_high: float = 0.40
    #: Feet per pixel on this surface, which the renderer needs to draw
    #: textures at their real size.
    ft_per_px: float = 0.0
    notes: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def measure(self) -> float:
        return self.length_ft if self.length_ft else self.area_sqft


# ---------------------------------------------------------------------------
# calibration
# ---------------------------------------------------------------------------


def solve_scale(
    ref_line: Sequence[Sequence[float]] | None,
    ref_real_ft: float,
    image_width: int,
    image_height: int,
    ref_kind: str = "door",
    confirmed: bool = False,
) -> ScaleSolution | None:
    """Build a scale from a two-point reference the user dragged on the photo."""
    if not ref_line or len(ref_line) < 2 or ref_real_ft <= 0:
        return None

    pts = geo.to_pixels(ref_line[:2], image_width, image_height)
    px = float(np.linalg.norm(pts[1] - pts[0]))
    if px < 4.0:
        return None

    ft_per_px = ref_real_ft / px
    return ScaleSolution(
        ft_per_px=ft_per_px,
        method=AreaMethod.REFERENCE_OBJECT,
        confidence=0.75 if confirmed else 0.5,
        note=(
            f"{ref_real_ft:g} ft reference measured {px:.0f} px on the photo "
            f"({ft_per_px * 12:.2f} inches per pixel)."
        ),
        ref_kind=ref_kind,
        ref_real_ft=ref_real_ft,
        ref_px=px,
    )


def fallback_scale(
    building_box: tuple[int, int, int, int] | None,
    image_width: int,
    image_height: int,
) -> ScaleSolution:
    """Tier C: assume the detected building is a standard two-storey height."""
    if building_box:
        px = max(1.0, float(building_box[3] - building_box[1]))
    else:
        px = max(1.0, image_height * 0.7)
    return ScaleSolution(
        ft_per_px=FALLBACK_BUILDING_HEIGHT_FT / px,
        method=AreaMethod.PIXEL_RATIO,
        confidence=0.25,
        note=(
            f"No reference confirmed. Assumed the building is "
            f"{FALLBACK_BUILDING_HEIGHT_FT:g} ft tall. Confirm a door or floor "
            f"height to tighten this considerably."
        ),
        ref_kind="assumed_building_height",
        ref_real_ft=FALLBACK_BUILDING_HEIGHT_FT,
        ref_px=px,
    )


# ---------------------------------------------------------------------------
# per-region measurement
# ---------------------------------------------------------------------------


def measure_region(
    polygon: Sequence[Sequence[float]],
    category: RegionCategory,
    image_width: int,
    image_height: int,
    scale: ScaleSolution | None,
    *,
    override_width_ft: float | None = None,
    override_height_ft: float | None = None,
    override_area_sqft: float | None = None,
    retained_fraction: float = 1.0,
) -> RegionMeasurement:
    """Measure one region, in square feet or running feet as appropriate."""
    notes: list[str] = []

    if len(polygon) < 2:
        return RegionMeasurement(notes=["Region has too few points to measure."])

    pts = geo.to_pixels(polygon, image_width, image_height)
    ft_per_px = scale.ft_per_px if (scale and scale.valid) else 0.0

    # --- linear items ---------------------------------------------------
    if category.measure is MeasureMode.LENGTH:
        if override_width_ft:
            lo, hi = AreaMethod.USER_DIMENSION.error_band
            return RegionMeasurement(
                length_ft=float(override_width_ft),
                method=AreaMethod.USER_DIMENSION,
                confidence=0.95,
                error_low=lo,
                error_high=hi,
                ft_per_px=ft_per_px,
                notes=["Length entered by you."],
            )
        if ft_per_px <= 0:
            return RegionMeasurement(
                notes=["No scale available - confirm a reference object."]
            )

        method = scale.method if scale else AreaMethod.PIXEL_RATIO
        conf = scale.confidence if scale else 0.25

        if len(polygon) >= 3:
            # A railing drawn as a region: rectify it and take its true width.
            # This beats measuring the drawn polyline, which is foreshortened.
            quad = geo.min_area_quad(pts)
            aspect, aspect_conf = geo.rectified_aspect_ratio(
                quad, image_width, image_height
            )
            height_ft = (
                float(override_height_ft)
                if override_height_ft
                else geo.quad_vertical_extent_px(quad) * ft_per_px
            )
            length_ft = height_ft * aspect
            conf = min(conf, aspect_conf)
            notes.append(
                f"Run length taken across the rectified surface "
                f"({aspect:.2f} : 1 proportions, {height_ft:.1f} ft high)."
            )
            meta = {
                "quad": [[round(float(x), 1), round(float(y), 1)] for x, y in quad],
                "aspect_confidence": round(aspect_conf, 2),
                "height_ft": round(height_ft, 2),
            }
        else:
            length_px = geo.polyline_length_px(pts, closed=False)
            length_ft = length_px * ft_per_px
            notes.append(
                "Measured along the drawn line. Parts of the run much nearer "
                "or further than the reference will be off - draw the railing "
                "as a region instead for a perspective-corrected length."
            )
            meta = {"length_px": round(length_px, 1)}

        lo, hi = method.error_band
        return RegionMeasurement(
            length_ft=round(length_ft, 2),
            method=method,
            confidence=round(conf, 2),
            error_low=lo,
            error_high=hi,
            ft_per_px=ft_per_px,
            notes=notes,
            meta=meta,
        )

    # --- area items -------------------------------------------------------
    if len(polygon) < 3:
        return RegionMeasurement(notes=["An area needs at least three points."])

    quad = geo.min_area_quad(pts)
    aspect, aspect_conf = geo.rectified_aspect_ratio(quad, image_width, image_height)
    h_px = geo.quad_vertical_extent_px(quad)
    fill = geo.fill_fraction(pts, quad)

    # Height drives everything: vertical edges stay vertical in a level photo,
    # so they are the dimension a single reference can scale most reliably.
    if override_height_ft:
        height_ft = float(override_height_ft)
        height_from_user = True
    elif ft_per_px > 0:
        height_ft = h_px * ft_per_px
        height_from_user = False
    else:
        return RegionMeasurement(
            notes=["No scale available - confirm a reference object."]
        )

    if override_width_ft:
        width_ft = float(override_width_ft)
        width_from_user = True
    else:
        width_ft = height_ft * aspect
        width_from_user = False

    if height_from_user or width_from_user:
        method, conf = AreaMethod.USER_DIMENSION, 0.95
        if height_from_user and not width_from_user:
            notes.append(
                "Width derived from your height using the perspective-corrected "
                f"proportions of this surface ({aspect:.2f} : 1)."
            )
    else:
        method = scale.method if scale else AreaMethod.PIXEL_RATIO
        conf = min(scale.confidence if scale else 0.25, aspect_conf)
        if aspect_conf < 0.7:
            notes.append(
                "This surface is close to square-on, so its real proportions "
                "had to be inferred using a typical camera lens."
            )

    area = width_ft * height_ft * fill * max(0.0, min(1.0, retained_fraction))

    if override_area_sqft:
        area = float(override_area_sqft)
        method, conf = AreaMethod.USER_DIMENSION, 1.0
        notes.append("Area entered directly by you; overrides all estimation.")

    if retained_fraction < 0.999:
        notes.append(
            f"{(1 - retained_fraction) * 100:.0f}% deducted for windows, doors "
            "and other openings on this surface."
        )
    if fill < 0.95:
        notes.append(
            f"Shape covers {fill * 100:.0f}% of its bounding rectangle; the "
            "area is scaled down accordingly."
        )

    lo, hi = method.error_band
    return RegionMeasurement(
        area_sqft=round(area, 2),
        width_ft=round(width_ft, 2),
        height_ft=round(height_ft, 2),
        aspect_ratio=round(aspect, 3),
        fill_fraction=round(fill, 3),
        method=method,
        confidence=round(conf, 2),
        error_low=lo,
        error_high=hi,
        ft_per_px=ft_per_px,
        notes=notes,
        meta={
            "quad": [[round(float(x), 1), round(float(y), 1)] for x, y in quad],
            "height_px": round(h_px, 1),
            "aspect_confidence": round(aspect_conf, 2),
            "retained_fraction": round(retained_fraction, 3),
        },
    )
