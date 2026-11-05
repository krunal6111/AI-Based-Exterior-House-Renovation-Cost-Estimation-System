"""Offline renovation visualisation (requirement 5.4).

The brief asks for the user's *actual house* redesigned, with the original
building structure preserved. That rules out re-generating the picture: this
renderer never invents geometry. It replaces the appearance of surfaces the
user marked, inside their own outlines, and drives the result with lighting
extracted from the photograph itself.

Pipeline per surface
--------------------
1. Rasterise the region outline, subtract its openings, feather the edge.
2. Pull the photograph's shading field -- shadow, grime, soft occlusion --
   which is independent of what the surface is made of.
3. Produce the new appearance:
   * flat colour, recoloured in LAB so shadows survive;
   * or a procedural texture drawn in rectified space at its real-world size
     and warped back through the surface's own perspective;
   * or a railing, drawn with an alpha channel so you see through it.
4. Multiply by the shading field and composite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import cv2
import numpy as np

from ..enums import RegionCategory
from . import geometry as geo
from . import textures as tex
from .catalog import Material
from .scene import shading_field

MM_PER_FOOT = 304.8

#: Rectified surfaces are drawn at this multiple of their on-screen size, so
#: that warping into perspective does not visibly soften the material.
SUPERSAMPLE = 1.6
MAX_RECT_EDGE = 3000


@dataclass
class RenderLayer:
    region_id: str
    polygon: Sequence[Sequence[float]]
    category: RegionCategory
    material: Material
    #: Real height of the surface in feet -- sets the texture's real scale.
    height_ft: float = 10.0
    params: dict[str, Any] = field(default_factory=dict)
    occluders: list[Sequence[Sequence[float]]] = field(default_factory=list)

    @property
    def colour(self) -> str | None:
        explicit = self.params.get("color")
        if explicit:
            return str(explicit)
        render = self.material.render or {}
        return render.get("base_color")


@dataclass
class RenderResult:
    image: np.ndarray
    engine: str
    layers_applied: int
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# masks
# ---------------------------------------------------------------------------


def _polygon_mask(
    polygon: Sequence[Sequence[float]],
    width: int,
    height: int,
    occluders: Sequence[Sequence[Sequence[float]]] = (),
    feather: float = 1.5,
) -> np.ndarray:
    """Anti-aliased 0..1 coverage mask for one region."""
    ss = 2  # supersample the rasterisation, then box-filter down
    mask = np.zeros((height * ss, width * ss), dtype=np.uint8)

    pts = (geo.to_pixels(polygon, width, height) * ss).astype(np.int32)
    cv2.fillPoly(mask, [pts], 255, lineType=cv2.LINE_AA)

    for occ in occluders:
        if len(occ) < 3:
            continue
        opts = (geo.to_pixels(occ, width, height) * ss).astype(np.int32)
        cv2.fillPoly(mask, [opts], 0, lineType=cv2.LINE_AA)

    small = cv2.resize(mask, (width, height), interpolation=cv2.INTER_AREA)
    alpha = small.astype(np.float32) / 255.0
    if feather > 0:
        alpha = cv2.GaussianBlur(alpha, (0, 0), feather)
    return np.clip(alpha, 0.0, 1.0)


def _region_illumination(lab_l: np.ndarray, alpha: np.ndarray) -> float:
    """How lit this surface is overall, relative to a well-lit wall.

    The shading field deliberately removes low-frequency lighting, so without
    this a wall standing in shade would come back fully sunlit.
    """
    weight = alpha.sum()
    if weight < 1e-6:
        return 1.0
    mean_l = float((lab_l * alpha).sum() / weight)
    return float(np.clip(mean_l / 150.0, 0.55, 1.25))


# ---------------------------------------------------------------------------
# appearance builders
# ---------------------------------------------------------------------------


def _solid_appearance(
    base_bgr: np.ndarray, alpha: np.ndarray, colour_hex: str, sheen: float
) -> np.ndarray:
    """Repaint a surface, keeping every shadow and streak in the photo."""
    lab = cv2.cvtColor(base_bgr.astype(np.uint8), cv2.COLOR_BGR2LAB).astype(np.float32)
    l_orig = lab[:, :, 0]

    swatch = tex.hex_to_bgr(colour_hex).reshape(1, 1, 3).astype(np.uint8)
    target = cv2.cvtColor(swatch, cv2.COLOR_BGR2LAB).astype(np.float32)[0, 0]

    weight = alpha.sum()
    mean_l = float((l_orig * alpha).sum() / weight) if weight > 1e-6 else 128.0
    mean_l = max(mean_l, 1.0)

    # Relative illumination, applied to the new albedo. gamma < 1 stops deep
    # shadows from crushing a light colour to black.
    ratio = np.power(np.clip(l_orig / mean_l, 0.15, 2.4), 0.9)
    l_new = np.clip(target[0] * ratio, 0, 255)

    if sheen:
        highlight = np.clip((ratio - 1.0), 0.0, 1.0) * (sheen * 22.0)
        l_new = np.clip(l_new + highlight, 0, 255)

    out = np.empty_like(lab)
    out[:, :, 0] = l_new
    out[:, :, 1] = target[1]
    out[:, :, 2] = target[2]
    return cv2.cvtColor(out.astype(np.uint8), cv2.COLOR_LAB2BGR).astype(np.float32)


def _rect_dimensions(quad: np.ndarray, aspect: float) -> tuple[int, int]:
    h_px = max(16.0, geo.quad_vertical_extent_px(quad)) * SUPERSAMPLE
    w_px = max(16.0, h_px * aspect)
    scale = min(1.0, MAX_RECT_EDGE / max(h_px, w_px))
    return (max(8, int(w_px * scale)), max(8, int(h_px * scale)))


def _warp_into_place(
    surface: np.ndarray, quad: np.ndarray, width: int, height: int
) -> np.ndarray:
    """Map a rectified drawing onto the surface's quad in the photograph."""
    sh, sw = surface.shape[:2]
    src = np.array([[0, 0], [sw, 0], [sw, sh], [0, sh]], dtype=np.float32)
    dst = geo.order_quad(quad).astype(np.float32)
    matrix = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(
        surface,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )


