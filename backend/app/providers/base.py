"""Provider contracts.

Region detection and photoreal rendering are the two places where a hosted AI
model could do better than local code. Both sit behind these protocols so the
offline implementation is the default and a Gemini implementation can be
dropped in without any router or service knowing the difference.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence, runtime_checkable

import numpy as np

from ..enums import RegionCategory, RegionSource


@dataclass
class DetectedRegion:
    label: str
    category: RegionCategory
    #: Normalised 0..1 polygon.
    polygon: list[list[float]]
    confidence: float = 0.5
    source: RegionSource = RegionSource.CV
    #: Index into the same result list; set on openings that sit on a wall.
    parent_index: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class SegmentationProvider(Protocol):
    name: str

    def available(self) -> bool: ...

    def detect(self, bgr: np.ndarray) -> list[DetectedRegion]: ...


@runtime_checkable
class RenderProvider(Protocol):
    name: str

    def available(self) -> bool: ...

    def render(
        self,
        base_bgr: np.ndarray,
        composite_bgr: np.ndarray,
        instructions: Sequence[str],
    ) -> np.ndarray: ...
