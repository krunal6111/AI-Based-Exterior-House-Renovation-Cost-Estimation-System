import { useMemo, useState } from "react";
import { CanvasEditor } from "../components/CanvasEditor";
import { CompareSlider } from "../components/CompareSlider";
import { categoryColor, useMoney, useStore } from "../store";
import type { Material } from "../types";

function swatchStyle(material: Material, colour?: string) {
  const render = (material.render ?? {}) as Record<string, string>;
  const base = colour || render.base_color || "#cccccc";
  const texture = render.texture;
  if (!texture || render.kind === "solid") return { background: base };
  // Cheap visual cue for the texture family, so the grid is scannable.
  const stripes: Record<string, string> = {
    stone_ashlar: `repeating-linear-gradient(0deg, ${base}, ${base} 9px, rgba(0,0,0,.18) 9px, rgba(0,0,0,.18) 11px)`,
    stone_ledger: `repeating-linear-gradient(0deg, ${base}, ${base} 4px, rgba(0,0,0,.22) 4px, rgba(0,0,0,.22) 6px)`,
    tile: `repeating-linear-gradient(0deg, ${base}, ${base} 12px, rgba(0,0,0,.14) 12px, rgba(0,0,0,.14) 14px), repeating-linear-gradient(90deg, ${base}, ${base} 20px, rgba(0,0,0,.14) 20px, rgba(0,0,0,.14) 22px)`,
    wood: `repeating-linear-gradient(0deg, ${base}, ${base} 6px, rgba(0,0,0,.16) 6px, rgba(0,0,0,.16) 8px)`,
    marble: `linear-gradient(135deg, ${base}, #fff 40%, ${base})`,
    metal_brushed: `linear-gradient(180deg, #fff2, transparent), ${base}`,
    plaster: base,
  };
  return { background: stripes[texture] ?? base };
}

