"""Offline facade region detection (requirement 5.2).

Classical computer vision only -- no model weights, no network, no API key.
The output is explicitly a *draft*: the workflow sends the user straight into
a polygon editor, because a colour-and-edge segmenter will never get a real
facade perfectly right. Getting the user 80% of the way there in a second,
with every outline editable, beats both a blank canvas and a false promise of
automation.

Method
------
1. Split the scene into sky / vegetation / building.
2. Oversegment the building into SLIC superpixels, then cluster those by mean
   LAB colour. Painted surfaces, trim, roof and glazing separate cleanly on
   colour in almost every facade photo.
3. Turn each cluster's connected components into polygons and classify them
   by position, size, shape and relative darkness.
4. Nest openings inside the wall they overlap.
"""

from __future__ import annotations

import cv2
import numpy as np
from skimage.segmentation import slic

from ..enums import RegionCategory, RegionSource
from ..services import geometry as geo
from ..services.scene import SceneMasks, scene_masks
from .base import DetectedRegion

#: Work at this resolution regardless of upload size -- detection quality is
#: driven by structure, not pixels, and this keeps it fast.
WORK_EDGE = 760

MIN_REGION_FRACTION = 0.010  # of the building's area
MAX_REGIONS = 10
#: Area / bounding-quad area. Below this a component is treated as clutter.
MIN_RECTANGULARITY = 0.45


class CvSegmentationProvider:
    name = "offline-cv"

    def available(self) -> bool:
        return True

    def detect(self, bgr: np.ndarray) -> list[DetectedRegion]:
        return detect_regions(bgr)


# ---------------------------------------------------------------------------


def _downscale(bgr: np.ndarray) -> tuple[np.ndarray, float]:
    h, w = bgr.shape[:2]
    longest = max(h, w)
    if longest <= WORK_EDGE:
        return bgr, 1.0
    scale = WORK_EDGE / longest
    small = cv2.resize(
        bgr,
        (max(1, int(w * scale)), max(1, int(h * scale))),
        interpolation=cv2.INTER_AREA,
    )
    return small, scale


