"""Material quantity calculation (requirement 5.6).

Pure functions over plain data -- no database, no image, no network -- so the
numbers that end up in a homeowner's budget can be unit-tested directly.

Every line carries the ``formula`` string that produced it. A quantity a user
cannot audit is a quantity they cannot argue with a contractor about.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

from ..enums import MaterialKind, QuantityUnit
from .catalog import Catalog, Material

SQFT_PER_SQM = 10.7639


@dataclass
class SurfaceDemand:
    """How much of a given material a design needs, before conversion."""

    material_id: str
    area_sqft: float = 0.0
    length_ft: float = 0.0
    coats: int = 0
    region_names: list[str] = field(default_factory=list)

    def add_region(self, name: str) -> None:
        if name and name not in self.region_names:
            self.region_names.append(name)


@dataclass
class PackBreakdown:
    size: float
    count: int

    @property
    def total(self) -> float:
        return self.size * self.count


@dataclass
class QuantityLine:
    material_id: str
    material_name: str
    group: str
    unit: str
    quantity: float
    #: Area/length this quantity was derived from, used again by the costing
    #: step to price labour.
    basis_area_sqft: float
    basis_length_ft: float
    wastage_pct: float
    formula: str
    derived: bool = False
    region_names: list[str] = field(default_factory=list)
    packs: list[PackBreakdown] = field(default_factory=list)
    pieces: int | None = None
    boxes: int | None = None
    notes: str = ""

    @property
    def purchase_quantity(self) -> float:
        """What you actually buy -- pack total if packaged, else the quantity."""
        if self.packs:
            return sum(p.total for p in self.packs)
        return self.quantity


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def solve_packs(required: float, sizes: Iterable[float]) -> list[PackBreakdown]:
    """Cover ``required`` units with the fewest, tightest standard packs.

    Greedy from the largest size, then a single top-up pack sized to the
    remainder. With real paint denominations (20/10/4/1 L) this is optimal and
    avoids the "buy 2x20 L for 21 L" trap.
    """
    sizes = sorted((float(s) for s in sizes if s > 0), reverse=True)
    if not sizes or required <= 0:
        return []

    counts: dict[float, int] = {}
    remaining = required
    for size in sizes[:-1]:
        n = int(remaining // size)
        if n:
            counts[size] = counts.get(size, 0) + n
            remaining -= n * size

    if remaining > 1e-9:
        # Smallest pack that still covers the remainder, else many of the
        # smallest available.
        candidates = [s for s in sizes if s >= remaining - 1e-9]
        if candidates:
            top = min(candidates)
            counts[top] = counts.get(top, 0) + 1
        else:
            smallest = sizes[-1]
            n = int(math.ceil(remaining / smallest))
            counts[smallest] = counts.get(smallest, 0) + n

    return [
        PackBreakdown(size=s, count=c) for s, c in sorted(counts.items(), reverse=True)
    ]


def _fmt(value: float, places: int = 2) -> str:
    return f"{value:,.{places}f}".rstrip("0").rstrip(".") if places else f"{value:,.0f}"


# ---------------------------------------------------------------------------
# demand collection
# ---------------------------------------------------------------------------


def collect_demand(
    assignments: Iterable[dict], catalog: Catalog
) -> dict[str, SurfaceDemand]:
    """Fold per-region assignments into per-material demand.

    Each assignment dict needs: ``material_id``, ``area_sqft``, ``length_ft``,
    ``region_name`` and optional ``coats``.
    """
    demand: dict[str, SurfaceDemand] = {}
    for a in assignments:
        mid = a.get("material_id")
        material = catalog.get(mid) if mid else None
        if material is None:
            continue
        d = demand.setdefault(mid, SurfaceDemand(material_id=mid))
        d.area_sqft += max(0.0, float(a.get("area_sqft") or 0.0))
        d.length_ft += max(0.0, float(a.get("length_ft") or 0.0))
        d.coats = max(
            d.coats,
            int(a.get("coats") or material.coverage.get("default_coats") or 0),
        )
        d.add_region(a.get("region_name", ""))
    return demand


def derive_consumables(
    demand: dict[str, SurfaceDemand], catalog: Catalog
) -> dict[str, SurfaceDemand]:
    """Add primer / putty / adhesive implied by the chosen finishes."""
    extra: dict[str, SurfaceDemand] = {}
    for mid, d in demand.items():
        material = catalog.get(mid)
        if material is None:
            continue
        for req_id in material.requires:
            req = catalog.get(req_id)
            if req is None:
                continue
            e = extra.setdefault(req_id, SurfaceDemand(material_id=req_id))
            e.area_sqft += d.area_sqft
            e.length_ft += d.length_ft
            e.coats = max(e.coats, int(req.coverage.get("default_coats") or 1))
            e.add_region(f"from {material.name}")
    return extra


# ---------------------------------------------------------------------------
# the conversion itself
# ---------------------------------------------------------------------------


def quantity_for(
    material: Material,
    demand: SurfaceDemand,
    wastage_pct: float | None = None,
    derived: bool = False,
) -> QuantityLine | None:
    """Convert an area or length demand into a purchasable quantity."""
    waste = material.wastage_pct if wastage_pct is None else float(wastage_pct)
    wf = 1.0 + waste / 100.0
    area = demand.area_sqft
    length = demand.length_ft
    cov = material.coverage

    base = dict(
        material_id=material.id,
        material_name=material.name,
        group=material.group,
        basis_area_sqft=area,
        basis_length_ft=length,
        wastage_pct=waste,
        derived=derived,
        region_names=list(demand.region_names),
    )

    unit = material.material_rate.unit

    # --- liquids priced per litre -------------------------------------
    if unit == QuantityUnit.LITRE.value:
        per_coat = float(cov.get("sqft_per_litre_per_coat") or 0)
        coats = demand.coats or int(cov.get("default_coats") or 1)
        if per_coat <= 0 or area <= 0:
            return None
        litres = area * coats / per_coat * wf
        packs = solve_packs(litres, material.pack_sizes_litre)
        return QuantityLine(
            unit=QuantityUnit.LITRE.value,
            quantity=round(litres, 2),
            packs=packs,
            formula=(
                f"{_fmt(area)} sqft x {coats} coats / {_fmt(per_coat)} sqft per litre "
                f"per coat x {_fmt(wf, 3)} wastage = {_fmt(litres)} L"
            ),
            notes=(
                "Buy "
                + " + ".join(f"{p.count} x {_fmt(p.size)} L" for p in packs)
                + f" = {_fmt(sum(p.total for p in packs))} L"
                if packs
                else ""
            ),
            **base,
        )

    # --- powders priced per kg ----------------------------------------
    if unit == QuantityUnit.KG.value:
        per_kg = float(cov.get("sqft_per_kg") or 0)
        if per_kg <= 0 or area <= 0:
            return None
        kg = area / per_kg * wf
        return QuantityLine(
            unit=QuantityUnit.KG.value,
            quantity=round(kg, 2),
            formula=(
                f"{_fmt(area)} sqft / {_fmt(per_kg)} sqft per kg x "
                f"{_fmt(wf, 3)} wastage = {_fmt(kg)} kg"
            ),
            **base,
        )

    # --- bagged goods --------------------------------------------------
    if unit == QuantityUnit.BAG.value:
        per_bag = float(cov.get("sqft_per_bag") or 0)
        if per_bag <= 0 or area <= 0:
            return None
        bags = math.ceil(area / per_bag * wf)
        return QuantityLine(
            unit=QuantityUnit.BAG.value,
            quantity=float(bags),
            formula=(
                f"ceil({_fmt(area)} sqft / {_fmt(per_bag)} sqft per bag x "
                f"{_fmt(wf, 3)}) = {bags} bags"
            ),
            **base,
        )

    # --- linear items ---------------------------------------------------
    if unit == QuantityUnit.RFT.value:
        if length <= 0:
            return None
        rft = length * wf
        return QuantityLine(
            unit=QuantityUnit.RFT.value,
            quantity=round(rft, 2),
            formula=(
                f"{_fmt(length)} running ft x {_fmt(wf, 3)} wastage = {_fmt(rft)} rft"
            ),
            **base,
        )

    # --- everything billed by area --------------------------------------
    if area <= 0:
        return None
    sqft = area * wf
    line = QuantityLine(
        unit=QuantityUnit.SQFT.value,
        quantity=round(sqft, 2),
        formula=f"{_fmt(area)} sqft x {_fmt(wf, 3)} wastage = {_fmt(sqft)} sqft",
        **base,
    )

    # Tiles additionally report a piece and box count.
    if material.kind is MaterialKind.TILE and material.piece_size_mm:
        w_mm, h_mm = material.piece_size_mm
        tile_sqft = (w_mm / 1000.0) * (h_mm / 1000.0) * SQFT_PER_SQM
        if tile_sqft > 0:
            pieces = math.ceil(sqft / tile_sqft)
            line.pieces = pieces
            per_box = material.pieces_per_box or 0
            if per_box > 0:
                line.boxes = math.ceil(pieces / per_box)
            line.formula += (
                f"; {_fmt(sqft)} sqft / {_fmt(tile_sqft, 3)} sqft per tile "
                f"({_fmt(w_mm, 0)}x{_fmt(h_mm, 0)} mm) = {pieces} tiles"
            )
            if line.boxes:
                line.formula += f" = {line.boxes} boxes of {per_box}"
    return line


def compute_quantities(
    assignments: Iterable[dict],
    catalog: Catalog,
    wastage_overrides: dict[str, float] | None = None,
) -> list[QuantityLine]:
    """Full requirement-5.6 pass: assignments in, purchasable quantities out."""
    overrides = wastage_overrides or {}
    assignments = list(assignments)

    demand = collect_demand(assignments, catalog)
    consumables = derive_consumables(demand, catalog)

    lines: list[QuantityLine] = []
    for mid, d in demand.items():
        material = catalog.require(mid)
        line = quantity_for(material, d, overrides.get(mid))
        if line:
            lines.append(line)
    for mid, d in consumables.items():
        material = catalog.require(mid)
        line = quantity_for(material, d, overrides.get(mid), derived=True)
        if line:
            lines.append(line)

    lines.sort(key=lambda ln: (ln.derived, ln.group, ln.material_name))
    return lines
