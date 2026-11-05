"""SQLAlchemy models.

Geometry is always stored **normalised to 0..1** against the working image so
that regions stay valid whatever resolution we later render or report at.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .enums import AreaMethod, ImageKind, RegionCategory, RegionSource


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), default="Untitled renovation")
    #: Browser-local UUID. Gives per-user isolation without building auth.
    owner_token: Mapped[str] = mapped_column(String(64), index=True)
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    notes: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now
    )

    #: Cost add-ons, kept on the project so the whole estimate is reproducible.
    cost_settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    images: Mapped[list["ImageAsset"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    regions: Mapped[list["Region"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    variants: Mapped[list["DesignVariant"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    rate_overrides: Mapped[list["RateOverride"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    calibration: Mapped["Calibration | None"] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        uselist=False,
        passive_deletes=True,
    )


class ImageAsset(Base):
    __tablename__ = "image_assets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16), default=ImageKind.ORIGINAL.value)
    filename: Mapped[str] = mapped_column(String(255))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    #: Quality-gate verdict (requirement 5.1). Only set on the original.
    quality: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    #: For renders: which variant produced it, and by which engine.
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    project: Mapped[Project] = relationship(back_populates="images")


class Region(Base):
    __tablename__ = "regions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), default="")
    category: Mapped[str] = mapped_column(
        String(24), default=RegionCategory.WALL.value
    )
    #: Openings reference the wall they are cut out of.
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("regions.id", ondelete="SET NULL"), nullable=True
    )

    #: [[x, y], ...] normalised 0..1, clockwise, >= 3 points.
    polygon: Mapped[list[list[float]]] = mapped_column(JSON, default=list)

    source: Mapped[str] = mapped_column(String(12), default=RegionSource.CV.value)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    user_edited: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- measurement ---------------------------------------------------
    area_sqft: Mapped[float | None] = mapped_column(Float, nullable=True)
    length_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    area_method: Mapped[str | None] = mapped_column(String(24), nullable=True)
    #: User-supplied real-world dimensions (Tier A), either may be set.
    override_width_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    override_height_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Straight override of the final number, wins over everything.
    override_area_sqft: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Diagnostics from the rectification step (vanishing points, quad, ...).
    measure_meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    project: Mapped[Project] = relationship(back_populates="regions")

    @property
    def category_enum(self) -> RegionCategory:
        try:
            return RegionCategory(self.category)
        except ValueError:
            return RegionCategory.OTHER

    @property
    def area_method_enum(self) -> AreaMethod | None:
        if not self.area_method:
            return None
        try:
            return AreaMethod(self.area_method)
        except ValueError:
            return None


class Calibration(Base):
    """Maps image pixels to real-world feet (requirement 5.5)."""

    __tablename__ = "calibrations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), unique=True, index=True
    )

    #: "door" | "floor_height" | "window" | "custom"
    ref_kind: Mapped[str] = mapped_column(String(24), default="door")
    #: Two normalised points the user dragged over the reference object.
    ref_line: Mapped[list[list[float]]] = mapped_column(JSON, default=list)
    ref_real_ft: Mapped[float] = mapped_column(Float, default=6.9)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)

    #: Derived scale at the reference plane, feet per normalised unit.
    ft_per_unit: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Estimated vanishing points / horizon for perspective rectification.
    perspective: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    project: Mapped[Project] = relationship(back_populates="calibration")


class DesignVariant(Base):
    """A named combination of materials (requirement 5.3)."""

    __tablename__ = "design_variants"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), default="Option A")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    render_asset_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    project: Mapped[Project] = relationship(back_populates="variants")
    assignments: Mapped[list["Assignment"]] = relationship(
        back_populates="variant",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Assignment(Base):
    """material -> region, within one design variant."""

    __tablename__ = "assignments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    variant_id: Mapped[str] = mapped_column(
        ForeignKey("design_variants.id", ondelete="CASCADE"), index=True
    )
    region_id: Mapped[str] = mapped_column(
        ForeignKey("regions.id", ondelete="CASCADE"), index=True
    )
    material_id: Mapped[str] = mapped_column(String(64))
    #: Per-assignment tweaks: colour hex, tile size, coats, orientation.
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    variant: Mapped[DesignVariant] = relationship(back_populates="assignments")


class RateOverride(Base):
    """Per-project edits to catalog rates (requirement 5.7)."""

    __tablename__ = "rate_overrides"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    material_id: Mapped[str] = mapped_column(String(64), index=True)
    material_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    labour_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    wastage_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    project: Mapped[Project] = relationship(back_populates="rate_overrides")
