"""Procedural material surfaces, generated in rectified (fronto-parallel) space.

Nothing here is a bitmap asset: every surface is synthesised with numpy at the
real-world scale the estimator computed, so a 600x300 mm tile is drawn 600x300
mm and a stone course is drawn at its real course height. Generating the whole
rectified plane in one pass also sidesteps seam artefacts entirely -- there is
no tiling, just one correctly-sized drawing that then gets warped into the
photograph's perspective.

All functions return float32 BGR in 0..255.
"""

from __future__ import annotations

import cv2
import numpy as np

MM_PER_FOOT = 304.8


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def hex_to_bgr(value: str | None, fallback: str = "#CCCCCC") -> np.ndarray:
    raw = (value or fallback).strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(c * 2 for c in raw)
    if len(raw) != 6:
        raw = fallback.lstrip("#")
    r, g, b = (int(raw[i : i + 2], 16) for i in (0, 2, 4))
    return np.array([b, g, r], dtype=np.float32)


def _hash01(values: np.ndarray, salt: float = 1.0) -> np.ndarray:
    """Deterministic per-cell pseudo-random in 0..1, fully vectorised."""
    x = np.sin(values.astype(np.float64) * 12.9898 * salt + 78.233 * salt) * 43758.5453
    return (x - np.floor(x)).astype(np.float32)


def fbm(h: int, w: int, octaves: int = 5, base: int = 4, seed: int = 0) -> np.ndarray:
    """Fractal value noise in 0..1 via successively upsampled random grids."""
    rng = np.random.default_rng(seed)
    out = np.zeros((h, w), dtype=np.float32)
    amplitude = 1.0
    total = 0.0
    for o in range(octaves):
        cells_y = max(2, base * (2**o))
        cells_x = max(2, int(base * (2**o) * w / max(h, 1)))
        cells_x = max(2, min(cells_x, 1024))
        grid = rng.random((cells_y, cells_x)).astype(np.float32)
        layer = cv2.resize(grid, (w, h), interpolation=cv2.INTER_CUBIC)
        out += amplitude * layer
        total += amplitude
        amplitude *= 0.5
    return np.clip(out / max(total, 1e-6), 0.0, 1.0)


def _relief_shade(height: np.ndarray, strength: float) -> np.ndarray:
    """Fake directional lighting from a height map (light from upper-left)."""
    if strength <= 0:
        return np.ones_like(height)
    gy, gx = np.gradient(height.astype(np.float32))
    shade = 1.0 + strength * (-gx * 0.7 - gy * 0.7) * 12.0
    return np.clip(shade, 0.55, 1.5).astype(np.float32)