def _texture_appearance(
    layer: RenderLayer,
    quad: np.ndarray,
    width: int,
    height: int,
) -> np.ndarray:
    recipe = dict(layer.material.render or {})
    for key in ("tile_size_mm", "course_mm", "grout"):
        if key in layer.params and layer.params[key]:
            recipe[key] = layer.params[key]

    aspect, _ = geo.rectified_aspect_ratio(quad, width, height)
    rect_w, rect_h = _rect_dimensions(quad, aspect)

    height_ft = max(0.5, float(layer.height_ft or 10.0))
    px_per_mm = rect_h / (height_ft * MM_PER_FOOT)

    surface = tex.build_surface(
        recipe,
        rect_w,
        rect_h,
        px_per_mm,
        base_color=layer.colour,
        seed=abs(hash(layer.region_id)) % 9973,
    )
    return _warp_into_place(surface, quad, width, height)


# ---------------------------------------------------------------------------
# railings (drawn with transparency)
# ---------------------------------------------------------------------------


def _build_railing(
    recipe: dict, w: int, h: int, px_per_mm: float, colour_hex: str | None
) -> tuple[np.ndarray, np.ndarray]:
    """Rectified railing elevation -> (BGR float, alpha 0..1)."""
    style = recipe.get("style", "bar")
    colour = tex.hex_to_bgr(colour_hex or recipe.get("base_color"), "#808080")
    bgr = np.zeros((h, w, 3), dtype=np.float32)
    alpha = np.zeros((h, w), dtype=np.float32)

    def mm(v: float) -> int:
        return max(1, int(round(v * px_per_mm)))

    yy, xx = np.mgrid[0:h, 0:w]

    def band_y(y0: int, y1: int, col: np.ndarray, a: float = 1.0) -> None:
        y0, y1 = max(0, y0), min(h, y1)
        if y1 <= y0:
            return
        bgr[y0:y1] = col
        alpha[y0:y1] = a

    top_rail = mm(55)
    bottom_rail = mm(45)

    if style == "glass":
        panel_top = top_rail + mm(30)
        panel = (yy >= panel_top) & (yy < h - bottom_rail)
        glass_tint = colour * 1.0
        # Glass is mostly transparent, slightly more reflective at the top.
        grad = np.clip(0.45 - (yy - panel_top) / max(h, 1) * 0.22, 0.14, 0.5)
        bgr[panel] = glass_tint
        alpha[panel] = grad[panel].astype(np.float32)

        band_y(0, top_rail, colour * 1.12, 1.0)  # handrail
        # Spigots along the base every 800 mm.
        spacing = max(mm(800), 4)
        spig_w = mm(110)
        for x0 in range(mm(120), w, spacing):
            x1 = min(w, x0 + spig_w)
            bgr[h - bottom_rail - mm(140) : h, x0:x1] = colour * 0.78
            alpha[h - bottom_rail - mm(140) : h, x0:x1] = 1.0
        band_y(h - bottom_rail, h, colour * 0.72, 1.0)

    elif style == "post_rail":
        band_y(0, top_rail, colour * 1.1, 1.0)
        mid = h // 2
        band_y(mid - mm(18), mid + mm(18), colour, 1.0)
        band_y(h - bottom_rail, h, colour * 0.9, 1.0)
        spacing = max(mm(float(recipe.get("post_spacing_mm", 1200))), 6)
        post_w = mm(45)
        for x0 in range(0, w, spacing):
            x1 = min(w, x0 + post_w)
            bgr[:, x0:x1] = colour * 1.05
            alpha[:, x0:x1] = 1.0

    else:  # vertical bars
        band_y(0, top_rail, colour * 1.1, 1.0)
        band_y(h - bottom_rail, h, colour * 0.9, 1.0)
        spacing = max(mm(115), 3)
        bar_w = max(1, mm(22))
        for x0 in range(mm(20), w, spacing):
            x1 = min(w, x0 + bar_w)
            bgr[:, x0:x1] = colour
            alpha[:, x0:x1] = 1.0

    # A touch of vertical shading so the metal is not flat.
    shade = np.linspace(1.08, 0.9, h, dtype=np.float32)[:, None, None]
    bgr = np.clip(bgr * shade, 0, 255)
    return bgr, np.clip(alpha, 0.0, 1.0)


