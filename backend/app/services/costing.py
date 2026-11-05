"""Cost estimation (requirement 5.7).

Deterministic and fully traceable: every line records the rate used, where
that rate came from (catalog default or a user override) and the arithmetic
that produced the number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from ..enums import QuantityUnit
from .catalog import Catalog
from .quantities import QuantityLine


@dataclass
class RateSet:
    """Effective rates for one material after applying project overrides."""

    material_id: str
    material_rate: float
    material_unit: str
    labour_rate: float
    labour_unit: str
    material_overridden: bool = False
    labour_overridden: bool = False


@dataclass
class CostLine:
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
    region_names: list[str] = field(default_factory=list)


@dataclass
class AddOn:
    key: str
    label: str
    amount: float
    basis: str


@dataclass
class CostEstimate:
    currency: str
    currency_symbol: str
    lines: list[CostLine]
    group_subtotals: dict[str, float]
    material_subtotal: float
    labour_subtotal: float
    subtotal: float
    addons: list[AddOn]
    grand_total: float
    total_area_sqft: float
    total_length_ft: float
    #: Low/high envelope from the area-estimation error bands.
    range_low: float
    range_high: float
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return asdict(self)


def effective_rates(
    material_id: str, catalog: Catalog, overrides: dict[str, dict[str, Any]]
) -> RateSet:
    m = catalog.require(material_id)
    ov = overrides.get(material_id, {}) or {}

    mat_override = ov.get("material_rate")
    lab_override = ov.get("labour_rate")

    return RateSet(
        material_id=material_id,
        material_rate=(
            float(mat_override) if mat_override is not None else m.material_rate.default
        ),
        material_unit=m.material_rate.unit,
        labour_rate=(
            float(lab_override) if lab_override is not None else m.labour_rate.default
        ),
        labour_unit=m.labour_rate.unit,
        material_overridden=mat_override is not None,
        labour_overridden=lab_override is not None,
    )


def _money(x: float) -> float:
    return round(float(x), 2)


def compute_cost(
    quantity_lines: Iterable[QuantityLine],
    catalog: Catalog,
    overrides: dict[str, dict[str, Any]] | None = None,
    cost_settings: dict[str, Any] | None = None,
    area_error_band: tuple[float, float] = (0.15, 0.22),
) -> CostEstimate:
    """Price a set of quantity lines into a full estimate.

    Material is priced on the *purchased* quantity (you pay for whole paint
    tins, not for the litres you happen to use). Labour is priced on the
    measured surface, before wastage -- a painter is paid for the wall, not
    for the paint that ended up on the drop sheet.
    """
    overrides = overrides or {}
    settings = dict(cost_settings or {})
    lines: list[CostLine] = []
    total_area = 0.0
    total_length = 0.0

    for q in quantity_lines:
        rates = effective_rates(q.material_id, catalog, overrides)

        billable_qty = q.purchase_quantity
        material_cost = billable_qty * rates.material_rate

        if rates.labour_unit == QuantityUnit.RFT.value:
            labour_basis = q.basis_length_ft
        else:
            labour_basis = q.basis_area_sqft
        labour_cost = labour_basis * rates.labour_rate

        # Only count each surface once towards the project totals; consumables
        # ride on the same surface as their parent finish.
        if not q.derived:
            total_area += q.basis_area_sqft
            total_length += q.basis_length_ft

        formula = (
            f"{billable_qty:,.2f} {q.unit} x {rates.material_rate:,.2f}/"
            f"{rates.material_unit} = {material_cost:,.2f}"
        )
        if labour_cost:
            formula += (
                f"  +  {labour_basis:,.2f} {rates.labour_unit} x "
                f"{rates.labour_rate:,.2f} = {labour_cost:,.2f} labour"
            )

        lines.append(
            CostLine(
                material_id=q.material_id,
                material_name=q.material_name,
                group=q.group,
                quantity=billable_qty,
                unit=q.unit,
                material_rate=rates.material_rate,
                material_cost=_money(material_cost),
                labour_basis=round(labour_basis, 2),
                labour_unit=rates.labour_unit,
                labour_rate=rates.labour_rate,
                labour_cost=_money(labour_cost),
                total=_money(material_cost + labour_cost),
                derived=q.derived,
                overridden=rates.material_overridden or rates.labour_overridden,
                formula=formula,
                region_names=list(q.region_names),
            )
        )

    material_subtotal = _money(sum(ln.material_cost for ln in lines))
    labour_subtotal = _money(sum(ln.labour_cost for ln in lines))
    subtotal = _money(material_subtotal + labour_subtotal)

    group_subtotals: dict[str, float] = {}
    for ln in lines:
        group_subtotals[ln.group] = _money(group_subtotals.get(ln.group, 0.0) + ln.total)

    # --- add-ons, applied in a documented order -------------------------
    addons: list[AddOn] = []
    running = subtotal

    if settings.get("scaffolding_enabled", True):
        rate = float(settings.get("scaffolding_rate_per_sqft", 0) or 0)
        if rate > 0 and total_area > 0:
            amt = _money(total_area * rate)
            addons.append(
                AddOn(
                    "scaffolding",
                    "Scaffolding / access",
                    amt,
                    f"{total_area:,.0f} sqft x {rate:,.2f}/sqft",
                )
            )
            running += amt

    transport = float(settings.get("transport_lumpsum", 0) or 0)
    if transport > 0:
        addons.append(AddOn("transport", "Transport & handling", _money(transport), "lump sum"))
        running += transport

    overhead_pct = float(settings.get("contractor_overhead_pct", 0) or 0)
    if overhead_pct:
        amt = _money(running * overhead_pct / 100.0)
        addons.append(
            AddOn("overhead", f"Contractor overhead & profit ({overhead_pct:g}%)", amt,
                  f"{overhead_pct:g}% of {running:,.2f}")
        )
        running += amt

    contingency_pct = float(settings.get("contingency_pct", 0) or 0)
    if contingency_pct:
        amt = _money(running * contingency_pct / 100.0)
        addons.append(
            AddOn("contingency", f"Contingency ({contingency_pct:g}%)", amt,
                  f"{contingency_pct:g}% of {running:,.2f}")
        )
        running += amt

    if settings.get("gst_enabled", True):
        gst_pct = float(settings.get("gst_pct", 0) or 0)
        if gst_pct:
            amt = _money(running * gst_pct / 100.0)
            addons.append(
                AddOn("gst", f"GST ({gst_pct:g}%)", amt, f"{gst_pct:g}% of {running:,.2f}")
            )
            running += amt

    grand_total = _money(running)
    low_err, high_err = area_error_band

    return CostEstimate(
        currency=catalog.currency,
        currency_symbol=catalog.currency_symbol,
        lines=lines,
        group_subtotals=group_subtotals,
        material_subtotal=material_subtotal,
        labour_subtotal=labour_subtotal,
        subtotal=subtotal,
        addons=addons,
        grand_total=grand_total,
        total_area_sqft=round(total_area, 2),
        total_length_ft=round(total_length, 2),
        range_low=_money(grand_total * (1.0 - low_err)),
        range_high=_money(grand_total * (1.0 + high_err)),
        notes=[
            "Material is priced on the quantity you actually purchase "
            "(whole tins, bags and boxes); labour is priced on the measured "
            "surface before wastage.",
            "Add-ons are applied in order: scaffolding, transport, overhead, "
            "contingency, then GST on the running total.",
            "The range reflects uncertainty in the measured areas only. It "
            "does not cover rate variation between contractors.",
        ],
    )


def blended_error_band(
    region_bands: Iterable[tuple[float, float, float]]
) -> tuple[float, float]:
    """Area-weighted blend of per-region error bands.

    Each item is ``(area_or_length, low_err, high_err)``. A project whose big
    walls were measured properly should not inherit the error band of one
    small guessed region.
    """
    items = [(w, lo, hi) for w, lo, hi in region_bands if w > 0]
    if not items:
        return (0.25, 0.40)
    total = sum(w for w, _, _ in items)
    low = sum(w * lo for w, lo, _ in items) / total
    high = sum(w * hi for w, _, hi in items) / total
    return (round(low, 4), round(high, 4))