export function DesignStep() {
  const {
    project,
    catalog,
    regions,
    selectedRegionId,
    selectRegion,
    assignMaterial,
    renderDesign,
    busy,
  } = useStore();
  const money = useMoney();
  const [showCompare, setShowCompare] = useState(true);

  const variant = project?.variants.find((v) => v.id === project.active_variant_id);
  const assignments = useMemo(
    () => new Map((variant?.assignments ?? []).map((a) => [a.region_id, a])),
    [variant],
  );

  const selected = regions.find((r) => r.id === selectedRegionId) ?? null;
  const current = selected ? assignments.get(selected.id) : undefined;
  const currentMaterial = catalog?.materials.find((m) => m.id === current?.material_id);

  const options = useMemo(() => {
    if (!catalog || !selected) return [];
    return catalog.materials.filter(
      (m) => m.assignable && m.applies_to.includes(selected.category),
    );
  }, [catalog, selected]);

  const renderUrl = variant?.render_url ?? null;
  const assignedCount = assignments.size;

  const estimateFor = (material: Material) => {
    const m = selected?.measurement;
    if (!m) return null;
    const basis = material.material_rate.unit === "rft" ? m.length_ft : m.area_sqft;
    if (!basis) return null;
    // Rough per-surface figure so the choice has a visible price consequence.
    const labourBasis = material.labour_rate.unit === "rft" ? m.length_ft : m.area_sqft;
    if (material.material_rate.unit === "litre") {
      const perCoat = Number(material.coverage.sqft_per_litre_per_coat ?? 100);
      const coats = Number(material.coverage.default_coats ?? 2);
      const litres = (basis * coats) / perCoat;
      return litres * material.material_rate.default + labourBasis * material.labour_rate.default;
    }
    return basis * material.material_rate.default + labourBasis * material.labour_rate.default;
  };

  return (
    <>
      <div className="stage-pane">
        <div className="canvas-toolbar">
          <div className="toolbar-group">
            <button
              className={`tool ${!showCompare ? "active" : ""}`}
              onClick={() => setShowCompare(false)}
            >
              ◰ Pick surfaces
            </button>
            <button
              className={`tool ${showCompare ? "active" : ""}`}
              onClick={() => setShowCompare(true)}
              disabled={!renderUrl}
            >
              ⇆ Before / after
            </button>
          </div>
          <div className="toolbar-group">
            <button
              className="tool active"
              onClick={() => void renderDesign()}
              disabled={!!busy || assignedCount === 0}
            >
              ✨ Render the house
            </button>
          </div>
        </div>

        {showCompare && renderUrl ? (
          <div className="canvas-host" style={{ padding: 28 }}>
            <CompareSlider beforeUrl={project?.image_url ?? null} afterUrl={renderUrl} />
          </div>
        ) : (
          <CanvasEditor
            imageUrl={project?.image_url ?? null}
            mode="regions"
            regions={regions}
            selectedId={selectedRegionId}
            onSelect={selectRegion}
            dimOthers
          />
        )}

        <div className="canvas-hint">
          {showCompare && renderUrl
            ? "Drag the divider to compare. The building's geometry is never altered — only the surface finish."
            : "Click a surface, then pick a material for it on the right."}
        </div>
      </div>

      <div className="side-pane">
        <div className="pane-head">
          <h2>Try materials</h2>
          <p>
            Pick a surface, then a material. The preview uses your photo's own
            lighting and perspective, so what you see is your house — not a
            showroom render of someone else's.
          </p>
        </div>

        <div className="pane-body">
          {assignedCount === 0 && (
            <div className="notice info">
              <span className="icon">i</span>
              <div>
                <strong>Nothing applied yet.</strong>
                Select a surface below and choose a material to get started.
              </div>
            </div>
          )}

          <div className="section-title">Surfaces</div>
          {regions
            .filter((r) => r.measurement)
            .map((region) => {
              const assigned = assignments.get(region.id);
              const material = catalog?.materials.find((m) => m.id === assigned?.material_id);
              return (
                <div
                  key={region.id}
                  className={`region-row ${region.id === selectedRegionId ? "selected" : ""}`}
                  onClick={() => selectRegion(region.id)}
                >
                  <span
                    className="swatch"
                    style={
                      material
                        ? swatchStyle(material, assigned?.params?.color as string)
                        : { background: categoryColor(region.category) }
                    }
                  />
                  <div className="meta">
                    <div className="name">{region.name}</div>
                    <div className="sub">
                      {material ? material.name : "No material yet"}
                      {region.measurement!.area_sqft > 0 &&
                        ` · ${region.measurement!.area_sqft.toFixed(0)} sq ft`}
                      {region.measurement!.length_ft > 0 &&
                        ` · ${region.measurement!.length_ft.toFixed(1)} rft`}
                    </div>
                  </div>
                  {material && (
                    <button
                      className="btn sm ghost"
                      onClick={(event) => {
                        event.stopPropagation();
                        void assignMaterial(region.id, null);
                      }}
                    >
                      Clear
                    </button>
                  )}
                </div>
              );
            })}

          {selected && (
            <>
              <div className="section-title">Materials for {selected.category_label}</div>
              {options.length === 0 && (
                <p className="small muted">
                  No catalog material applies to this surface type. Change its type on
                  the Surfaces step if that looks wrong.
                </p>
              )}
              <div className="material-grid">
                {options.map((material) => {
                  const price = estimateFor(material);
                  return (
                    <button
                      key={material.id}
                      className={`material-card ${
                        current?.material_id === material.id ? "selected" : ""
                      }`}
                      onClick={() => void assignMaterial(selected.id, material.id)}
                      disabled={!!busy}
                    >
                      <span className="chip" style={swatchStyle(material)} />
                      <span className="label">
                        {material.name}
                        <small>
                          {price !== null
                            ? `≈ ${money(price)}`
                            : `${money(material.material_rate.default)}/${material.material_rate.unit}`}
                        </small>
                      </span>
                    </button>
                  );
                })}
              </div>

              {currentMaterial && (
                <div className="card mt">
                  <h3>{currentMaterial.name}</h3>
                  <p className="hint">{currentMaterial.short}</p>

                  {currentMaterial.palette.length > 0 && (
                    <>
                      <div className="section-title" style={{ marginTop: 4 }}>
                        Colour
                      </div>
                      <div className="palette">
                        {currentMaterial.palette.map((colour) => (
                          <button
                            key={colour}
                            className={`chip-btn ${
                              (current?.params?.color ?? (currentMaterial.render as Record<string, string>)?.base_color) === colour
                                ? "selected"
                                : ""
                            }`}
                            style={{ background: colour }}
                            title={colour}
                            onClick={() =>
                              void assignMaterial(selected.id, currentMaterial.id, {
                                ...(current?.params ?? {}),
                                color: colour,
                              })
                            }
                          />
                        ))}
                      </div>
                    </>
                  )}

                  <div className="kv mt">
                    <span className="k">Expected life</span>
                    <span className="v">
                      {currentMaterial.durability_years[1]
                        ? `${currentMaterial.durability_years[0]}–${currentMaterial.durability_years[1]} years`
                        : "—"}
                    </span>
                  </div>
                  <div className="kv">
                    <span className="k">Material rate</span>
                    <span className="v">
                      {money(currentMaterial.material_rate.default)}/
                      {currentMaterial.material_rate.unit}
                    </span>
                  </div>
                  <div className="kv">
                    <span className="k">Labour rate</span>
                    <span className="v">
                      {money(currentMaterial.labour_rate.default)}/
                      {currentMaterial.labour_rate.unit}
                    </span>
                  </div>

                  {currentMaterial.maintenance && (
                    <p className="small muted mt">
                      <strong>Upkeep.</strong> {currentMaterial.maintenance}
                    </p>
                  )}
                  {currentMaterial.source_note && (
                    <p className="tiny muted">{currentMaterial.source_note}</p>
                  )}
                </div>
              )}
            </>
          )}

          <button
            className="btn primary block mt"
            onClick={() => void renderDesign()}
            disabled={!!busy || assignedCount === 0}
          >
            {renderUrl ? "Re-render with these materials" : "Render the house"}
          </button>
        </div>
      </div>
    </>
  );
}
