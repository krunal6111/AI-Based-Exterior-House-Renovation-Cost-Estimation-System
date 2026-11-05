"""Imaging, measurement and the HTTP surface, exercised end to end.

These run against the real sample photographs in ``house_images/`` so the
awkward formats (AVIF, JFIF) are covered by the test suite rather than by
hope.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.enums import AreaMethod, RegionCategory
from app.services import image_io
from app.services.quality import assess
from app.services.scale import measure_region, solve_scale
from app.services.scene import scene_masks

SAMPLES = Path(__file__).resolve().parents[2] / "house_images"
GOOD_PHOTO = SAMPLES / "west-virginia-gray-cottage-64dd6bb056057.avif"


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as test_client:
        test_client.headers.update({"X-Owner-Token": "pytest-owner"})
        yield test_client


def square(x0=0.2, y0=0.2, x1=0.8, y1=0.8):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


# ----------------------------------------------------------- image io ----


@pytest.mark.skipif(not SAMPLES.exists(), reason="sample photos not present")
@pytest.mark.parametrize(
    "name", [p.name for p in SAMPLES.iterdir() if p.is_file()] if SAMPLES.exists() else []
)
def test_every_sample_format_normalises(name, tmp_path):
    """AVIF and JFIF must come through as plain RGB JPEG."""
    source = SAMPLES / name
    working = image_io.build_working_set(source.read_bytes(), tmp_path / name)

    assert working.original_path.exists()
    assert working.display_path.exists()
    assert working.width > 0 and working.height > 0
    assert max(working.display_width, working.display_height) <= 1400

    bgr = image_io.read_bgr(working.original_path)
    assert bgr.ndim == 3 and bgr.shape[2] == 3
    assert bgr.dtype == np.uint8


def test_garbage_bytes_are_rejected_clearly(tmp_path):
    with pytest.raises(image_io.UnsupportedImage) as excinfo:
        image_io.build_working_set(b"this is not an image", tmp_path)
    assert "could not be read" in str(excinfo.value).lower()


def test_thumbnail_is_bounded(tmp_path):
    source = tmp_path / "src.jpg"
    image_io.save_bgr(np.full((1200, 1600, 3), 128, np.uint8), source)
    out = image_io.make_thumbnail(source, tmp_path / "small.jpg", 400)
    bgr = image_io.read_bgr(out)
    assert max(bgr.shape[:2]) == 400


# ------------------------------------------------------------- quality ----


def test_tiny_image_is_rejected_with_guidance():
    report = assess(np.full((200, 200, 3), 128, np.uint8), check_building=False)
    assert report.usable is False
    codes = {i.code for i in report.issues}
    assert "resolution_too_low" in codes
    assert all(i.guidance for i in report.issues if i.severity == "error")


def test_flat_grey_image_is_flagged_as_blurry():
    report = assess(np.full((900, 1200, 3), 128, np.uint8), check_building=False)
    assert "blurry" in {i.code for i in report.issues}


@pytest.mark.skipif(not GOOD_PHOTO.exists(), reason="sample photo not present")
def test_real_photo_passes_the_gate(tmp_path):
    working = image_io.build_working_set(GOOD_PHOTO.read_bytes(), tmp_path)
    report = assess(image_io.read_bgr(working.original_path))
    assert report.usable is True
    assert report.score > 70


# --------------------------------------------------------------- scene ----


@pytest.mark.skipif(not GOOD_PHOTO.exists(), reason="sample photo not present")
def test_scene_masks_are_resolution_invariant(tmp_path):
    """Fixed-pixel morphology would otherwise make results depend on upload size."""
    from PIL import Image

    image_io.register_openers()
    with Image.open(GOOD_PHOTO) as img:
        rgb = img.convert("RGB")

    fractions = []
    for edge in (700, 1100, 1700):
        resized = image_io._fit(rgb, edge)
        masks = scene_masks(image_io.to_bgr(resized))
        fractions.append(masks.building_fraction)

    assert max(fractions) - min(fractions) < 0.12, fractions
    assert all(0.1 < f < 0.9 for f in fractions)


# ---------------------------------------------------------- measurement ----


def test_scale_from_a_reference_line():
    # A door spanning 20% of a 2000 px tall image, 6.9 ft in reality.
    scale = solve_scale([[0.5, 0.4], [0.5, 0.6]], 6.9, 1000, 2000, confirmed=True)
    assert scale is not None
    assert scale.ft_per_px == pytest.approx(6.9 / 400.0)
    assert scale.method is AreaMethod.REFERENCE_OBJECT


def test_scale_needs_two_usable_points():
    assert solve_scale([[0.5, 0.5]], 6.9, 1000, 1000) is None
    assert solve_scale([[0.5, 0.5], [0.5, 0.5]], 6.9, 1000, 1000) is None
    assert solve_scale([[0.1, 0.1], [0.9, 0.9]], 0, 1000, 1000) is None


def test_measured_area_matches_the_reference():
    """A wall exactly as tall as a 10 ft reference, square-on and square."""
    scale = solve_scale([[0.1, 0.2], [0.1, 0.8]], 10.0, 1000, 1000, confirmed=True)
    assert scale is not None

    measurement = measure_region(
        square(0.2, 0.2, 0.8, 0.8), RegionCategory.WALL, 1000, 1000, scale
    )
    # The region is 600 px square; the reference was 600 px == 10 ft.
    assert measurement.height_ft == pytest.approx(10.0, rel=0.02)
    assert measurement.width_ft == pytest.approx(10.0, rel=0.05)
    assert measurement.area_sqft == pytest.approx(100.0, rel=0.08)
    assert measurement.method is AreaMethod.REFERENCE_OBJECT


def test_user_dimension_beats_estimation():
    scale = solve_scale([[0.1, 0.2], [0.1, 0.8]], 10.0, 1000, 1000, confirmed=True)
    measurement = measure_region(
        square(), RegionCategory.WALL, 1000, 1000, scale,
        override_width_ft=24.0, override_height_ft=9.0,
    )
    assert measurement.area_sqft == pytest.approx(216.0)
    assert measurement.method is AreaMethod.USER_DIMENSION
    assert measurement.error_low < 0.1


def test_direct_area_override_wins_outright():
    scale = solve_scale([[0.1, 0.2], [0.1, 0.8]], 10.0, 1000, 1000, confirmed=True)
    measurement = measure_region(
        square(), RegionCategory.WALL, 1000, 1000, scale, override_area_sqft=777.0
    )
    assert measurement.area_sqft == pytest.approx(777.0)
    assert measurement.method is AreaMethod.USER_DIMENSION


def test_openings_are_deducted():
    scale = solve_scale([[0.1, 0.2], [0.1, 0.8]], 10.0, 1000, 1000, confirmed=True)
    full = measure_region(square(), RegionCategory.WALL, 1000, 1000, scale)
    with_hole = measure_region(
        square(), RegionCategory.WALL, 1000, 1000, scale, retained_fraction=0.75
    )
    assert with_hole.area_sqft == pytest.approx(full.area_sqft * 0.75, rel=1e-6)
    assert any("deducted" in note for note in with_hole.notes)


def test_railing_measured_as_length_not_area():
    scale = solve_scale([[0.1, 0.2], [0.1, 0.8]], 10.0, 1000, 1000, confirmed=True)
    measurement = measure_region(
        square(0.1, 0.5, 0.9, 0.6), RegionCategory.RAILING, 1000, 1000, scale
    )
    assert measurement.length_ft > 0
    assert measurement.area_sqft == 0


def test_no_scale_yields_no_measurement():
    measurement = measure_region(square(), RegionCategory.WALL, 1000, 1000, None)
    assert measurement.area_sqft == 0
    assert any("No scale" in note for note in measurement.notes)


# ------------------------------------------------------------------ api ----


def test_health_reports_well_formed_status(client):
    """The live process may legitimately have AI configured via a developer's
    own backend/.env (Vertex AI or AI Studio) -- that is not a regression, so
    this only checks the response shape, not a specific value. The actual
    "offline unless configured" invariant is a property of the Settings
    class's defaults, checked in isolation below.
    """
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert isinstance(body["offline_mode"], bool)
    assert isinstance(body["ai_render_available"], bool)
    # AI render is never auto-enabled by AI segmentation being on, or vice
    # versa -- each needs its own explicit flag.
    if not body["offline_mode"]:
        assert body["ai_segmentation_available"] or body["ai_render_available"]


def test_settings_default_to_fully_offline():
    """With no .env and no environment variables at all, every AI path is
    off. This is the actual default-offline guarantee; it is a property of
    the Settings class, not of whatever a developer's own machine has
    configured for themselves.
    """
    from app.config import Settings

    fresh = Settings(_env_file=None)  # type: ignore[call-arg]
    assert fresh.ai_backend is None
    assert fresh.ai_configured is False
    assert fresh.ai_segmentation_available is False
    assert fresh.ai_render_available is False


def test_catalog_is_complete(client):
    body = client.get("/api/catalog").json()
    assert len(body["materials"]) >= 10
    assert body["currency"] == "INR"
    assert body["limitations"]
    assert any(m["assignable"] for m in body["materials"])


def test_upload_rejects_non_images(client):
    response = client.post(
        "/api/projects",
        files={"file": ("notes.txt", b"hello", "text/plain")},
        data={"name": "Bad upload"},
    )
    assert response.status_code == 415


@pytest.mark.skipif(not GOOD_PHOTO.exists(), reason="sample photo not present")
def test_full_journey(client):
    """Upload -> surfaces -> scale -> materials -> render -> estimate -> PDF."""
    response = client.post(
        "/api/projects",
        files={"file": (GOOD_PHOTO.name, GOOD_PHOTO.read_bytes(), "image/avif")},
        data={"name": "Journey test"},
    )
    assert response.status_code == 201
    project = response.json()
    project_id = project["id"]

    assert project["quality"]["usable"] is True
    assert project["regions"], "detection produced no starting surfaces"
    assert project["calibration"] is not None, "no reference was pre-placed"
    assert project["calibration"]["confirmed"] is False

    # Confirming the scale must move every region off the rough tier.
    calibration = project["calibration"]
    response = client.put(
        f"/api/projects/{project_id}/calibration",
        json={
            "ref_kind": "door",
            "ref_line": calibration["ref_line"],
            "ref_real_ft": 6.9,
            "confirmed": True,
        },
    )
    assert response.status_code == 200
    project = response.json()
    methods = {
        r["measurement"]["method"] for r in project["regions"] if r["measurement"]
    }
    assert methods == {"reference_object"}

    # Apply a material to the largest surface.
    walls = sorted(
        (r for r in project["regions"] if (r["measurement"] or {}).get("area_sqft", 0) > 0),
        key=lambda r: -r["measurement"]["area_sqft"],
    )
    assert walls, "no measurable surface found"
    response = client.put(
        f"/api/projects/{project_id}/assignments",
        json={
            "name": "Option A",
            "assignments": [
                {"region_id": walls[0]["id"], "material_id": "paint_premium", "params": {}}
            ],
        },
    )
    assert response.status_code == 200

    render = client.post(f"/api/projects/{project_id}/render")
    assert render.status_code == 200
    assert render.json()["layers_applied"] == 1
    assert render.json()["engine"] == "offline-cv"

    estimate = client.get(f"/api/projects/{project_id}/estimate").json()
    assert estimate["grand_total"] > 0
    assert estimate["range_low"] < estimate["grand_total"] < estimate["range_high"]
    # Premium paint pulls in primer and putty automatically.
    assert {q["material_id"] for q in estimate["quantities"]} >= {
        "paint_premium", "primer_exterior", "putty_exterior",
    }

    pdf = client.get(f"/api/projects/{project_id}/report.pdf")
    assert pdf.status_code == 200
    assert pdf.content[:5] == b"%PDF-"
    assert len(pdf.content) > 20_000

    client.delete(f"/api/projects/{project_id}")


def test_projects_are_isolated_per_owner(client):
    created = client.post(
        "/api/projects",
        files={"file": ("x.jpg", _tiny_jpeg(), "image/jpeg")},
        data={"name": "Mine"},
    )
    assert created.status_code == 201
    project_id = created.json()["id"]

    stranger = client.get(
        f"/api/projects/{project_id}", headers={"X-Owner-Token": "someone-else"}
    )
    assert stranger.status_code == 404

    listing = client.get("/api/projects", headers={"X-Owner-Token": "someone-else"})
    assert listing.json() == []

    client.delete(f"/api/projects/{project_id}")


def _tiny_jpeg() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (700, 700), (120, 120, 120)).save(buffer, "JPEG")
    return buffer.getvalue()
