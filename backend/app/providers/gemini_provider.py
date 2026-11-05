"""Optional Gemini providers (requirement: nice-to-have, never required).

Everything here is dormant unless a credential is configured *and* the
matching feature flag is on. Two credential paths work, either is enough:

* ``GEMINI_API_KEY`` -- AI Studio, simplest to get, has a free tier for the
  text/flash models.
* ``VERTEX_PROJECT`` + ``GOOGLE_APPLICATION_CREDENTIALS`` (a service-account
  JSON key, set as an environment variable rather than a setting here so it
  never ends up in this repo) -- Vertex AI, needed if you're already on GCP.

The offline providers remain the default, so the system has no hard
dependency on a paid API or a network connection.

Two notes on the API surface, both verified against the current docs:

* The SDK entry point is the **Interactions API** (``client.interactions.create``,
  ``google-genai >= 2.30.1``), not the older ``generate_content``.
* Google's own segmentation example is self-contradictory about whether the
  ``mask`` field is a base64 PNG or a polygon. Rather than depend on that, we
  declare **our own** response schema asking for explicit polygons, which is
  what the editor and the area maths want anyway.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any, Sequence

import cv2
import numpy as np

from ..config import settings
from ..enums import RegionCategory, RegionSource
from ..services import geometry as geo
from .base import DetectedRegion

log = logging.getLogger(__name__)

_VALID_CATEGORIES = [c.value for c in RegionCategory]


def _client():
    """Build a client for whichever auth path is configured, or None.

    Vertex AI only answers Interactions API calls on the "global", "us" or
    "eu" locations -- a regional location like "us-central1" 400s even
    though the model shows up fine in that region's catalog listing. This
    tripped up initial testing enough that it is worth this comment.
    """
    backend = settings.ai_backend
    if backend is None:
        return None
    try:
        from google import genai
    except ImportError:
        log.warning(
            "A Gemini credential is set but google-genai is not installed. "
            "Run: pip install -r requirements-ai.txt"
        )
        return None

    try:
        if backend == "vertex":
            return genai.Client(
                vertexai=True,
                project=settings.vertex_project,
                location=settings.vertex_location,
            )
        return genai.Client(api_key=settings.gemini_api_key)
    except Exception as exc:  # pragma: no cover - credential/network issues
        log.warning("Could not create the Gemini client (%s backend): %s", backend, exc)
        return None


def _encode_jpeg(bgr: np.ndarray, max_edge: int = 1400) -> str:
    """BGR array -> base64 JPEG.

    JPEG specifically: Gemini accepts PNG/JPEG/WEBP/HEIC/HEIF but **not** AVIF,
    and one of this project's own sample photos is an AVIF.
    """
    h, w = bgr.shape[:2]
    longest = max(h, w)
    if longest > max_edge:
        scale = max_edge / longest
        bgr = cv2.resize(
            bgr,
            (max(1, int(w * scale)), max(1, int(h * scale))),
            interpolation=cv2.INTER_AREA,
        )
    ok, buffer = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        raise RuntimeError("Could not encode the image as JPEG")
    return base64.b64encode(buffer.tobytes()).decode("ascii")


# ---------------------------------------------------------------------------
# segmentation
# ---------------------------------------------------------------------------

_SEGMENTATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "surfaces": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "Short human label, e.g. 'Left main wall'.",
                    },
                    "category": {
                        "type": "string",
                        "enum": _VALID_CATEGORIES,
                        "description": "Which structural component this is.",
                    },
                    "polygon": {
                        "type": "array",
                        "description": (
                            "Outline as [x, y] pairs, each 0-1000, x from the "
                            "left edge and y from the top edge. 4 to 10 points, "
                            "in order around the shape."
                        ),
                        "items": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "minItems": 2,
                            "maxItems": 2,
                        },
                        "minItems": 3,
                        "maxItems": 10,
                    },
                    "confidence": {"type": "number"},
                },
                "required": ["label", "category", "polygon", "confidence"],
            },
            "maxItems": 14,
        }
    },
    "required": ["surfaces"],
}

_SEGMENTATION_PROMPT = """\
You are helping measure a house for an exterior renovation quote.

Identify the distinct exterior SURFACES of the building in this photograph:
main walls, windows, doors, balconies, pillars/columns, parapet walls, railings,
roof edges, gates, plinth bands and sunshades.

Rules:
- Only outline parts of the building itself. Never outline sky, trees, lawn,
  mulch, paving, vehicles or people.
- Give each surface as a polygon tracing its visible outline, with coordinates
  scaled 0-1000 across the image width and height.
- Treat each flat plane as its own surface. A wall facing left and a wall facing
  front are two surfaces, not one.
