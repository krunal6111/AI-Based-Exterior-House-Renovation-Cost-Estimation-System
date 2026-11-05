"""Pure geometry helpers: polygons, quads and perspective rectification.

No I/O, no database, no OpenCV windows -- everything here is a deterministic
function of its inputs, which is what makes the estimation math unit-testable.

Coordinate conventions
----------------------
* **Normalised** coordinates are ``[x, y]`` floats in ``0..1``, relative to the
  working image. This is how geometry is persisted.
* **Pixel** coordinates are ``[x, y]`` floats in image space.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

import numpy as np

Point = Sequence[float]
Polygon = Sequence[Point]


# ---------------------------------------------------------------------------
# conversions
# ---------------------------------------------------------------------------


def to_pixels(polygon: Polygon, width: int, height: int) -> np.ndarray:
    """Normalised polygon -> float32 pixel array of shape (N, 2)."""
    arr = np.asarray(polygon, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise ValueError(f"polygon must be (N, 2), got {arr.shape}")
    return np.column_stack([arr[:, 0] * width, arr[:, 1] * height]).astype(np.float64)


def to_normalised(points: np.ndarray, width: int, height: int) -> list[list[float]]:
    arr = np.asarray(points, dtype=np.float64)
    return [
        [float(np.clip(x / width, 0.0, 1.0)), float(np.clip(y / height, 0.0, 1.0))]
        for x, y in arr
    ]


# ---------------------------------------------------------------------------
# basic measures
# ---------------------------------------------------------------------------


def polygon_area_px(points: np.ndarray | Polygon) -> float:
    """Shoelace area. Always positive, so winding order does not matter."""
    arr = np.asarray(points, dtype=np.float64)
    if len(arr) < 3:
        return 0.0
    x, y = arr[:, 0], arr[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2.0)


def polyline_length_px(points: np.ndarray | Polygon, closed: bool = False) -> float:
    arr = np.asarray(points, dtype=np.float64)
    if len(arr) < 2:
        return 0.0
    seq = np.vstack([arr, arr[:1]]) if closed else arr
    return float(np.sum(np.linalg.norm(np.diff(seq, axis=0), axis=1)))


def centroid_px(points: np.ndarray | Polygon) -> tuple[float, float]:
    arr = np.asarray(points, dtype=np.float64)
    if len(arr) == 0:
        return (0.0, 0.0)
    if len(arr) < 3:
        return (float(arr[:, 0].mean()), float(arr[:, 1].mean()))
    x, y = arr[:, 0], arr[:, 1]
    cross = x * np.roll(y, -1) - np.roll(x, -1) * y
    a = cross.sum() / 2.0
    if abs(a) < 1e-9:
        return (float(x.mean()), float(y.mean()))
    cx = float(((x + np.roll(x, -1)) * cross).sum() / (6.0 * a))
    cy = float(((y + np.roll(y, -1)) * cross).sum() / (6.0 * a))
    return (cx, cy)


def bounding_box_px(points: np.ndarray | Polygon) -> tuple[float, float, float, float]:
    arr = np.asarray(points, dtype=np.float64)
    return (
        float(arr[:, 0].min()),
        float(arr[:, 1].min()),
        float(arr[:, 0].max()),
        float(arr[:, 1].max()),
    )


# ---------------------------------------------------------------------------
# quad fitting
# ---------------------------------------------------------------------------


def order_quad(points: np.ndarray) -> np.ndarray:
    """Order 4 points as top-left, top-right, bottom-right, bottom-left.

    Angle-from-centroid alone is not enough: for a wide rectangle the top-left
    corner sits past -135 degrees, so anchoring the sort at a fixed angle
    rotates the whole cycle and silently swaps width for height. Instead we
    take the cyclic order from the angles, force clockwise winding, and only
    then rotate the cycle so the most top-left corner leads.
    """
    pts = np.asarray(points, dtype=np.float64).reshape(4, 2)
    centre = pts.mean(axis=0)
    angles = np.arctan2(pts[:, 1] - centre[1], pts[:, 0] - centre[0])
    pts = pts[np.argsort(angles)]

    # Shoelace > 0 in image coordinates (y down) == clockwise on screen.
    x, y = pts[:, 0], pts[:, 1]
    if float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) < 0:
        pts = pts[::-1]

    start = int(np.argmin(pts[:, 0] + pts[:, 1]))
    return np.roll(pts, -start, axis=0)


def min_area_quad(points: np.ndarray | Polygon) -> np.ndarray:
    """Best-fit convex quadrilateral for an arbitrary polygon.

    Tries progressively coarser Douglas-Peucker simplification of the convex
    hull until exactly four vertices remain; falls back to the minimum-area
    rotated rectangle. The quad is what the perspective rectification needs --
    a wall polygon with a notch cut out of it still sits on one plane.
    """
    import cv2

    arr = np.asarray(points, dtype=np.float32).reshape(-1, 1, 2)
    if len(arr) < 3:
        raise ValueError("need at least 3 points to fit a quad")

    hull = cv2.convexHull(arr)
    peri = cv2.arcLength(hull, True)

    for frac in (0.01, 0.02, 0.03, 0.05, 0.08, 0.12, 0.18, 0.25):
        approx = cv2.approxPolyDP(hull, frac * peri, True)
        if len(approx) == 4:
            return order_quad(approx.reshape(-1, 2).astype(np.float64))

    box = cv2.boxPoints(cv2.minAreaRect(arr))
    return order_quad(np.asarray(box, dtype=np.float64))


# ---------------------------------------------------------------------------
# perspective rectification
# ---------------------------------------------------------------------------


#: Focal length assumed when it cannot be recovered from the quad, as a
#: multiple of the image's long edge. A phone main camera (~26 mm equivalent)
#: works out at ~0.72x; a slightly tighter framing or a modest crop pushes it
#: towards 1.0x. 0.85 splits the difference (~61 degree horizontal field of
#: view). This only matters for square-on photos, where the quad carries no
#: perspective cue to recover the real focal length from -- getting it wrong
#: by 20% moves the estimated area by roughly 5%, well inside the declared
#: Tier B band.
ASSUMED_FOCAL_RATIO = 0.85


def estimate_focal_length(
    quad: np.ndarray, image_width: int, image_height: int
) -> tuple[float, bool]:
    """Recover the camera focal length in pixels from one projected rectangle.

    Closed-form solution of Zhang & He (2006), assuming square pixels and a
    principal point at the image centre. Returns ``(focal_px, recovered)``;
    when the quad degenerates to a parallelogram -- i.e. the photo was taken
    square-on, so the perspective cue is gone -- there is nothing to recover
    and a typical focal length is assumed instead.
    """
    pts = order_quad(quad)  # TL, TR, BR, BL
    cx, cy = image_width / 2.0, image_height / 2.0
    assumed = ASSUMED_FOCAL_RATIO * max(image_width, image_height)

    m1 = np.array([pts[0][0] - cx, pts[0][1] - cy, 1.0])  # TL = origin corner
    m2 = np.array([pts[1][0] - cx, pts[1][1] - cy, 1.0])  # TR = along width
    m3 = np.array([pts[3][0] - cx, pts[3][1] - cy, 1.0])  # BL = along height
    m4 = np.array([pts[2][0] - cx, pts[2][1] - cy, 1.0])  # BR = diagonal

    den2 = np.dot(np.cross(m2, m4), m3)
    den3 = np.dot(np.cross(m3, m4), m2)
    if abs(den2) < 1e-9 or abs(den3) < 1e-9:
        return (assumed, False)

    n2 = (np.dot(np.cross(m1, m4), m3) / den2) * m2 - m1
    n3 = (np.dot(np.cross(m1, m4), m2) / den3) * m3 - m1

    # Both edge pairs parallel in the image => no finite vanishing point.
    if abs(n2[2]) < 1e-7 or abs(n3[2]) < 1e-7:
        return (assumed, False)

    f_sq = -((n2[0] * n3[0]) + (n2[1] * n3[1])) / (n2[2] * n3[2])
    if not np.isfinite(f_sq) or f_sq <= 1.0:
        return (assumed, False)

    focal = float(math.sqrt(f_sq))
    # Reject physically implausible solutions (0.3x - 6x the long edge).
    long_edge = max(image_width, image_height)
    if not (0.3 * long_edge <= focal <= 6.0 * long_edge):
        return (assumed, False)
    return (focal, True)


def rectified_aspect_ratio(
    quad: np.ndarray, image_width: int, image_height: int
) -> tuple[float, float]:
    """True width:height ratio of a rectangle seen in perspective.

    Works in two steps so that a square-on photo degrades gracefully instead of
    falling back to the raw on-screen edge ratio (which is foreshortened and
    can be 20%+ wrong):

    1. Estimate the focal length from the quad, or assume a typical one.
    2. Build the homography from a unit square onto the quad. Writing
       ``H = K[r1 r2 t]`` and imposing ``|r1| = |r2| = 1`` on the real
       rectangle gives ``width/height = |K^-1 h1| / |K^-1 h2|``.

    Returns ``(aspect_ratio, confidence)`` with confidence in 0..1.
    """
    import cv2

    pts = order_quad(quad).astype(np.float32)
    focal, recovered = estimate_focal_length(quad, image_width, image_height)

    unit = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float32)
    try:
        H = cv2.getPerspectiveTransform(unit, pts)
    except cv2.error:
        return (1.0, 0.0)

    cx, cy = image_width / 2.0, image_height / 2.0
    k_inv = np.array(
        [[1.0 / focal, 0.0, -cx / focal], [0.0, 1.0 / focal, -cy / focal], [0, 0, 1.0]]
    )
    w_vec = k_inv @ H[:, 0]
    h_vec = k_inv @ H[:, 1]
    nw, nh = float(np.linalg.norm(w_vec)), float(np.linalg.norm(h_vec))
    if nh < 1e-12 or nw < 1e-12:
        return (1.0, 0.0)

    ratio = nw / nh
    if not np.isfinite(ratio) or not (0.02 < ratio < 50.0):
        return (1.0, 0.0)

    return (float(ratio), 0.9 if recovered else 0.6)


def quad_vertical_extent_px(quad: np.ndarray) -> float:
    """Mean length of the two (near-)vertical edges of an ordered quad."""
    pts = order_quad(quad)
    left = float(np.linalg.norm(pts[3] - pts[0]))   # TL -> BL
    right = float(np.linalg.norm(pts[2] - pts[1]))  # TR -> BR
    return (left + right) / 2.0


def quad_horizontal_extent_px(quad: np.ndarray) -> float:
    pts = order_quad(quad)
    top = float(np.linalg.norm(pts[1] - pts[0]))
    bottom = float(np.linalg.norm(pts[2] - pts[3]))
    return (top + bottom) / 2.0


def fill_fraction(polygon: np.ndarray | Polygon, quad: np.ndarray) -> float:
    """How much of its bounding quad the polygon actually covers (0..1].

    Lets an L-shaped or clipped wall be scaled off the quad's rectified
    dimensions without pretending it is a full rectangle.
    """
    quad_area = polygon_area_px(quad)
    if quad_area <= 1e-9:
        return 1.0
    return float(min(1.0, polygon_area_px(polygon) / quad_area))


# ---------------------------------------------------------------------------
# boolean operations (occluders)
# ---------------------------------------------------------------------------


def subtract_children(
    parent: Polygon, children: Iterable[Polygon]
) -> tuple[list[list[list[float]]], float]:
    """Cut child polygons (windows, doors) out of a parent wall polygon.

    Returns ``(remaining_rings, retained_fraction)``. The fraction is the share
    of the parent's area that survives, which is exactly the factor the area
    estimate must be multiplied by so an opening is never painted or billed.
    """
    from shapely.geometry import MultiPolygon
    from shapely.geometry import Polygon as ShapelyPolygon
    from shapely.validation import make_valid

    parent_poly = make_valid(ShapelyPolygon(parent))
    if parent_poly.is_empty or parent_poly.area <= 0:
        return ([list(map(list, parent))], 1.0)
    original_area = parent_poly.area

    for child in children:
        pts = list(child)
        if len(pts) < 3:
            continue
        child_poly = make_valid(ShapelyPolygon(pts))
        if child_poly.is_empty:
            continue
        try:
            parent_poly = parent_poly.difference(child_poly)
        except Exception:  # pragma: no cover - shapely robustness guard
            continue

    if parent_poly.is_empty:
        return ([], 0.0)

    geoms = (
        list(parent_poly.geoms)
        if isinstance(parent_poly, MultiPolygon)
        else [parent_poly]
    )
    rings = [
        [[float(x), float(y)] for x, y in g.exterior.coords[:-1]]
        for g in geoms
        if g.area > 0
    ]
    retained = float(parent_poly.area / original_area) if original_area else 1.0
    return (rings, max(0.0, min(1.0, retained)))


def simplify_polygon(polygon: Polygon, tolerance: float = 0.004) -> list[list[float]]:
    """Drop redundant vertices so the canvas editor stays draggable.

    ``tolerance`` is in normalised units, so ~0.4% of the image edge.
    """
    from shapely.geometry import Polygon as ShapelyPolygon

    pts = list(polygon)
    if len(pts) <= 4:
        return [[float(x), float(y)] for x, y in pts]
    simplified = ShapelyPolygon(pts).simplify(tolerance, preserve_topology=True)
    if simplified.is_empty or simplified.geom_type != "Polygon":
        return [[float(x), float(y)] for x, y in pts]
    coords = [[float(x), float(y)] for x, y in simplified.exterior.coords[:-1]]
    return coords if len(coords) >= 3 else [[float(x), float(y)] for x, y in pts]
