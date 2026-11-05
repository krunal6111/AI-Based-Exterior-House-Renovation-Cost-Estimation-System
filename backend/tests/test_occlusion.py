"""Windows and doors must be cut out of their wall automatically.

Before this, a window was only excluded from a wall's area/render when the
user had explicitly set its `parent_id` via the "cut out of which surface?"
dropdown -- a UI affordance almost nobody finds. A real user's photo showed
exactly the failure this implies: a wall repaint rendered straight over its
windows and door. `overlapping_openings` fixes this by also recognising an
opening purely from geometric overlap, with the explicit link kept as a
belt-and-braces override for cases geometry alone would miss.
"""

from __future__ import annotations

import pytest

from app.enums import MeasureMode, RegionCategory
from app.models import Calibration, Project, Region
from app.services.estimator import measure_project, overlapping_openings


def make_region(
    id: str, category: str, polygon: list[list[float]], parent_id: str | None = None
) -> Region:
    region = Region(
        id=id,
        project_id="p1",
        name=id,
        category=category,
        polygon=polygon,
        parent_id=parent_id,
        source="manual",
        confidence=1.0,
        user_edited=True,
    )
    return region


def box(x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


@pytest.fixture
def project_with_unlinked_window() -> Project:
    """A wall with a window fully inside it, never linked via parent_id --
    exactly the common case a real user produces without using the dropdown."""
    project = Project(id="p1", owner_token="t")
    wall = make_region("wall", RegionCategory.WALL.value, box(0.1, 0.1, 0.9, 0.9))
    window = make_region("window", RegionCategory.WINDOW.value, box(0.3, 0.3, 0.5, 0.5))
    door = make_region("door", RegionCategory.DOOR.value, box(0.6, 0.6, 0.75, 0.85))
    project.regions = [wall, window, door]
    return project


def test_unlinked_opening_is_found_by_overlap(project_with_unlinked_window):
    wall = project_with_unlinked_window.regions[0]
    found = overlapping_openings(project_with_unlinked_window, wall)
    assert {r.id for r in found} == {"window", "door"}


def test_explicit_parent_link_is_still_honoured():
    """A sliver overlap wouldn't clear the geometric threshold on its own,
    so the explicit link has to be what makes it count."""
    project = Project(id="p1", owner_token="t")
    wall = make_region("wall", RegionCategory.WALL.value, box(0.0, 0.0, 1.0, 1.0))
    # Only 4% of this window's own area actually overlaps the wall.
    sliver = make_region(
        "window", RegionCategory.WINDOW.value, box(0.98, 0.0, 1.5, 0.5), parent_id="wall"
    )
    project.regions = [wall, sliver]

    found = overlapping_openings(project, wall)
    assert {r.id for r in found} == {"window"}


def test_barely_overlapping_unlinked_opening_is_not_swept_in():
    project = Project(id="p1", owner_token="t")
    wall = make_region("wall", RegionCategory.WALL.value, box(0.0, 0.0, 1.0, 1.0))
    sliver = make_region("window", RegionCategory.WINDOW.value, box(0.98, 0.0, 1.5, 0.5))
    project.regions = [wall, sliver]

    assert overlapping_openings(project, wall) == []


def test_non_opening_regions_never_become_occluders():
    """A pillar overlapping a wall should not be cut out of it."""
    project = Project(id="p1", owner_token="t")
    wall = make_region("wall", RegionCategory.WALL.value, box(0.0, 0.0, 1.0, 1.0))
    pillar = make_region("pillar", RegionCategory.PILLAR.value, box(0.4, 0.0, 0.5, 1.0))
    project.regions = [wall, pillar]

    assert overlapping_openings(project, wall) == []


def test_measured_wall_area_deducts_unlinked_openings():
    """End-to-end: the bug a real upload reproduced was overstated (and then
    over-painted) wall area because nobody had linked the windows."""
    project = Project(id="p1", owner_token="t")
    project.calibration = Calibration(
        project_id="p1",
        ref_kind="custom",
        ref_line=[[0.1, 0.1], [0.1, 0.9]],
        ref_real_ft=16.0,  # 0.8 of the 1000px image height == 16 ft tall wall
        confirmed=True,
    )
    wall = make_region("wall", RegionCategory.WALL.value, box(0.1, 0.1, 0.9, 0.9))
    window = make_region("window", RegionCategory.WINDOW.value, box(0.3, 0.3, 0.5, 0.5))
    project.regions = [wall, window]

    measurements = measure_project(project, 1000, 1000)
    wall_measurement = measurements.by_region["wall"]
    window_measurement = measurements.by_region["window"]

    assert wall.category_enum.measure is MeasureMode.AREA
    # 4% of the wall's own bounding square is the window -> ~96% retained.
    assert any("deducted" in note for note in wall_measurement.notes)
    assert wall_measurement.area_sqft < 16.0 * 16.0 * 0.97
    assert window_measurement.area_sqft > 0
