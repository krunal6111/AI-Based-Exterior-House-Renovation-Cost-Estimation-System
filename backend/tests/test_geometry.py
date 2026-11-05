"""Geometry and perspective rectification.

The rectification tests project synthetic rectangles through a known camera,
so the expected answer is exact rather than eyeballed.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.services import geometry as geo

W, H = 1600, 1200
FOCAL = 1500.0
K = np.array([[FOCAL, 0, W / 2], [0, FOCAL, H / 2], [0, 0, 1]], dtype=float)


def project(points3d: np.ndarray, yaw_deg: float, pitch_deg: float, distance: float = 6.0):
    ay, ax = math.radians(yaw_deg), math.radians(pitch_deg)
    ry = np.array(
        [[math.cos(ay), 0, math.sin(ay)], [0, 1, 0], [-math.sin(ay), 0, math.cos(ay)]]
    )
    rx = np.array(
        [[1, 0, 0], [0, math.cos(ax), -math.sin(ax)], [0, math.sin(ax), math.cos(ax)]]
    )
    camera = (rx @ ry @ points3d.T).T + np.array([0, 0, distance])
    image = (K @ camera.T).T
    return image[:, :2] / image[:, 2:3]


def rectangle(aspect: float) -> np.ndarray:
    w, h = aspect, 1.0
    return np.array(
        [[-w / 2, -h / 2, 0], [w / 2, -h / 2, 0], [w / 2, h / 2, 0], [-w / 2, h / 2, 0]],
        dtype=float,
    )


# ---------------------------------------------------------------------------


def test_polygon_area_unit_square():
    assert geo.polygon_area_px([[0, 0], [1, 0], [1, 1], [0, 1]]) == pytest.approx(1.0)


def test_polygon_area_ignores_winding_direction():
    clockwise = [[0, 0], [2, 0], [2, 3], [0, 3]]
    counter = list(reversed(clockwise))
    assert geo.polygon_area_px(clockwise) == pytest.approx(6.0)
    assert geo.polygon_area_px(counter) == pytest.approx(6.0)


def test_polyline_length():
    assert geo.polyline_length_px([[0, 0], [3, 4]]) == pytest.approx(5.0)
    assert geo.polyline_length_px([[0, 0], [3, 4], [3, 0]]) == pytest.approx(9.0)


def test_order_quad_handles_wide_rectangles():
    """A wide rectangle used to come back rotated, swapping width for height."""
    pts = np.array([[100, 50], [600, 50], [600, 300], [100, 300]], dtype=float)
    ordered = geo.order_quad(pts[[2, 0, 3, 1]])  # deliberately shuffled
    assert ordered[0].tolist() == [100, 50]   # top-left
    assert ordered[1].tolist() == [600, 50]   # top-right
    assert ordered[2].tolist() == [600, 300]  # bottom-right
    assert ordered[3].tolist() == [100, 300]  # bottom-left


@pytest.mark.parametrize("aspect", [0.5, 1.0, 2.0, 3.5])
def test_fronto_parallel_aspect_is_exact(aspect):
    quad = project(rectangle(aspect), 0, 0)
    recovered, _ = geo.rectified_aspect_ratio(quad, W, H)
    assert recovered == pytest.approx(aspect, rel=0.02)


@pytest.mark.parametrize("aspect", [0.5, 1.0, 2.0, 3.5])
@pytest.mark.parametrize("yaw,pitch", [(30, 12), (55, 20), (-40, 15)])
def test_perspective_aspect_recovered(aspect, yaw, pitch):
    """With a real perspective cue the focal length is recoverable, so the
    aspect ratio should come back essentially exact."""
    quad = project(rectangle(aspect), yaw, pitch)
    recovered, confidence = geo.rectified_aspect_ratio(quad, W, H)
    assert recovered == pytest.approx(aspect, rel=0.05)
    assert confidence >= 0.8


def test_focal_length_recovered_from_perspective():
    quad = project(rectangle(2.0), 35, 15)
    focal, recovered = geo.estimate_focal_length(quad, W, H)
    assert recovered is True
    assert focal == pytest.approx(FOCAL, rel=0.08)


def test_focal_length_assumed_when_square_on():
    quad = project(rectangle(2.0), 0, 0)
    _, recovered = geo.estimate_focal_length(quad, W, H)
    assert recovered is False


def test_min_area_quad_recovers_projected_rectangle():
    quad = project(rectangle(1.6), 25, 10)
    noisy = np.vstack([quad, quad.mean(axis=0)])  # an interior stray point
    fitted = geo.min_area_quad(noisy)
    assert np.abs(np.sort(fitted, axis=0) - np.sort(quad, axis=0)).max() < 2.0


def test_subtract_children_deducts_openings():
    wall = [[0, 0], [10, 0], [10, 10], [0, 10]]           # 100 sq units
    windows = [
        [[1, 1], [3, 1], [3, 3], [1, 3]],                  # 4
        [[6, 6], [8, 6], [8, 8], [6, 8]],                  # 4
    ]
    _, retained = geo.subtract_children(wall, windows)
    assert retained == pytest.approx(0.92)


def test_subtract_children_without_openings_is_identity():
    wall = [[0, 0], [4, 0], [4, 4], [0, 4]]
    _, retained = geo.subtract_children(wall, [])
    assert retained == pytest.approx(1.0)


def test_fill_fraction_of_triangle_in_its_quad():
    triangle = [[0, 0], [10, 0], [0, 10]]
    quad = geo.min_area_quad(np.array(triangle, dtype=float))
    assert geo.fill_fraction(triangle, quad) == pytest.approx(0.5, abs=0.05)


def test_normalisation_round_trip():
    polygon = [[0.1, 0.2], [0.8, 0.25], [0.75, 0.9], [0.15, 0.85]]
    pixels = geo.to_pixels(polygon, 1000, 800)
    back = geo.to_normalised(pixels, 1000, 800)
    assert np.allclose(np.array(back), np.array(polygon), atol=1e-9)