- Outline windows and doors separately from the wall they sit in.
- Do not outline the roof covering itself; it is not being renovated.
- Prefer a few accurate surfaces over many sloppy ones.
"""


class GeminiSegmentationProvider:
    name = "gemini"

    def available(self) -> bool:
        return settings.ai_segmentation_available and _client() is not None

    def detect(self, bgr: np.ndarray) -> list[DetectedRegion]:
        client = _client()
        if client is None:
            return []

        try:
            interaction = client.interactions.create(
                model=settings.ai_segmentation_model,
                input=[
                    {"type": "text", "text": _SEGMENTATION_PROMPT},
                    {
                        "type": "image",
                        "data": _encode_jpeg(bgr),
                        "mime_type": "image/jpeg",
                    },
                ],
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": _SEGMENTATION_SCHEMA,
                },
                # The docs recommend minimal thinking for spatial tasks, but
                # Vertex AI rejects "minimal" on this model while accepting
                # "low" -- see settings.ai_thinking_level.
                generation_config={"thinking_level": settings.ai_thinking_level},
            )
            payload = json.loads(interaction.output_text)
        except Exception as exc:
            log.warning("Gemini segmentation failed, falling back offline: %s", exc)
            return []

        regions: list[DetectedRegion] = []
        for item in payload.get("surfaces", []):
            polygon = item.get("polygon") or []
            if len(polygon) < 3:
                continue
            try:
                normalised = [
                    [
                        min(1.0, max(0.0, float(x) / 1000.0)),
                        min(1.0, max(0.0, float(y) / 1000.0)),
                    ]
                    for x, y in polygon
                ]
            except (TypeError, ValueError):
                continue

            try:
                category = RegionCategory(item.get("category", "other"))
            except ValueError:
                category = RegionCategory.OTHER

            regions.append(
                DetectedRegion(
                    label=str(item.get("label") or category.label)[:120],
                    category=category,
                    polygon=geo.simplify_polygon(normalised, tolerance=0.004),
                    confidence=float(item.get("confidence", 0.7)),
                    source=RegionSource.AI,
                    meta={"provider": "gemini"},
                )
            )

        _nest_openings(regions)
        return regions


def _nest_openings(regions: list[DetectedRegion]) -> None:
    """Attach each opening to the wall it overlaps most."""
    from shapely.geometry import Polygon as ShapelyPolygon
    from shapely.validation import make_valid

    walls = [
        (i, make_valid(ShapelyPolygon(r.polygon)))
        for i, r in enumerate(regions)
        if r.category is RegionCategory.WALL and len(r.polygon) >= 3
    ]
    for i, region in enumerate(regions):
        if not region.category.is_opening or len(region.polygon) < 3:
            continue
        shape = make_valid(ShapelyPolygon(region.polygon))
        if shape.is_empty or shape.area <= 0:
            continue
        best, best_overlap = None, 0.0
        for wall_index, wall in walls:
            if wall_index == i or wall.is_empty:
                continue
            try:
                overlap = shape.intersection(wall).area / shape.area
            except Exception:  # pragma: no cover
                continue
            if overlap > best_overlap:
                best, best_overlap = wall_index, overlap
        if best is not None and best_overlap > 0.35:
            region.parent_index = best


# ---------------------------------------------------------------------------
# photoreal finishing pass
# ---------------------------------------------------------------------------


class GeminiRenderProvider:
    """Optional 'HD render' pass over the offline composite.

    Deliberately conditioned on BOTH the original photo and our own composite,
    and told in plain terms not to move anything. The offline render already
    places the right material in the right place with the right lighting; the
    model is only asked to clean up edges and micro-detail. Used the other way
    round -- as a free-form generator -- it would quietly redraw the building,
    which is exactly what requirement 5.4 forbids.
    """

    name = "gemini-image"

    def available(self) -> bool:
        return settings.ai_render_available and _client() is not None

    def render(
        self,
        base_bgr: np.ndarray,
        composite_bgr: np.ndarray,
        instructions: Sequence[str] = (),
    ) -> np.ndarray:
        client = _client()
        if client is None:
            return composite_bgr

        prompt = (
            "The second image is a proposed renovation of the house in the first "
            "image: the same photograph with new surface materials composited onto "
            "it. Produce a photorealistic version of the second image.\n\n"
            "Absolute constraints:\n"
            "- Do NOT change the building's shape, size, roofline, window or door "
            "positions, or the camera viewpoint.\n"
            "- Do NOT change the sky, trees, garden, paving or any background.\n"
            "- Keep every material exactly where it has been placed, in the same "
            "colour.\n"
            "- Only improve realism: material grain, edge quality where surfaces "
            "meet trim, and how the existing light falls on the new finishes.\n"
        )
        if instructions:
            prompt += "\nMaterials applied:\n" + "\n".join(f"- {i}" for i in instructions)

        try:
            interaction = client.interactions.create(
                model=settings.ai_render_model,
                input=[
                    {"type": "text", "text": prompt},
                    {
                        "type": "image",
                        "data": _encode_jpeg(base_bgr, 1600),
                        "mime_type": "image/jpeg",
                    },
                    {
                        "type": "image",
                        "data": _encode_jpeg(composite_bgr, 1600),
                        "mime_type": "image/jpeg",
                    },
                ],
                response_format={
                    "type": "image",
                    "mime_type": "image/jpeg",
                    "image_size": "2K",
                },
            )
            raw = base64.b64decode(interaction.output_image.data)
        except Exception as exc:
            log.warning("Gemini render failed, keeping the offline composite: %s", exc)
            return composite_bgr

        decoded = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if decoded is None:
            log.warning("Gemini returned an image that could not be decoded")
            return composite_bgr

        # Match the offline composite's size so downstream code is unaffected.
        h, w = composite_bgr.shape[:2]
        if decoded.shape[:2] != (h, w):
            decoded = cv2.resize(decoded, (w, h), interpolation=cv2.INTER_LANCZOS4)
        return decoded