def _polygon_from_mask(
    mask: np.ndarray, width: int, height: int, epsilon_frac: float = 0.02
) -> tuple[list[list[float]], float] | None:
    """Component -> editable polygon, plus how rectangular it was.

    Facade surfaces are quadrilaterals in the real world, so a component that
    is already close to one is snapped to its best-fit quad. Four draggable
    corners that line up with the wall are far more useful to correct than a
    twelve-point outline that traces every bush in front of it.
    """
    contours, _ = cv2.findContours(
        mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(contour)
    if area < 24:
        return None

    quad = geo.min_area_quad(contour.reshape(-1, 2).astype(np.float64))
    quad_area = geo.polygon_area_px(quad)
    rectangularity = float(area / quad_area) if quad_area > 1e-6 else 0.0

    if rectangularity >= 0.72:
        return geo.to_normalised(quad, width, height), rectangularity

    peri = cv2.arcLength(contour, True)
    approx = cv2.approxPolyDP(contour, epsilon_frac * peri, True)
    if len(approx) > 10:
        approx = cv2.approxPolyDP(contour, 0.04 * peri, True)
    if len(approx) < 3:
        return None
    pts = approx.reshape(-1, 2).astype(np.float64)
    return geo.to_normalised(pts, width, height), rectangularity


def _classify(
    comp_mask: np.ndarray,
    lightness: np.ndarray,
    building_box: tuple[int, int, int, int],
    building_mean_l: float,
    img_h: int,
    img_w: int,
) -> tuple[RegionCategory, float, dict]:
    ys, xs = np.where(comp_mask)
    if len(ys) == 0:
        return RegionCategory.OTHER, 0.2, {}

    x0, y0, x1, y1 = xs.min(), ys.min(), xs.max(), ys.max()
    bw, bh = max(1, x1 - x0), max(1, y1 - y0)
    area = float(len(ys))
    extent = area / float(bw * bh)  # how rectangular the blob is
    aspect = bw / float(bh)

    bx0, by0, bx1, by1 = building_box
    b_h = max(1, by1 - by0)
    b_w = max(1, bx1 - bx0)
    rel_area = area / float(b_h * b_w)
    rel_y = (y0 + y1) / 2.0  # absolute, compared against the building band
    vertical_pos = (rel_y - by0) / b_h  # 0 at building top, 1 at its base

    mean_l = float(lightness[comp_mask].mean())
    darkness = (building_mean_l - mean_l) / max(building_mean_l, 1.0)

    meta = {
        "extent": round(extent, 3),
        "aspect": round(aspect, 3),
        "rel_area": round(rel_area, 4),
        "vertical_pos": round(float(vertical_pos), 3),
        "darkness": round(darkness, 3),
    }

    # --- openings: compact, rectangular, darker than the facade -----------
    if (
        0.0015 < rel_area < 0.10
        and extent > 0.55
        and darkness > 0.12
        and 0.25 < aspect < 3.2
    ):
        # A door sits in the lower half of the facade and is clearly taller
        # than it is wide. Getting this right matters well beyond the label:
        # a detected door is what the measurement step pre-places its scale
        # reference on, and a door is the most dependable known dimension in
        # any facade photograph.
        if vertical_pos > 0.45 and aspect < 0.85:
            return RegionCategory.DOOR, 0.5, meta
        return RegionCategory.WINDOW, 0.55, meta

    # --- pillars: tall, narrow, reaching the base -------------------------
    if aspect < 0.30 and bh > 0.30 * b_h and rel_area < 0.09:
        return RegionCategory.PILLAR, 0.45, meta

    # --- parapet / roof band: wide, shallow, high up ----------------------
    if aspect > 3.5 and vertical_pos < 0.30 and rel_area < 0.12:
        return RegionCategory.PARAPET, 0.35, meta

    # --- railing: wide, shallow, low down, busy with vertical edges -------
    if aspect > 2.5 and 0.45 < vertical_pos < 0.95 and rel_area < 0.10:
        return RegionCategory.RAILING, 0.3, meta

    confidence = 0.65 if rel_area > 0.06 else 0.45
    return RegionCategory.WALL, confidence, meta


def detect_regions(bgr: np.ndarray) -> list[DetectedRegion]:
    # k-means seeds itself from OpenCV's global RNG. Without pinning it, the
    # same photo yields different regions on every run, which makes the
    # detection step feel unreliable and breaks reproducible estimates.
    cv2.setRNGSeed(20260101)

    small, _ = _downscale(bgr)
    h, w = small.shape[:2]

    masks: SceneMasks = scene_masks(small)
    if masks.building_box is None or not masks.building.any():
        return []

    lab = cv2.cvtColor(cv2.bilateralFilter(small, 9, 70, 70), cv2.COLOR_BGR2LAB)
    lightness = lab[:, :, 0].astype(np.float32)
    building = masks.building
    building_area = float(building.sum())
    building_mean_l = float(lightness[building].mean())

    # --- 1. superpixels, restricted to the building ---------------------
    segments = slic(
        cv2.cvtColor(small, cv2.COLOR_BGR2RGB),
        n_segments=420,
        compactness=12.0,
        sigma=1.5,
        start_label=1,
        mask=building,
    )

    ids = [i for i in np.unique(segments) if i != 0]
    if not ids:
        return []

    features = np.array(
        [lab[segments == i].mean(axis=0) for i in ids], dtype=np.float32
    )

    # --- 2. cluster superpixels by colour --------------------------------
    k = int(np.clip(len(ids) // 14, 3, 6))
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 24, 0.8)
    _, labels, _ = cv2.kmeans(
        features, k, None, criteria, 4, cv2.KMEANS_PP_CENTERS
    )
    labels = labels.flatten()

    cluster_map = np.zeros((h, w), dtype=np.int32)
    for seg_id, cluster in zip(ids, labels):
        cluster_map[segments == seg_id] = cluster + 1

    # --- 3. connected components per cluster -> candidate regions --------
    candidates: list[tuple[float, np.ndarray]] = []
    for cluster in range(1, k + 1):
        mask = (cluster_map == cluster).astype(np.uint8)
        if mask.sum() < MIN_REGION_FRACTION * building_area:
            continue
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))

        count, comp_labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        for idx in range(1, count):
            area = stats[idx, cv2.CC_STAT_AREA]
            if area < MIN_REGION_FRACTION * building_area:
                continue
            candidates.append((float(area), comp_labels == idx))

    candidates.sort(key=lambda c: c[0], reverse=True)
    candidates = candidates[:MAX_REGIONS]

    # --- 4. classify and build polygons ----------------------------------
    regions: list[DetectedRegion] = []
    counters: dict[RegionCategory, int] = {}

    for area, comp in candidates:
        category, confidence, meta = _classify(
            comp, lightness, masks.building_box, building_mean_l, h, w
        )
        built = _polygon_from_mask(comp, w, h)
        if built is None:
            continue
        polygon, rectangularity = built

        # Scattered, non-convex blobs are nearly always foliage or shadow
        # mistaken for a surface. Leaving them out keeps the review step about
        # adjusting real surfaces rather than deleting junk.
        if rectangularity < MIN_RECTANGULARITY:
            continue
        meta["rectangularity"] = round(rectangularity, 3)

        if len(polygon) > 4:
            polygon = geo.simplify_polygon(polygon, tolerance=0.006)
        if len(polygon) < 3:
            continue

        counters[category] = counters.get(category, 0) + 1
        suffix = f" {counters[category]}" if counters[category] > 1 else ""
        regions.append(
            DetectedRegion(
                label=f"{category.label}{suffix}",
                category=category,
                polygon=polygon,
                confidence=confidence,
                source=RegionSource.CV,
                meta=meta,
            )
        )

    _nest_openings(regions)
    return regions


def _nest_openings(regions: list[DetectedRegion]) -> None:
    """Point each opening at the wall it sits on, by polygon overlap."""
    from shapely.geometry import Polygon as ShapelyPolygon
    from shapely.validation import make_valid

    walls = [
        (i, make_valid(ShapelyPolygon(r.polygon)))
        for i, r in enumerate(regions)
        if r.category is RegionCategory.WALL and len(r.polygon) >= 3
    ]
    if not walls:
        return

    for i, region in enumerate(regions):
        if not region.category.is_opening or len(region.polygon) < 3:
            continue
        shape = make_valid(ShapelyPolygon(region.polygon))
        if shape.is_empty or shape.area <= 0:
            continue
        best_idx, best_overlap = None, 0.0
        for wall_idx, wall in walls:
            if wall_idx == i or wall.is_empty:
                continue
            try:
                overlap = shape.intersection(wall).area / shape.area
            except Exception:  # pragma: no cover
                continue
            if overlap > best_overlap:
                best_idx, best_overlap = wall_idx, overlap
        if best_idx is not None and best_overlap > 0.35:
            region.parent_index = best_idx
