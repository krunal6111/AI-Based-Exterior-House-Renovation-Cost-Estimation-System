"""Loads the material catalog and the documented assumptions.

Both files live in ``app/data`` and are plain JSON on purpose: the whole point
of requirement 5.7 is that a user can see and change every rate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from ..config import DATA_DIR
from ..enums import MaterialKind, QuantityUnit, RegionCategory


@dataclass(frozen=True)
class RateBand:
    unit: str
    min: float
    default: float
    max: float

    @classmethod
    def from_json(cls, raw: dict[str, Any] | None) -> "RateBand":
        raw = raw or {}
        return cls(
            unit=raw.get("unit", "sqft"),
            min=float(raw.get("min", 0.0)),
            default=float(raw.get("default", 0.0)),
            max=float(raw.get("max", 0.0)),
        )


@dataclass(frozen=True)
class Material:
    id: str
    name: str
    short: str
    kind: MaterialKind
    group: str
    applies_to: tuple[RegionCategory, ...]
    render: dict[str, Any] | None
    palette: tuple[str, ...]
    coverage: dict[str, Any]
    wastage_pct: float
    material_rate: RateBand
    labour_rate: RateBand
    pack_sizes_litre: tuple[float, ...]
    piece_size_mm: tuple[float, float] | None
    pieces_per_box: int | None
    requires: tuple[str, ...]
    durability_years: tuple[int, int]
    maintenance: str
    source_note: str
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @property
    def is_linear(self) -> bool:
        return self.material_rate.unit == QuantityUnit.RFT.value

    @property
    def assignable(self) -> bool:
        """Consumables are derived, never picked by the user."""
        return self.kind is not MaterialKind.CONSUMABLE and bool(self.applies_to)

    def supports(self, category: RegionCategory) -> bool:
        return category in self.applies_to

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Material":
        rate = raw.get("rate", {})
        applies: list[RegionCategory] = []
        for c in raw.get("applies_to", []):
            try:
                applies.append(RegionCategory(c))
            except ValueError:
                continue
        piece = raw.get("piece_size_mm")
        dur = raw.get("durability_years") or [0, 0]
        return cls(
            id=raw["id"],
            name=raw["name"],
            short=raw.get("short", ""),
            kind=MaterialKind(raw["kind"]),
            group=raw.get("group", "Other"),
            applies_to=tuple(applies),
            render=raw.get("render"),
            palette=tuple(raw.get("palette", [])),
            coverage=raw.get("coverage", {}) or {},
            wastage_pct=float(raw.get("wastage_pct", 0.0)),
            material_rate=RateBand.from_json(rate.get("material")),
            labour_rate=RateBand.from_json(rate.get("labour")),
            pack_sizes_litre=tuple(float(p) for p in raw.get("pack_sizes_litre", [])),
            piece_size_mm=(float(piece[0]), float(piece[1])) if piece else None,
            pieces_per_box=raw.get("pieces_per_box"),
            requires=tuple(raw.get("requires", [])),
            durability_years=(int(dur[0]), int(dur[1])),
            maintenance=raw.get("maintenance", ""),
            source_note=raw.get("source_note", ""),
            raw=raw,
        )


@dataclass(frozen=True)
class Catalog:
    currency: str
    currency_symbol: str
    disclaimer: str
    updated: str
    materials: dict[str, Material]

    def get(self, material_id: str) -> Material | None:
        return self.materials.get(material_id)

    def require(self, material_id: str) -> Material:
        m = self.materials.get(material_id)
        if m is None:
            raise KeyError(f"unknown material: {material_id}")
        return m

    def assignable(self) -> list[Material]:
        return [m for m in self.materials.values() if m.assignable]

    def for_category(self, category: RegionCategory) -> list[Material]:
        return [m for m in self.assignable() if m.supports(category)]


@lru_cache(maxsize=1)
def load_catalog() -> Catalog:
    data = json.loads((DATA_DIR / "materials.json").read_text(encoding="utf-8"))
    materials = {
        raw["id"]: Material.from_json(raw) for raw in data.get("materials", [])
    }
    return Catalog(
        currency=data.get("currency", "INR"),
        currency_symbol=data.get("currency_symbol", "₹"),
        disclaimer=data.get("disclaimer", ""),
        updated=data.get("updated", ""),
        materials=materials,
    )


@lru_cache(maxsize=1)
def load_assumptions() -> dict[str, Any]:
    return json.loads((DATA_DIR / "assumptions.json").read_text(encoding="utf-8"))


def default_cost_settings() -> dict[str, Any]:
    return dict(load_assumptions().get("cost_settings", {}))


def reference_objects() -> list[dict[str, Any]]:
    return list(load_assumptions().get("reference_objects", []))


def quality_thresholds() -> dict[str, Any]:
    return dict(load_assumptions().get("quality_gate", {}))


def limitations() -> list[str]:
    return list(load_assumptions().get("limitations", []))