def _unit_grid(
    h: int, w: int, cell_w: float, cell_h: float, stagger: bool
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Course layout -> (unit id, fractional x within unit, fractional y)."""
    cell_w = max(2.0, float(cell_w))
    cell_h = max(2.0, float(cell_h))
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)

    row = np.floor(yy / cell_h)
    offset = (np.mod(row, 2.0) * 0.5 * cell_w) if stagger else 0.0
    shifted = xx + offset
    col = np.floor(shifted / cell_w)

    unit = row * 7919.0 + col
    fx = (shifted / cell_w) - col
    fy = (yy / cell_h) - row
    return unit, fx, fy


def _apply_grout(
    colour: np.ndarray,
    fx: np.ndarray,
    fy: np.ndarray,
    cell_w: float,
    cell_h: float,
    grout_px: float,
    grout_bgr: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Paint joint lines; also return a 0..1 'inset' map used for bevelling."""
    if grout_px <= 0.3:
        return colour, np.ones(fx.shape, dtype=np.float32)

    dist_x = np.minimum(fx * cell_w, (1.0 - fx) * cell_w)
    dist_y = np.minimum(fy * cell_h, (1.0 - fy) * cell_h)
    dist = np.minimum(dist_x, dist_y)

    joint = np.clip((grout_px - dist) / max(grout_px, 1e-3), 0.0, 1.0)
    joint = joint[:, :, None]
    out = colour * (1.0 - joint) + grout_bgr[None, None, :] * joint

    inset = np.clip(dist / max(grout_px * 2.2, 1e-3), 0.0, 1.0)
    return out, inset.astype(np.float32)


# ---------------------------------------------------------------------------
# the surfaces
# ---------------------------------------------------------------------------


def build_surface(
    recipe: dict,
    width: int,
    height: int,
    px_per_mm: float,
    base_color: str | None = None,
    seed: int = 7,
) -> np.ndarray:
    """Draw a material across a rectified rectangle of ``width`` x ``height`` px."""
    width = max(8, int(width))
    height = max(8, int(height))
    kind = (recipe or {}).get("texture", "plaster")
    base = hex_to_bgr(base_color or (recipe or {}).get("base_color"), "#CCCCCC")
    relief = float((recipe or {}).get("relief", 0.3))
    course = (recipe or {}).get("course_mm") or [600, 300]
    grout_cfg = (recipe or {}).get("grout") or {}
    grout_bgr = hex_to_bgr(grout_cfg.get("color"), "#999999")
    grout_px = float(grout_cfg.get("width_mm", 0)) * px_per_mm

    cell_w = max(3.0, float(course[0]) * px_per_mm)
    cell_h = max(3.0, float(course[1]) * px_per_mm)

    builder = _BUILDERS.get(kind, _plaster)
    return builder(
        width, height, base, cell_w, cell_h, grout_px, grout_bgr, relief,
        px_per_mm, seed,
    )


def _finish(colour: np.ndarray, shade: np.ndarray) -> np.ndarray:
    return np.clip(colour * shade[:, :, None], 0, 255).astype(np.float32)


def _plaster(w, h, base, cw, ch, gpx, gcol, relief, ppm, seed):  # noqa: ANN001
    grain = fbm(h, w, octaves=6, base=6, seed=seed)
    fine = fbm(h, w, octaves=3, base=48, seed=seed + 1)
    height_map = grain * 0.6 + fine * 0.4
    colour = base[None, None, :] * (0.88 + 0.24 * height_map[:, :, None])
    return _finish(colour, _relief_shade(height_map, relief * 1.4))


def _stone_ashlar(w, h, base, cw, ch, gpx, gcol, relief, ppm, seed):  # noqa: ANN001
    unit, fx, fy = _unit_grid(h, w, cw, ch, stagger=True)
    tone = _hash01(unit, 1.0)[:, :, None]
    hue_jit = (_hash01(unit, 2.3)[:, :, None] - 0.5) * 18.0

    colour = base[None, None, :] * (0.80 + 0.40 * tone) + hue_jit
    rough = fbm(h, w, octaves=5, base=24, seed=seed)
    colour *= (0.86 + 0.28 * rough)[:, :, None]

    colour, inset = _apply_grout(colour, fx, fy, cw, ch, gpx, gcol)
    height_map = rough * 0.5 + inset * 0.5 + tone[:, :, 0] * 0.12
    return _finish(colour, _relief_shade(height_map, relief))


def _stone_ledger(w, h, base, cw, ch, gpx, gcol, relief, ppm, seed):  # noqa: ANN001
    unit, fx, fy = _unit_grid(h, w, cw, ch, stagger=True)
    tone = _hash01(unit, 1.7)[:, :, None]
    colour = base[None, None, :] * (0.70 + 0.62 * tone)

    # Split-face stone: strong horizontal striation within each piece.
    striate = fbm(h, w, octaves=4, base=8, seed=seed + 5)
    striate = cv2.GaussianBlur(striate, (1, 31), 0)
    colour *= (0.82 + 0.34 * striate)[:, :, None]

    colour, inset = _apply_grout(colour, fx, fy, cw, ch, gpx, gcol)
    height_map = striate * 0.45 + inset * 0.4 + tone[:, :, 0] * 0.25
    return _finish(colour, _relief_shade(height_map, relief * 1.3))


def _marble(w, h, base, cw, ch, gpx, gcol, relief, ppm, seed):  # noqa: ANN001
    unit, fx, fy = _unit_grid(h, w, cw, ch, stagger=False)
    # Keep slab-to-slab variation subtle, or the panel grid reads as blocks.
    slab = _hash01(unit, 3.1)[:, :, None]
    colour = base[None, None, :] * (0.985 + 0.03 * slab)

    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    # Veins run on a diagonal and are bent by multi-scale turbulence; raising
    # the ridge to a high power is what makes them thin and sharp instead of
    # the soft blobs a plain sine threshold gives.
    diag = (xx * 0.78 + yy * 0.62) / max(cw, 1.0)
    # A strong low-frequency warp applied to the coordinate itself makes the
    # spacing between veins wander, instead of the evenly combed ridges a
    # plain sine gives.
    # Amplitude must stay well under the diagonal's own span across the slab,
    # otherwise the warped field folds back on itself and the veins close into
    # contour loops instead of running across the stone.
    drift = fbm(h, w, octaves=3, base=2, seed=seed + 101)
    diag = diag + (drift - 0.5) * 1.2

    vein_total = np.zeros((h, w), dtype=np.float32)
    for i, (freq, amp, power, weight) in enumerate(
        ((2.2, 2.8, 8.0, 1.00), (5.0, 1.6, 12.0, 0.6), (11.0, 0.9, 18.0, 0.35))
    ):
        turb = fbm(h, w, octaves=5, base=2 + 2 * i, seed=seed + 13 * i)
        ridge = np.abs(np.sin((diag * freq + (turb - 0.5) * amp) * np.pi))
        layer = np.power(1.0 - ridge, power)
        # Veins die out and reappear along their length rather than running
        # unbroken from edge to edge.
        fade = fbm(h, w, octaves=3, base=3 + i, seed=seed + 57 * (i + 1))
        layer *= np.clip((fade - 0.3) * 2.4, 0.0, 1.0)
        vein_total += weight * layer
    veins = np.clip(vein_total, 0.0, 1.0)
    veins = cv2.GaussianBlur(veins, (0, 0), 0.8)

    # Marble veining is a dark mineral line with a pale halo beside it.
    halo = np.clip(cv2.GaussianBlur(veins, (0, 0), 6.0) - veins, 0.0, 1.0)
    colour *= (1.0 + 0.05 * halo)[:, :, None]
    vein_colour = base[None, None, :] * 0.55
    v = (veins * 0.8)[:, :, None]
    colour = colour * (1.0 - v) + vein_colour * v

    # Faint overall cloudiness so flat areas are not dead.
    cloud = fbm(h, w, octaves=4, base=3, seed=seed + 41)
    colour *= (0.975 + 0.05 * cloud)[:, :, None]

    colour, inset = _apply_grout(colour, fx, fy, cw, ch, gpx, gcol)
    return _finish(colour, _relief_shade(inset * 0.4, relief))


def _tile(w, h, base, cw, ch, gpx, gcol, relief, ppm, seed):  # noqa: ANN001
    unit, fx, fy = _unit_grid(h, w, cw, ch, stagger=False)
    tone = _hash01(unit, 4.4)[:, :, None]
    colour = base[None, None, :] * (0.93 + 0.14 * tone)

    speckle = fbm(h, w, octaves=4, base=40, seed=seed + 2)
    colour *= (0.95 + 0.10 * speckle)[:, :, None]

    colour, inset = _apply_grout(colour, fx, fy, cw, ch, gpx, gcol)

    # Glazed sheen: a soft gradient across each individual tile face.
    sheen = (1.0 - fy) * 0.06 + fx * 0.03
    colour *= (1.0 + sheen * inset)[:, :, None]
    return _finish(colour, _relief_shade(inset * 0.6, relief))


def _wood(w, h, base, cw, ch, gpx, gcol, relief, ppm, seed):  # noqa: ANN001
    unit, fx, fy = _unit_grid(h, w, cw, ch, stagger=True)
    plank = _hash01(unit, 5.9)[:, :, None]
    colour = base[None, None, :] * (0.80 + 0.40 * plank)

    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    wobble = fbm(h, w, octaves=4, base=5, seed=seed + 3)
    grain = np.sin(yy * (0.9 / max(ch, 1.0)) * 18.0 + wobble * 9.0)
    grain = 0.5 + 0.5 * grain
    grain = cv2.GaussianBlur(grain.astype(np.float32), (9, 1), 0)
    colour *= (0.84 + 0.26 * grain)[:, :, None]

    colour, inset = _apply_grout(colour, fx, fy, cw, ch, gpx, gcol)
    return _finish(colour, _relief_shade(grain * 0.3 + inset * 0.5, relief))


def _metal_brushed(w, h, base, cw, ch, gpx, gcol, relief, ppm, seed):  # noqa: ANN001
    unit, fx, fy = _unit_grid(h, w, cw, ch, stagger=False)
    panel = _hash01(unit, 6.3)[:, :, None]
    colour = base[None, None, :] * (0.96 + 0.08 * panel)

    rng = np.random.default_rng(seed + 11)
    streaks = rng.random((h, w)).astype(np.float32)
    streaks = cv2.GaussianBlur(streaks, (121, 1), 0)
    streaks = (streaks - streaks.min()) / max(float(np.ptp(streaks)), 1e-6)
    colour *= (0.93 + 0.14 * streaks)[:, :, None]

    # Flat panels catch light as a broad vertical gradient.
    yy = np.linspace(1.06, 0.94, h, dtype=np.float32)[:, None]
    colour *= yy[:, :, None]

    colour, inset = _apply_grout(colour, fx, fy, cw, ch, gpx, gcol)
    return _finish(colour, _relief_shade(inset * 0.8, relief))


def _shingle(w, h, base, cw, ch, gpx, gcol, relief, ppm, seed):  # noqa: ANN001
    # Strips run the full course width; only the row boundary reads as a
    # reveal, not a grid, since shingle tabs within a course overlap rather
    # than butt-join the way a tile or panel does.
    unit, fx, fy = _unit_grid(h, w, cw, ch, stagger=True)
    row = np.floor(unit / 7919.0)
    tone = _hash01(row, 2.9)[:, :, None]
    colour = base[None, None, :] * (0.78 + 0.34 * tone)

    grit = fbm(h, w, octaves=5, base=64, seed=seed + 9)
    colour *= (0.90 + 0.18 * grit)[:, :, None]

    dist_y = np.minimum(fy * ch, (1.0 - fy) * ch)
    reveal = np.clip((gpx * 2.2 - dist_y) / max(gpx * 2.2, 1e-3), 0.0, 1.0)[:, :, None]
    colour = colour * (1.0 - 0.6 * reveal) + gcol[None, None, :] * (0.6 * reveal)

    height_map = grit * 0.4 + (1.0 - dist_y / max(ch, 1.0)) * 0.6
    return _finish(colour, _relief_shade(height_map, relief * 1.2))


_BUILDERS = {
    "plaster": _plaster,
    "stone_ashlar": _stone_ashlar,
    "stone_ledger": _stone_ledger,
    "marble": _marble,
    "tile": _tile,
    "wood": _wood,
    "metal_brushed": _metal_brushed,
    "shingle": _shingle,
}

AVAILABLE_TEXTURES = tuple(_BUILDERS)
