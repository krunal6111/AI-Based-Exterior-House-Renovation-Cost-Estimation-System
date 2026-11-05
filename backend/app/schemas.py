"""Pydantic request/response models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------


class RateBandOut(BaseModel):
    unit: str
    min: float
    default: float
    max: float


class MaterialOut(BaseModel):
    id: str
    name: str
    short: str = ""
    kind: str
    group: str
    applies_to: list[str]
    render: dict[str, Any] | None = None
    palette: list[str] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)
    wastage_pct: float = 0
    material_rate: RateBandOut
    labour_rate: RateBandOut
    durability_years: list[int] = Field(default_factory=list)
    maintenance: str = ""
    source_note: str = ""
    assignable: bool = True


class CatalogOut(BaseModel):
    currency: str
    currency_symbol: str
    disclaimer: str
    updated: str
    materials: list[MaterialOut]
    reference_objects: list[dict[str, Any]]
    categories: list[dict[str, Any]]
    default_cost_settings: dict[str, Any]
    limitations: list[str]


# ---------------------------------------------------------------------------
# regions
# ---------------------------------------------------------------------------


class RegionIn(BaseModel):
    id: str | None = None
    name: str = ""
    category: str = "wall"
    polygon: list[list[float]]
    parent_id: str | None = None
    source: str = "manual"
    confidence: float = 0.6
    user_edited: bool = True
    override_width_ft: float | None = None
    override_height_ft: float | None = None
    override_area_sqft: float | None = None


class MeasurementOut(BaseModel):
    area_sqft: float = 0
    length_ft: float = 0
    width_ft: float = 0
    height_ft: float = 0
    aspect_ratio: float = 1
    method: str | None = None
    method_label: str = ""
    confidence: float = 0
    error_low: float = 0
    error_high: float = 0
    notes: list[str] = Field(default_factory=list)


class RegionOut(BaseModel):
    id: str
    name: str
    category: str
    category_label: str
    measure: str
    polygon: list[list[float]]
    parent_id: str | None = None
    source: str
    confidence: float
    user_edited: bool
    override_width_ft: float | None = None
    override_height_ft: float | None = None
    override_area_sqft: float | None = None
    measurement: MeasurementOut | None = None


class RegionsIn(BaseModel):
    regions: list[RegionIn]


# ---------------------------------------------------------------------------
# calibration
# ---------------------------------------------------------------------------


class CalibrationIn(BaseModel):
    ref_kind: str = "door"
    ref_line: list[list[float]]
    ref_real_ft: float
    confirmed: bool = True


class CalibrationOut(BaseModel):
    ref_kind: str
    ref_line: list[list[float]]
    ref_real_ft: float
    confirmed: bool
    ft_per_px: float | None = None
    note: str = ""


# ---------------------------------------------------------------------------
# design
# ---------------------------------------------------------------------------


class AssignmentIn(BaseModel):
    region_id: str
    material_id: str
    params: dict[str, Any] = Field(default_factory=dict)


class AssignmentOut(BaseModel):
    id: str
    region_id: str
    material_id: str
    params: dict[str, Any] = Field(default_factory=dict)


class VariantIn(BaseModel):
    name: str = "Option A"
    assignments: list[AssignmentIn] = Field(default_factory=list)


class VariantOut(BaseModel):
    id: str
    name: str
    is_active: bool
    render_url: str | None = None
    assignments: list[AssignmentOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# rates / settings
# ---------------------------------------------------------------------------


class RateOverrideIn(BaseModel):
    material_id: str
    material_rate: float | None = None
    labour_rate: float | None = None
    wastage_pct: float | None = None


class RateOverridesIn(BaseModel):
    overrides: list[RateOverrideIn] = Field(default_factory=list)


class ProjectPatch(BaseModel):
    name: str | None = None
    notes: str | None = None
    cost_settings: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# estimate
# ---------------------------------------------------------------------------


class QuantityOut(BaseModel):
    material_id: str
    material_name: str
    group: str
    unit: str
    quantity: float
    purchase_quantity: float
    basis_area_sqft: float
    basis_length_ft: float
    wastage_pct: float
    formula: str
    derived: bool
    region_names: list[str]
    packs: list[dict[str, Any]] = Field(default_factory=list)
    pieces: int | None = None
    boxes: int | None = None
    notes: str = ""


class CostLineOut(BaseModel):
    material_id: str
    material_name: str
    group: str
    quantity: float
    unit: str
    material_rate: float
    material_cost: float
    labour_basis: float
    labour_unit: str
    labour_rate: float
    labour_cost: float
    total: float
    derived: bool
    overridden: bool
    formula: str
    region_names: list[str]


class AddOnOut(BaseModel):
    key: str
    label: str
    amount: float
    basis: str


class EstimateOut(BaseModel):
    currency: str
    currency_symbol: str
    quantities: list[QuantityOut]
    lines: list[CostLineOut]
    group_subtotals: dict[str, float]
    material_subtotal: float
    labour_subtotal: float
    subtotal: float
    addons: list[AddOnOut]
    grand_total: float
    range_low: float
    range_high: float
    total_area_sqft: float
    total_length_ft: float
    notes: list[str]
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# project
# ---------------------------------------------------------------------------


class QualityIssueOut(BaseModel):
    code: str
    severity: str
    message: str
    guidance: str


class QualityOut(BaseModel):
    usable: bool
    score: float
    width: int
    height: int
    sharpness: float
    mean_luma: float
    clipped_fraction: float
    building_fraction: float
    issues: list[QualityIssueOut]


class ProjectSummary(ORMModel):
    id: str
    name: str
    currency: str
    created_at: datetime
    updated_at: datetime
    thumbnail_url: str | None = None


class ProjectOut(BaseModel):
    id: str
    name: str
    notes: str = ""
    currency: str
    currency_symbol: str
    created_at: datetime
    updated_at: datetime
    image_url: str | None = None
    image_width: int = 0
    image_height: int = 0
    quality: QualityOut | None = None
    regions: list[RegionOut] = Field(default_factory=list)
    calibration: CalibrationOut | None = None
    variants: list[VariantOut] = Field(default_factory=list)
    active_variant_id: str | None = None
    cost_settings: dict[str, Any] = Field(default_factory=dict)
    rate_overrides: list[RateOverrideIn] = Field(default_factory=list)


class RenderOut(BaseModel):
    render_url: str
    engine: str
    layers_applied: int
    warnings: list[str] = Field(default_factory=list)


class HealthOut(BaseModel):
    status: str
    version: str
    offline_mode: bool
    ai_segmentation_available: bool
    ai_render_available: bool
