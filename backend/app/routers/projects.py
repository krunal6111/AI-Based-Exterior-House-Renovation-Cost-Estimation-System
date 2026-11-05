"""Project lifecycle: upload, regions, calibration, design, estimate, report."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..enums import ImageKind, RegionCategory, RegionSource
from ..models import (
    Assignment,
    Calibration,
    DesignVariant,
    ImageAsset,
    Project,
    RateOverride,
    Region,
)
from ..providers import detect_regions as detect_surfaces
from ..schemas import (
    CalibrationIn,
    EstimateOut,
    ProjectOut,
    ProjectPatch,
    ProjectSummary,
    RateOverridesIn,
    RegionsIn,
    RenderOut,
    VariantIn,
)
from ..services import image_io
from ..services.catalog import load_catalog, default_cost_settings, reference_objects
from ..services.estimator import (
    active_variant,
    build_estimate,
    measure_project,
    render_layers,
)
from ..services.pdf_report import build_report
from ..services.quality import assess
from ..services.scene import scene_masks
from ..services.renderer import render as render_image

router = APIRouter(prefix="/projects", tags=["projects"])
log = logging.getLogger(__name__)

#: Images are embedded in the PDF at this long edge - plenty for A4.
PDF_IMAGE_EDGE = 1000


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def owner_token(x_owner_token: str | None = Header(default=None)) -> str:
    """Per-browser identity. Enough to keep concurrent users apart."""
    return (x_owner_token or "").strip() or "anonymous"


def _get_project(db: Session, project_id: str, token: str) -> Project:
    project = db.get(Project, project_id)
    if project is None or project.owner_token != token:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _original_asset(project: Project) -> ImageAsset | None:
    for a in project.images:
        if a.kind == ImageKind.ORIGINAL.value:
            return a
    return None


def _building_box(asset: ImageAsset | None) -> tuple[int, int, int, int] | None:
    box = (asset.meta or {}).get("building_box") if asset else None
    return tuple(box) if box and len(box) == 4 else None


def _file_url(project_id: str, filename: str) -> str:
    return f"{settings.api_prefix}/projects/{project_id}/files/{filename}"


def _serialise(db: Session, project: Project) -> ProjectOut:
    catalog = load_catalog()
    asset = _original_asset(project)
    width = asset.width if asset else 0
    height = asset.height if asset else 0

    measurements = (
        measure_project(project, width, height, _building_box(asset))
        if (asset and project.regions)
        else None
    )

    regions_out = []
    for r in sorted(project.regions, key=lambda x: (x.category, x.name)):
        cat = r.category_enum
        m = measurements.by_region.get(r.id) if measurements else None
        regions_out.append(
            {
                "id": r.id,
                "name": r.name or cat.label,
                "category": r.category,
                "category_label": cat.label,
                "measure": cat.measure.value,
                "polygon": r.polygon,
                "parent_id": r.parent_id,
                "source": r.source,
                "confidence": r.confidence,
                "user_edited": r.user_edited,
                "override_width_ft": r.override_width_ft,
                "override_height_ft": r.override_height_ft,
                "override_area_sqft": r.override_area_sqft,
                "measurement": (
                    {
                        "area_sqft": m.area_sqft,
                        "length_ft": m.length_ft,
                        "width_ft": m.width_ft,
                        "height_ft": m.height_ft,
                        "aspect_ratio": m.aspect_ratio,
                        "method": m.method.value,
                        "method_label": m.method.label,
                        "confidence": m.confidence,
                        "error_low": m.error_low,
                        "error_high": m.error_high,
                        "notes": m.notes,
                    }
                    if m
                    else None
                ),
            }
        )

    variants_out = []
    for v in project.variants:
        render_url = None
        if v.render_asset_id:
            for a in project.images:
                if a.id == v.render_asset_id:
                    render_url = _file_url(
                        project.id, (a.meta or {}).get("view") or a.filename
                    )
                    break
        variants_out.append(
            {
                "id": v.id,
                "name": v.name,
                "is_active": v.is_active,
                "render_url": render_url,
                "assignments": [
                    {
                        "id": a.id,
                        "region_id": a.region_id,
                        "material_id": a.material_id,
                        "params": a.params or {},
                    }
                    for a in v.assignments
                ],
            }
        )

    cal = project.calibration
    cal_out = None
    if cal:
        cal_out = {
            "ref_kind": cal.ref_kind,
            "ref_line": cal.ref_line,
            "ref_real_ft": cal.ref_real_ft,
            "confirmed": cal.confirmed,
            "ft_per_px": cal.ft_per_unit,
            "note": (measurements.scale.note if measurements and measurements.scale else ""),
        }

    av = active_variant(project)
    return ProjectOut(
        id=project.id,
        name=project.name,
        notes=project.notes or "",
        currency=project.currency,
        currency_symbol=catalog.currency_symbol,
        created_at=project.created_at,
        updated_at=project.updated_at,
        image_url=_file_url(project.id, "display.jpg") if asset else None,
        image_width=width,
        image_height=height,
        quality=asset.quality if asset and asset.quality else None,
        regions=regions_out,
        calibration=cal_out,
        variants=variants_out,
        active_variant_id=av.id if av else None,
        cost_settings={**default_cost_settings(), **(project.cost_settings or {})},
        rate_overrides=[
            {
                "material_id": o.material_id,
                "material_rate": o.material_rate,
                "labour_rate": o.labour_rate,
                "wastage_pct": o.wastage_pct,
            }
            for o in project.rate_overrides
        ],
    )


def _reference_ft(kind: str, default: float) -> float:
    ref = next((r for r in reference_objects() if r["id"] == kind), None)
    return float(ref["real_ft"]) if ref else default


def _guess_calibration(
    project: Project,
    regions: list[Region],
    building_box: tuple[int, int, int, int] | None,
    width: int,
    height: int,
) -> None:
    """Always pre-place a draggable reference, left unconfirmed.

    A detected door is by far the best anchor; failing that we drop a handle
    down the middle of the building so the user has something concrete to drag
    onto a dimension they know. Either way it stays unconfirmed, because the
    difference between a guessed and a confirmed reference is the difference
    between a rough estimate and a usable one.
    """
    if project.calibration is not None:
        return

    doors = [r for r in regions if r.category == RegionCategory.DOOR.value]
    if doors:
        door = max(doors, key=lambda r: len(r.polygon))
        pts = np.asarray(door.polygon, dtype=float)
        if len(pts) >= 3:
            project.calibration = Calibration(
                ref_kind="door",
                ref_line=[
                    [float(pts[:, 0].mean()), float(pts[:, 1].min())],
                    [float(pts[:, 0].mean()), float(pts[:, 1].max())],
                ],
                ref_real_ft=_reference_ft("door", 6.9),
                confirmed=False,
            )
            return

    if building_box and width and height:
        x0, y0, x1, y1 = building_box
        cx = ((x0 + x1) / 2.0) / width
        top, bottom = y0 / height, y1 / height
        # Span the middle half of the building: a handle that is easy to grab
        # and roughly one storey tall on a typical low-rise facade.
        mid = (top + bottom) / 2.0
        span = (bottom - top) * 0.25
        project.calibration = Calibration(
            ref_kind="floor_height",
            ref_line=[[cx, max(0.0, mid - span)], [cx, min(1.0, mid + span)]],
            ref_real_ft=_reference_ft("floor_height", 10.0),
            confirmed=False,
        )


def _store_regions(
    db: Session, project: Project, detected: list[Any], replace: bool = True
) -> list[Region]:
    if replace:
        for r in list(project.regions):
            db.delete(r)
        project.regions.clear()
        db.flush()

    created: list[Region] = []
    for d in detected:
        region = Region(
            project_id=project.id,
            name=d.label,
            category=d.category.value,
            polygon=d.polygon,
            source=d.source.value,
            confidence=d.confidence,
            user_edited=False,
            measure_meta=d.meta or {},
        )
        db.add(region)
        created.append(region)
    db.flush()

    # Second pass: wire up parent links now that ids exist.
    for d, region in zip(detected, created):
        if d.parent_index is not None and 0 <= d.parent_index < len(created):
            region.parent_id = created[d.parent_index].id
    db.flush()
    return created


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=ProjectOut, status_code=201)
async def create_project(
    file: UploadFile = File(...),
    name: str = Form("Untitled renovation"),
    db: Session = Depends(get_db),
    token: str = Depends(owner_token),
):
    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"Image is larger than {settings.max_upload_mb} MB.",
        )

    project = Project(name=name.strip() or "Untitled renovation", owner_token=token)
    db.add(project)
    db.flush()

    dest = settings.project_dir(project.id)
    try:
        working = image_io.build_working_set(data, dest)
    except image_io.UnsupportedImage as exc:
        db.rollback()
        raise HTTPException(status_code=415, detail=str(exc)) from exc

    bgr = image_io.read_bgr(working.original_path)
    masks = scene_masks(bgr)
    report = assess(bgr, masks=masks)

    db.add(
        ImageAsset(
            project_id=project.id,
            kind=ImageKind.ORIGINAL.value,
            filename="original.jpg",
            width=working.width,
            height=working.height,
            quality=report.as_dict(),
            meta={
                "source_format": working.source_format,
                "display": "display.jpg",
                "display_width": working.display_width,
                "display_height": working.display_height,
                "building_box": list(masks.building_box) if masks.building_box else None,
            },
        )
    )

    if report.usable:
        detected, backend = detect_surfaces(bgr)
        log.info("Detected %d surfaces using %s", len(detected), backend)
        regions = _store_regions(db, project, detected)
        _guess_calibration(
            project, regions, masks.building_box, working.width, working.height
        )

    db.add(DesignVariant(project_id=project.id, name="Option A", is_active=True))
    db.commit()
    db.refresh(project)
    return _serialise(db, project)


@router.get("", response_model=list[ProjectSummary])
def list_projects(db: Session = Depends(get_db), token: str = Depends(owner_token)):
    rows = db.scalars(
        select(Project)
        .where(Project.owner_token == token)
        .order_by(Project.updated_at.desc())
    ).all()
    out = []
    for p in rows:
        asset = _original_asset(p)
        out.append(
            ProjectSummary(
                id=p.id,
                name=p.name,
                currency=p.currency,
                created_at=p.created_at,
                updated_at=p.updated_at,
                thumbnail_url=_file_url(p.id, "display.jpg") if asset else None,
            )
        )
    return out


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: str, db: Session = Depends(get_db), token: str = Depends(owner_token)
):
    return _serialise(db, _get_project(db, project_id, token))


@router.patch("/{project_id}", response_model=ProjectOut)
def patch_project(
    project_id: str,
    payload: ProjectPatch,
    db: Session = Depends(get_db),
    token: str = Depends(owner_token),
):
    project = _get_project(db, project_id, token)
    if payload.name is not None:
        project.name = payload.name.strip() or project.name
    if payload.notes is not None:
        project.notes = payload.notes
    if payload.cost_settings is not None:
        merged = dict(project.cost_settings or {})
        merged.update(payload.cost_settings)
        project.cost_settings = merged
    db.commit()
    db.refresh(project)
    return _serialise(db, project)


@router.delete("/{project_id}", status_code=204)
def delete_project(
    project_id: str, db: Session = Depends(get_db), token: str = Depends(owner_token)
):
    project = _get_project(db, project_id, token)
    db.delete(project)
    db.commit()
    return None


@router.get("/{project_id}/files/{filename}")
def get_file(
    project_id: str,
    filename: str,
    db: Session = Depends(get_db),
    token: str = Depends(owner_token),
):
    _get_project(db, project_id, token)
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Bad filename")
    path = settings.project_dir(project_id) / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path)


@router.post("/{project_id}/detect", response_model=ProjectOut)
def redetect(
    project_id: str, db: Session = Depends(get_db), token: str = Depends(owner_token)
):
    project = _get_project(db, project_id, token)
    asset = _original_asset(project)
    if asset is None:
        raise HTTPException(status_code=400, detail="No image uploaded yet")

    bgr = image_io.read_bgr(settings.project_dir(project.id) / asset.filename)
    detected, backend = detect_surfaces(bgr)
    log.info("Re-detected %d surfaces using %s", len(detected), backend)
    regions = _store_regions(db, project, detected)
    _guess_calibration(
        project, regions, _building_box(asset), asset.width, asset.height
    )
    db.commit()
    db.refresh(project)
    return _serialise(db, project)


@router.put("/{project_id}/regions", response_model=ProjectOut)
def put_regions(
    project_id: str,
    payload: RegionsIn,
    db: Session = Depends(get_db),
    token: str = Depends(owner_token),
):
    project = _get_project(db, project_id, token)

    incoming_ids = {r.id for r in payload.regions if r.id}
    for existing in list(project.regions):
        if existing.id not in incoming_ids:
            db.delete(existing)
    db.flush()

    by_id = {r.id: r for r in project.regions if r.id in incoming_ids}
    temp_to_real: dict[str, str] = {}

    for item in payload.regions:
        try:
            category = RegionCategory(item.category)
        except ValueError:
            category = RegionCategory.OTHER
        if len(item.polygon) < 2:
            continue

        region = by_id.get(item.id) if item.id else None
        if region is None:
            region = Region(project_id=project.id)
            db.add(region)
            db.flush()
            if item.id:
                temp_to_real[item.id] = region.id

        region.name = item.name or category.label
        region.category = category.value
        region.polygon = [[float(x), float(y)] for x, y in item.polygon]
        region.source = item.source or RegionSource.MANUAL.value
        region.confidence = item.confidence
        region.user_edited = item.user_edited
        region.override_width_ft = item.override_width_ft
        region.override_height_ft = item.override_height_ft
        region.override_area_sqft = item.override_area_sqft
        by_id[item.id or region.id] = region

    db.flush()
    for item in payload.regions:
        key = item.id or ""
        region = by_id.get(key)
        if region is None or not item.parent_id:
            continue
        parent = by_id.get(item.parent_id)
        if parent is None:
            parent_id = temp_to_real.get(item.parent_id, item.parent_id)
            parent = db.get(Region, parent_id)
        region.parent_id = parent.id if parent and parent.id != region.id else None

    db.commit()
    db.refresh(project)
    return _serialise(db, project)


@router.put("/{project_id}/calibration", response_model=ProjectOut)
def put_calibration(
    project_id: str,
    payload: CalibrationIn,
    db: Session = Depends(get_db),
    token: str = Depends(owner_token),
):
    project = _get_project(db, project_id, token)
    if len(payload.ref_line) < 2:
        raise HTTPException(status_code=422, detail="Reference needs two points")

    cal = project.calibration or Calibration(project_id=project.id)
    cal.ref_kind = payload.ref_kind
    cal.ref_line = [[float(x), float(y)] for x, y in payload.ref_line[:2]]
    cal.ref_real_ft = float(payload.ref_real_ft)
    cal.confirmed = payload.confirmed
    project.calibration = cal
    db.add(cal)
    db.commit()
    db.refresh(project)
    return _serialise(db, project)


@router.put("/{project_id}/assignments", response_model=ProjectOut)
def put_assignments(
    project_id: str,
    payload: VariantIn,
    db: Session = Depends(get_db),
    token: str = Depends(owner_token),
):
    project = _get_project(db, project_id, token)
    variant = active_variant(project)
    if variant is None:
        variant = DesignVariant(project_id=project.id, name=payload.name, is_active=True)
        db.add(variant)
        db.flush()
        project.variants.append(variant)

    if payload.name:
        variant.name = payload.name

    for a in list(variant.assignments):
        db.delete(a)
    # Clear the loaded collection too, or SQLAlchemy replays the DELETEs on
    # the next flush and warns that the rows have already gone.
    variant.assignments.clear()
    db.flush()

    region_ids = {r.id for r in project.regions}
    catalog = load_catalog()
    for item in payload.assignments:
        if item.region_id not in region_ids or catalog.get(item.material_id) is None:
            continue
        db.add(
            Assignment(
                variant_id=variant.id,
                region_id=item.region_id,
                material_id=item.material_id,
                params=item.params or {},
            )
        )
    db.commit()
    db.refresh(project)
    return _serialise(db, project)


@router.put("/{project_id}/rates", response_model=ProjectOut)
def put_rates(
    project_id: str,
    payload: RateOverridesIn,
    db: Session = Depends(get_db),
    token: str = Depends(owner_token),
):
    project = _get_project(db, project_id, token)
    for o in list(project.rate_overrides):
        db.delete(o)
    db.flush()

    catalog = load_catalog()
    for item in payload.overrides:
        if catalog.get(item.material_id) is None:
            continue
        if (
            item.material_rate is None
            and item.labour_rate is None
            and item.wastage_pct is None
        ):
            continue
        db.add(
            RateOverride(
                project_id=project.id,
                material_id=item.material_id,
                material_rate=item.material_rate,
                labour_rate=item.labour_rate,
                wastage_pct=item.wastage_pct,
            )
        )
    db.commit()
    db.refresh(project)
    return _serialise(db, project)


@router.get("/{project_id}/estimate", response_model=EstimateOut)
def get_estimate(
    project_id: str, db: Session = Depends(get_db), token: str = Depends(owner_token)
):
    project = _get_project(db, project_id, token)
    asset = _original_asset(project)
    if asset is None:
        raise HTTPException(status_code=400, detail="No image uploaded yet")

    measurements = measure_project(
        project, asset.width, asset.height, _building_box(asset)
    )
    quantities, estimate, warnings = build_estimate(project, measurements)

    return EstimateOut(
        currency=estimate.currency,
        currency_symbol=estimate.currency_symbol,
        quantities=[
            {
                "material_id": q.material_id,
                "material_name": q.material_name,
                "group": q.group,
                "unit": q.unit,
                "quantity": q.quantity,
                "purchase_quantity": q.purchase_quantity,
                "basis_area_sqft": q.basis_area_sqft,
                "basis_length_ft": q.basis_length_ft,
                "wastage_pct": q.wastage_pct,
                "formula": q.formula,
                "derived": q.derived,
                "region_names": q.region_names,
                "packs": [{"size": p.size, "count": p.count} for p in q.packs],
                "pieces": q.pieces,
                "boxes": q.boxes,
                "notes": q.notes,
            }
            for q in quantities
        ],
        lines=[ln.__dict__ for ln in estimate.lines],
        group_subtotals=estimate.group_subtotals,
        material_subtotal=estimate.material_subtotal,
        labour_subtotal=estimate.labour_subtotal,
        subtotal=estimate.subtotal,
        addons=[a.__dict__ for a in estimate.addons],
        grand_total=estimate.grand_total,
        range_low=estimate.range_low,
        range_high=estimate.range_high,
        total_area_sqft=estimate.total_area_sqft,
        total_length_ft=estimate.total_length_ft,
        notes=estimate.notes,
        warnings=warnings,
    )


@router.post("/{project_id}/render", response_model=RenderOut)
def post_render(
    project_id: str, db: Session = Depends(get_db), token: str = Depends(owner_token)
):
    project = _get_project(db, project_id, token)
    asset = _original_asset(project)
    if asset is None:
        raise HTTPException(status_code=400, detail="No image uploaded yet")

    variant = active_variant(project)
    if variant is None or not variant.assignments:
        raise HTTPException(
            status_code=400,
            detail="Assign at least one material before rendering.",
        )

    folder = settings.project_dir(project.id)
    bgr = image_io.read_bgr(folder / asset.filename)
    measurements = measure_project(
        project, asset.width, asset.height, _building_box(asset)
    )
    layers = render_layers(project, measurements)

    result = render_image(bgr, layers)

    filename = f"render-{variant.id[:8]}.jpg"
    view_name = f"render-{variant.id[:8]}-view.jpg"
    width, height = image_io.save_bgr(result.image, folder / filename, quality=90)
    # A display-sized copy keeps the browser and the PDF off the full-resolution
    # render, which can be several megabytes.
    image_io.make_thumbnail(folder / filename, folder / view_name, settings.display_edge)

    existing = next((a for a in project.images if a.filename == filename), None)
    if existing is None:
        existing = ImageAsset(
            project_id=project.id,
            kind=ImageKind.RENDER.value,
            filename=filename,
            width=width,
            height=height,
        )
        db.add(existing)
        db.flush()
    existing.width, existing.height = width, height
    existing.meta = {
        "engine": result.engine,
        "layers": result.layers_applied,
        "view": view_name,
    }
    variant.render_asset_id = existing.id

    db.commit()
    return RenderOut(
        render_url=_file_url(project.id, view_name),
        engine=result.engine,
        layers_applied=result.layers_applied,
        warnings=result.warnings,
    )


@router.get("/{project_id}/report.pdf")
def get_report(
    project_id: str, db: Session = Depends(get_db), token: str = Depends(owner_token)
):
    project = _get_project(db, project_id, token)
    asset = _original_asset(project)
    if asset is None:
        raise HTTPException(status_code=400, detail="No image uploaded yet")

    catalog = load_catalog()
    folder = settings.project_dir(project.id)
    measurements = measure_project(
        project, asset.width, asset.height, _building_box(asset)
    )
    quantities, estimate, warnings = build_estimate(project, measurements, catalog)

    region_rows = []
    for r in project.regions:
        m = measurements.by_region.get(r.id)
        if not m or (not m.area_sqft and not m.length_ft):
            continue
        region_rows.append(
            {
                "name": r.name or r.category_enum.label,
                "category_label": r.category_enum.label,
                "area_sqft": m.area_sqft,
                "length_ft": m.length_ft,
                "method_label": m.method.label,
                "error_low": m.error_low,
                "error_high": m.error_high,
            }
        )
    region_rows.sort(key=lambda r: -(r["area_sqft"] or r["length_ft"]))

    variant = active_variant(project)
    regions_by_id = {r.id: r for r in project.regions}
    used: dict[str, dict[str, Any]] = {}
    if variant:
        for a in variant.assignments:
            material = catalog.get(a.material_id)
            region = regions_by_id.get(a.region_id)
            if material is None or region is None:
                continue
            entry = used.setdefault(
                material.id,
                {
                    "name": material.name,
                    "durability_years": list(material.durability_years),
                    "maintenance": material.maintenance,
                    "regions": "",
                },
            )
            label = region.name or region.category_enum.label
            entry["regions"] = (
                f"{entry['regions']}, {label}" if entry["regions"] else label
            )

    render_path = None
    if variant and variant.render_asset_id:
        for a in project.images:
            if a.id == variant.render_asset_id:
                candidate = folder / ((a.meta or {}).get("view") or a.filename)
                render_path = candidate if candidate.exists() else folder / a.filename
                break

    # Embed display-sized images; full-resolution JPEGs add megabytes to the
    # PDF without adding anything visible at A4.
    original_for_pdf = image_io.make_thumbnail(
        folder / asset.filename, folder / "report-original.jpg", PDF_IMAGE_EDGE
    )
    if render_path is not None and render_path.exists():
        render_path = image_io.make_thumbnail(
            render_path, folder / "report-render.jpg", PDF_IMAGE_EDGE
        )

    out_path = folder / "report.pdf"
    build_report(
        out_path,
        project_name=project.name,
        original_image=original_for_pdf,
        render_image=render_path,
        regions=region_rows,
        quantities=quantities,
        estimate=estimate,
        materials_used=list(used.values()),
        cost_settings={**default_cost_settings(), **(project.cost_settings or {})},
        warnings=warnings,
        catalog_disclaimer=catalog.disclaimer,
    )

    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in project.name).strip()
    return FileResponse(
        out_path,
        media_type="application/pdf",
        filename=f"{safe or 'renovation'}-estimate.pdf",
    )
