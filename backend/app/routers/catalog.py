"""Material catalog, reference objects and documented assumptions."""

from __future__ import annotations

from fastapi import APIRouter

from ..enums import RegionCategory
from ..schemas import CatalogOut, MaterialOut, RateBandOut
from ..services.catalog import (
    default_cost_settings,
    limitations,
    load_catalog,
    reference_objects,
)

router = APIRouter(tags=["catalog"])


@router.get("/catalog", response_model=CatalogOut)
def get_catalog():
    catalog = load_catalog()
    materials = [
        MaterialOut(
            id=m.id,
            name=m.name,
            short=m.short,
            kind=m.kind.value,
            group=m.group,
            applies_to=[c.value for c in m.applies_to],
            render=m.render,
            palette=list(m.palette),
            coverage=m.coverage,
            wastage_pct=m.wastage_pct,
            material_rate=RateBandOut(**m.material_rate.__dict__),
            labour_rate=RateBandOut(**m.labour_rate.__dict__),
            durability_years=list(m.durability_years),
            maintenance=m.maintenance,
            source_note=m.source_note,
            assignable=m.assignable,
        )
        for m in catalog.materials.values()
    ]

    categories = [
        {
            "value": c.value,
            "label": c.label,
            "measure": c.measure.value,
            "is_opening": c.is_opening,
        }
        for c in RegionCategory
    ]

    return CatalogOut(
        currency=catalog.currency,
        currency_symbol=catalog.currency_symbol,
        disclaimer=catalog.disclaimer,
        updated=catalog.updated,
        materials=materials,
        reference_objects=reference_objects(),
        categories=categories,
        default_cost_settings=default_cost_settings(),
        limitations=limitations(),
    )