def _railing_appearance(
    layer: RenderLayer, quad: np.ndarray, width: int, height: int
) -> tuple[np.ndarray, np.ndarray]:
    recipe = dict(layer.material.render or {})
    aspect, _ = geo.rectified_aspect_ratio(quad, width, height)
    rect_w, rect_h = _rect_dimensions(quad, aspect)

    height_ft = max(0.5, float(layer.height_ft or 3.5))
    px_per_mm = rect_h / (height_ft * MM_PER_FOOT)

    surface, surf_alpha = _build_railing(recipe, rect_w, rect_h, px_per_mm, layer.colour)
    rgba = np.dstack([surface, surf_alpha * 255.0]).astype(np.float32)
    warped = _warp_into_place(rgba, quad, width, height)
    return warped[:, :, :3], np.clip(warped[:, :, 3] / 255.0, 0.0, 1.0)


# ---------------------------------------------------------------------------
# the renderer
# ---------------------------------------------------------------------------


def render(base_bgr: np.ndarray, layers: Sequence[RenderLayer]) -> RenderResult:
    height, width = base_bgr.shape[:2]
    result = base_bgr.astype(np.float32)
    warnings: list[str] = []
    applied = 0

    shading = shading_field(base_bgr)
    lab_l = cv2.cvtColor(base_bgr, cv2.COLOR_BGR2LAB)[:, :, 0].astype(np.float32)

    for layer in layers:
        if len(layer.polygon) < 3:
            warnings.append(f"{layer.region_id}: outline has fewer than 3 points.")
            continue
        render_cfg = layer.material.render or {}
        kind = render_cfg.get("kind")
        if not kind:
            continue

        alpha = _polygon_mask(layer.polygon, width, height, layer.occluders)
        if alpha.max() < 0.02:
            warnings.append(f"{layer.region_id}: outline covers no pixels.")
            continue

        illum = _region_illumination(lab_l, alpha)

        try:
            if kind == "solid":
                new = _solid_appearance(
                    base_bgr, alpha, layer.colour or "#CCCCCC",
                    float(render_cfg.get("sheen", 0.0)),
                )
                new = new * shading[:, :, None]
                effective = alpha

            elif kind == "railing":
                pts = geo.to_pixels(layer.polygon, width, height)
                quad = geo.min_area_quad(pts)
                new, rail_alpha = _railing_appearance(layer, quad, width, height)
                new = new * (shading[:, :, None] * 0.35 + 0.65) * illum
                effective = alpha * rail_alpha

            else:  # texture
                pts = geo.to_pixels(layer.polygon, width, height)
                quad = geo.min_area_quad(pts)
                new = _texture_appearance(layer, quad, width, height)
                new = new * shading[:, :, None] * illum
                effective = alpha

        except Exception as exc:  # pragma: no cover - keep one bad region local
            warnings.append(f"{layer.region_id}: could not render ({exc}).")
            continue

        a3 = effective[:, :, None]
        result = result * (1.0 - a3) + np.clip(new, 0, 255) * a3
        applied += 1

    return RenderResult(
        image=np.clip(result, 0, 255).astype(np.uint8),
        engine="offline-cv",
        layers_applied=applied,
        warnings=warnings,
    )
