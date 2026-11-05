"""Input quality gate (requirement 5.1).

The brief asks the system to "reject extremely low-quality input" *and* to
"guide the user if the image is not usable". Those are different jobs: almost
everything here produces advice rather than a rejection, because a homeowner
with one usable-but-soft photo is better served by a warning than a wall.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from .catalog import quality_thresholds
from .scene import scene_masks


@dataclass
class Issue:
    code: str
    severity: str  # "error" | "warning" | "info"
    message: str
    guidance: str


@dataclass
class QualityReport:
    usable: bool
    score: float  # 0..100
    width: int
    height: int
    sharpness: float
    mean_luma: float
    clipped_fraction: float
    building_fraction: float
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    def as_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return asdict(self)


def assess(
    bgr: np.ndarray, *, check_building: bool = True, masks=None
) -> QualityReport:
    """Grade an uploaded photo. Pass ``masks`` to reuse an existing
    :func:`~app.services.scene.scene_masks` result instead of recomputing it."""
    t = quality_thresholds()
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    mean_luma = float(gray.mean())
    clipped = float(((gray <= 4) | (gray >= 251)).mean())

    building_fraction = 0.0
    if check_building:
        try:
            scene = masks if masks is not None else scene_masks(bgr)
            building_fraction = scene.building_fraction
        except Exception:  # pragma: no cover - never block an upload on this
            building_fraction = 0.0

    issues: list[Issue] = []
    min_edge = min(w, h)

    # --- resolution ----------------------------------------------------
    if min_edge < int(t.get("min_edge_px", 480)):
        issues.append(
            Issue(
                "resolution_too_low",
                "error",
                f"This image is only {w}x{h} pixels.",
                "Upload a photo at least 640 px on its shortest side. A photo "
                "straight from any phone camera will do.",
            )
        )
    elif min_edge < int(t.get("warn_edge_px", 640)):
        issues.append(
            Issue(
                "resolution_low",
                "warning",
                f"{w}x{h} is on the small side.",
                "Detection and area estimates will be rougher than usual. A "
                "larger photo would improve both.",
            )
        )

    # --- focus ----------------------------------------------------------
    blur_min = float(t.get("blur_laplacian_var_min", 80.0))
    blur_warn = float(t.get("blur_warn_var", 150.0))
    if sharpness < blur_min:
        issues.append(
            Issue(
                "blurry",
                "error",
                f"The photo looks out of focus (sharpness {sharpness:.0f}).",
                "Retake it holding the phone still, tapping the wall to focus "
                "before you shoot.",
            )
        )
    elif sharpness < blur_warn:
        issues.append(
            Issue(
                "soft_focus",
                "warning",
                f"The photo is a little soft (sharpness {sharpness:.0f}).",
                "Usable, but edges of windows and railings may be detected "
                "less precisely.",
            )
        )

    # --- exposure ---------------------------------------------------------
    luma_min = float(t.get("luma_min", 40.0))
    luma_max = float(t.get("luma_max", 215.0))
    if mean_luma < luma_min:
        issues.append(
            Issue(
                "underexposed",
                "warning",
                f"The photo is very dark (average brightness {mean_luma:.0f}/255).",
                "Shoot in daylight with the sun behind you. Dark photos hide "
                "the shading the renderer needs.",
            )
        )
    elif mean_luma > luma_max:
        issues.append(
            Issue(
                "overexposed",
                "warning",
                f"The photo is very bright (average brightness {mean_luma:.0f}/255).",
                "Avoid shooting straight into the sun; blown-out walls lose "
                "the texture that makes a render look real.",
            )
        )

    if clipped > float(t.get("clipped_fraction_max", 0.25)):
        issues.append(
            Issue(
                "clipped",
                "warning",
                f"{clipped * 100:.0f}% of the image is pure black or pure white.",
                "Try an overcast day or early morning light for an even "
                "exposure across the facade.",
            )
        )

    # --- is there a building? ----------------------------------------------
    min_building = float(t.get("min_building_fraction", 0.20))
    if check_building and building_fraction < min_building:
        issues.append(
            Issue(
                "building_too_small",
                "warning",
                f"The building seems to fill only about "
                f"{building_fraction * 100:.0f}% of the frame.",
                "Step closer or crop the photo so the facade fills most of "
                "the image. Lots of sky and garden reduces measurement accuracy.",
            )
        )

    # --- score -------------------------------------------------------------
    score = 100.0
    score -= min(30.0, max(0.0, (blur_warn - sharpness) / blur_warn * 30.0))
    score -= min(20.0, max(0.0, (640 - min_edge) / 640 * 20.0))
    score -= min(15.0, abs(mean_luma - 128.0) / 128.0 * 15.0)
    score -= min(15.0, clipped * 60.0)
    if check_building:
        score -= min(20.0, max(0.0, (0.35 - building_fraction) / 0.35 * 20.0))
    score = float(max(0.0, min(100.0, score)))

    usable = not any(i.severity == "error" for i in issues)

    if usable and not issues:
        issues.append(
            Issue(
                "ok",
                "info",
                "Good photo - sharp, well exposed and well framed.",
                "",
            )
        )

    return QualityReport(
        usable=usable,
        score=round(score, 1),
        width=w,
        height=h,
        sharpness=round(sharpness, 1),
        mean_luma=round(mean_luma, 1),
        clipped_fraction=round(clipped, 4),
        building_fraction=round(building_fraction, 4),
        issues=issues,
    )
