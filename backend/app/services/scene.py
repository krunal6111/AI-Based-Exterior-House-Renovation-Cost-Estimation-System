"""Coarse scene decomposition: sky, vegetation, building.

Shared by the quality gate (is there even a building in this photo?) and by
the offline region detector (where should it look?). Classical CV only -- no
model weights, no network.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class SceneMasks:
    sky: np.ndarray          # bool
    vegetation: np.ndarray   # bool
    building: np.ndarray     # bool
    building_box: tuple[int, int, int, int] | None  # x0, y0, x1, y1

    @property
    def building_fraction(self) -> float:
        return float(self.building.mean()) if self.building.size else 0.0


def _largest_components(
    mask: np.ndarray, min_fraction: float = 0.01, keep: int = 1
) -> np.ndarray:
    """Keep the ``keep`` largest connected components above a size floor."""
    m = mask.astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    if count <= 1:
        return np.zeros_like(mask, dtype=bool)
    areas = stats[1:, cv2.CC_STAT_AREA]
    floor = min_fraction * mask.size
    order = np.argsort(areas)[::-1]
    out = np.zeros_like(mask, dtype=bool)
    taken = 0
    for idx in order:
        if areas[idx] < floor or taken >= keep:
            break
        out |= labels == (idx + 1)
        taken += 1
    return out


def _fill_small_holes(mask: np.ndarray, max_fraction: float) -> np.ndarray:
    """Close gaps smaller than ``max_fraction`` of the frame.

    Tree canopies are full of sky-coloured gaps and a facade is full of
    windows; both need to read as one solid region, but the fill has to be
    bounded or a ring of trees would swallow the house.
    """
    m = mask.astype(np.uint8)
    inverse = (m == 0).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(inverse, 8)
    out = m.copy()
    limit = max_fraction * m.size
    for i in range(1, count):
        if stats[i, cv2.CC_STAT_AREA] < limit:
            out[labels == i] = 1
    return out


def structure_density(bgr: np.ndarray) -> np.ndarray:
    """Where the long straight lines are, normalised 0..1.

    This is the cue that separates a building from foliage far more reliably
    than colour: a facade is made of long horizontal and vertical edges (roof
    lines, sills, posts, courses), while a tree is high-frequency noise with
    no sustained straight runs.
    """
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 40, 130)

    min_len = max(12, int(0.07 * max(h, w)))
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180, threshold=50, minLineLength=min_len, maxLineGap=12
    )

    acc = np.zeros((h, w), dtype=np.float32)
    if lines is not None:
        for x1, y1, x2, y2 in lines[:, 0]:
            angle = abs(np.degrees(np.arctan2(float(y2 - y1), float(x2 - x1))))
            angle = min(angle, 180.0 - angle)
            # Keep near-horizontal (<25 deg) and near-vertical (>65 deg) runs.
            if angle < 25.0 or angle > 65.0:
                cv2.line(acc, (x1, y1), (x2, y2), 1.0, 9)

    density = cv2.GaussianBlur(acc, (0, 0), max(h, w) * 0.015)
    peak = float(density.max())
    return density / peak if peak > 1e-6 else density


def _reconstruct(marker: np.ndarray, mask: np.ndarray, iterations: int = 40) -> np.ndarray:
    """Geodesic dilation of ``marker`` constrained to ``mask``."""
    kernel = np.ones((5, 5), np.uint8)
    current = (marker & mask).astype(np.uint8)
    for _ in range(iterations):
        grown = cv2.dilate(current, kernel) & mask
        if np.array_equal(grown, current):
            break
        current = grown
    return current


def _detach_thin_links(mask: np.ndarray, radius: int) -> np.ndarray:
    """Drop regions joined to the main mass only by a narrow bridge.

    A flight of steps or a path can tie the lawn and flower beds to the house,
    making them one connected component. Eroding snaps those thin links; the
    surviving core is then regrown inside the original mask so the facade
    keeps its true outline.
    """
    m = mask.astype(np.uint8)
    if radius < 1:
        return m
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1,) * 2)
    core = cv2.erode(m, kernel)
    if core.sum() == 0:
        return m
    core = _largest_components(core.astype(bool), min_fraction=0.0, keep=1).astype(np.uint8)
    if core.sum() == 0:
        return m
    return _reconstruct(core, m)


def _components_touching_top(mask: np.ndarray, band: int = 4) -> np.ndarray:
    """Sky has to actually reach the top of the frame."""
    m = mask.astype(np.uint8)
    count, labels, _, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    if count <= 1:
        return np.zeros_like(mask, dtype=bool)
    top_labels = set(np.unique(labels[:band, :])) - {0}
    if not top_labels:
        return np.zeros_like(mask, dtype=bool)
    return np.isin(labels, list(top_labels))


#: Every mask is computed at this long-edge resolution and then resized back.
#: The morphology kernels below are sized in pixels, so without a fixed
#: working resolution the same photo would segment differently depending on
#: how large it was uploaded.
MASK_WORK_EDGE = 760


def scene_masks(bgr: np.ndarray) -> SceneMasks:
    full_h, full_w = bgr.shape[:2]
    longest = max(full_h, full_w)

    if longest > MASK_WORK_EDGE:
        scale = MASK_WORK_EDGE / longest
        work = cv2.resize(
            bgr,
            (max(1, int(full_w * scale)), max(1, int(full_h * scale))),
            interpolation=cv2.INTER_AREA,
        )
        masks = _scene_masks_at_working_size(work)
        return _upscale_masks(masks, full_w, full_h)

    return _scene_masks_at_working_size(bgr)


def _upscale_masks(masks: SceneMasks, width: int, height: int) -> SceneMasks:
    def up(m: np.ndarray) -> np.ndarray:
        return (
            cv2.resize(
                m.astype(np.uint8), (width, height), interpolation=cv2.INTER_NEAREST
            ).astype(bool)
        )

    building = up(masks.building)
    box = None
    if building.any():
        ys, xs = np.where(building)
        box = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
    return SceneMasks(
        sky=up(masks.sky),
        vegetation=up(masks.vegetation),
        building=building,
        building_box=box,
    )


def _scene_masks_at_working_size(bgr: np.ndarray) -> SceneMasks:
    h_img, w_img = bgr.shape[:2]
    blur = cv2.bilateralFilter(bgr, 7, 60, 60)
    hsv = cv2.cvtColor(blur, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    # --- sky -----------------------------------------------------------
    blue_sky = (hue >= 90) & (hue <= 135) & (sat > 35) & (val > 90)
    bright_overcast = (sat < 38) & (val > 180)
    sky = blue_sky | bright_overcast

    # Sky lives in the upper part of a facade photo and must touch the top.
    upper = np.zeros((h_img, w_img), dtype=bool)
    upper[: int(0.65 * h_img), :] = True
    sky &= upper
    sky = cv2.morphologyEx(
        sky.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8)
    ).astype(bool)
    sky = _components_touching_top(sky)
    sky = cv2.morphologyEx(
        sky.astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8)
    ).astype(bool)

    # --- vegetation ------------------------------------------------------
    # Close first, then fill the gaps between leaves, so a canopy becomes one
    # solid region instead of thousands of speckles the building mask would
    # otherwise inherit.
    vegetation = (hue >= 28) & (hue <= 92) & (sat > 55) & (val > 25)
    vegetation = cv2.morphologyEx(
        vegetation.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((21, 21), np.uint8)
    )
    vegetation = _fill_small_holes(vegetation, 0.02)
    vegetation = cv2.morphologyEx(
        vegetation, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8)
    ).astype(bool)

    # --- building --------------------------------------------------------
    # Straight-line structure, minus anything already claimed by sky or
    # foliage. Colour alone is not enough: shaded lawn, mulch and tree trunks
    # are neither green nor sky, and would otherwise read as facade.
    structure = structure_density(bgr) > 0.30
    building = (structure & ~vegetation & ~sky).astype(np.uint8)
    building = cv2.morphologyEx(
        building, cv2.MORPH_CLOSE, np.ones((17, 17), np.uint8)
    )
    building = _largest_components(building.astype(bool), min_fraction=0.02, keep=1)
    building = _fill_small_holes(building, 0.05)
    building = _detach_thin_links(
        building, radius=max(4, int(round(min(h_img, w_img) * 0.025)))
    ).astype(bool)

    box = None
    if building.any():
        ys, xs = np.where(building)
        box = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))

    return SceneMasks(
        sky=sky, vegetation=vegetation, building=building, building_box=box
    )


def shading_field(bgr: np.ndarray, sigma: int = 0) -> np.ndarray:
    """Material-independent lighting map, ~1.0 on average.

    Divides the perceptual lightness by a heavily blurred copy of itself. What
    survives is shadow, soft occlusion, streaking and surface grain -- exactly
    the cues that make a repainted wall still look like a photograph rather
    than a flat fill.
    """
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    lightness = lab[:, :, 0].astype(np.float32)
    if sigma <= 0:
        sigma = max(9, int(round(min(bgr.shape[:2]) * 0.06)) | 1)
    if sigma % 2 == 0:
        sigma += 1
    base = cv2.GaussianBlur(lightness, (sigma, sigma), 0)
    base = np.maximum(base, 1.0)
    field = lightness / base
    return np.clip(field, 0.35, 1.9)
