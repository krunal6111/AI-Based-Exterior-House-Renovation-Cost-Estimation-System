"""Swappable detection / rendering backends.

The offline implementations are the default and the only ones guaranteed to
exist. Gemini is consulted first *only* when a key and the matching flag are
present, and it always falls back to offline on any failure.
"""

from __future__ import annotations

import logging

import numpy as np

from .base import DetectedRegion, RenderProvider, SegmentationProvider
from .cv_provider import CvSegmentationProvider

log = logging.getLogger(__name__)

__all__ = [
    "DetectedRegion",
    "SegmentationProvider",
    "RenderProvider",
    "CvSegmentationProvider",
    "detect_regions",
    "segmentation_backend",
]

_offline = CvSegmentationProvider()


def _gemini_segmenter():
    try:
        from .gemini_provider import GeminiSegmentationProvider
    except Exception:  # pragma: no cover - optional dependency
        return None
    provider = GeminiSegmentationProvider()
    return provider if provider.available() else None


def segmentation_backend() -> str:
    return "gemini" if _gemini_segmenter() else _offline.name


def detect_regions(bgr: np.ndarray) -> tuple[list[DetectedRegion], str]:
    """Detect surfaces, preferring Gemini when it is switched on.

    Returns ``(regions, backend_name)``. An empty or failed AI result silently
    degrades to the offline detector rather than leaving the user with nothing.
    """
    provider = _gemini_segmenter()
    if provider is not None:
        try:
            regions = provider.detect(bgr)
            if regions:
                return regions, provider.name
            log.info("Gemini returned no surfaces; using the offline detector")
        except Exception as exc:  # pragma: no cover - network/credential issues
            log.warning("Gemini segmentation error (%s); using offline", exc)

    return _offline.detect(bgr), _offline.name
