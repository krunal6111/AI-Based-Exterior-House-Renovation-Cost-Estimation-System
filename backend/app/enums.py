"""Shared vocabulary for the whole pipeline.

Kept free of SQLAlchemy/Pydantic imports so the pure estimation services can
use these without dragging in the web stack.
"""

from __future__ import annotations

from enum import Enum


class RegionCategory(str, Enum):
    """Structural components the system identifies (requirement 5.2)."""

    WALL = "wall"
    WINDOW = "window"
    DOOR = "door"
    BALCONY = "balcony"
    PILLAR = "pillar"
    PARAPET = "parapet"
    RAILING = "railing"
    ROOF = "roof"
    ROOF_EDGE = "roof_edge"
    GATE = "gate"
    PLINTH = "plinth"
    SUNSHADE = "sunshade"
    OTHER = "other"

    @property
    def label(self) -> str:
        return _CATEGORY_LABELS[self]

    @property
    def measure(self) -> "MeasureMode":
        return (
            MeasureMode.LENGTH
            if self in _LINEAR_CATEGORIES
            else MeasureMode.AREA
        )

    @property
    def is_opening(self) -> bool:
        """Openings are subtracted from the wall they sit on."""
        return self in _OPENING_CATEGORIES


_CATEGORY_LABELS: dict[RegionCategory, str] = {
    RegionCategory.WALL: "Main wall",
    RegionCategory.WINDOW: "Window",
    RegionCategory.DOOR: "Door",
    RegionCategory.BALCONY: "Balcony",
    RegionCategory.PILLAR: "Pillar / column",
    RegionCategory.PARAPET: "Parapet wall",
    RegionCategory.RAILING: "Railing",
    RegionCategory.ROOF: "Roof covering",
    RegionCategory.ROOF_EDGE: "Roof edge / coping",
    RegionCategory.GATE: "Gate area",
    RegionCategory.PLINTH: "Plinth / skirting band",
    RegionCategory.SUNSHADE: "Sunshade / chajja",
    RegionCategory.OTHER: "Other surface",
}

#: Measured in running feet rather than square feet.
_LINEAR_CATEGORIES = {RegionCategory.RAILING, RegionCategory.ROOF_EDGE}

#: Cut out of their parent wall so wall area is never double-counted.
_OPENING_CATEGORIES = {
    RegionCategory.WINDOW,
    RegionCategory.DOOR,
    RegionCategory.GATE,
}


class MeasureMode(str, Enum):
    AREA = "area"
    LENGTH = "length"


class AreaMethod(str, Enum):
    """How a region's real-world size was arrived at.

    The tiers carry very different confidence, and the UI/report always show
    which one was used -- an estimate that hides its own uncertainty is worse
    than useless for budgeting.
    """

    USER_DIMENSION = "user_dimension"      # Tier A: user typed a real dimension
    REFERENCE_OBJECT = "reference_object"  # Tier B: scaled from a known object
    PIXEL_RATIO = "pixel_ratio"            # Tier C: crude fallback

    @property
    def error_band(self) -> tuple[float, float]:
        """(low, high) fractional error, e.g. (0.05, 0.08) == -5%/+8%."""
        return _ERROR_BANDS[self]

    @property
    def label(self) -> str:
        return _METHOD_LABELS[self]


_ERROR_BANDS: dict[AreaMethod, tuple[float, float]] = {
    AreaMethod.USER_DIMENSION: (0.05, 0.08),
    AreaMethod.REFERENCE_OBJECT: (0.12, 0.20),
    AreaMethod.PIXEL_RATIO: (0.25, 0.40),
}

_METHOD_LABELS: dict[AreaMethod, str] = {
    AreaMethod.USER_DIMENSION: "Measured (you supplied a real dimension)",
    AreaMethod.REFERENCE_OBJECT: "Scaled from a reference object",
    AreaMethod.PIXEL_RATIO: "Rough pixel estimate - please verify",
}


class RegionSource(str, Enum):
    CV = "cv"          # offline computer-vision suggestion
    AI = "ai"          # Gemini segmentation (opt-in)
    MANUAL = "manual"  # drawn or corrected by the user


class MaterialKind(str, Enum):
    """Drives both the renderer and the quantity formula."""

    PAINT = "paint"
    TEXTURE = "texture"
    CLADDING = "cladding"
    TILE = "tile"
    PANEL = "panel"
    RAILING = "railing"
    CONSUMABLE = "consumable"  # putty, primer - derived, never user-assigned


class RenderKind(str, Enum):
    SOLID = "solid"      # flat colour, shading preserved
    TEXTURE = "texture"  # perspective-tiled procedural texture


class QuantityUnit(str, Enum):
    SQFT = "sqft"
    RFT = "rft"          # running feet
    LITRE = "litre"
    PIECE = "piece"
    KG = "kg"
    BAG = "bag"


class ImageKind(str, Enum):
    ORIGINAL = "original"
    DISPLAY = "display"
    RENDER = "render"
