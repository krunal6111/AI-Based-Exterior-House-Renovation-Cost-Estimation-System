import { useState } from "react";
import { CanvasEditor } from "../components/CanvasEditor";
import { categoryColor, useStore } from "../store";
import type { Point } from "../types";

export function SurfacesStep() {
  const {
    project,
    catalog,
    regions,
    selectedRegionId,
    regionsDirty,
    selectRegion,
    patchRegion,
    addRegion,
    deleteRegion,
    saveRegions,
    redetect,
    busy,
  } = useStore();

  const [drawCategory, setDrawCategory] = useState<string | null>(null);
  const selected = regions.find((r) => r.id === selectedRegionId) ?? null;
  const categories = catalog?.categories ?? [];

  const onDrawComplete = (polygon: Point[]) => {
    if (drawCategory) addRegion(polygon, drawCategory);
    setDrawCategory(null);
  };

  const openings = categories.filter((c) => c.is_opening).map((c) => c.value);
  const parentCandidates = regions.filter(
    (r) => r.id !== selected?.id && !openings.includes(r.category),
  );

  return (
    <>
      <div className="stage-pane">
        <div className="canvas-toolbar">
          <div className="toolbar-group">
            <button
              className={`tool ${drawCategory === "wall" ? "active" : ""}`}
              onClick={() => setDrawCategory(drawCategory === "wall" ? null : "wall")}
            >
              ✏️ Draw wall
            </button>
            <button
              className={`tool ${drawCategory === "window" ? "active" : ""}`}
              onClick={() => setDrawCategory(drawCategory === "window" ? null : "window")}
            >
              ▭ Draw opening
            </button>
            <button
              className={`tool ${drawCategory === "railing" ? "active" : ""}`}
              onClick={() => setDrawCategory(drawCategory === "railing" ? null : "railing")}
            >
              ⌗ Draw railing
            </button>
          </div>
          <div className="toolbar-group">
            <button
              className="tool"
              onClick={() => void redetect()}
              disabled={!!busy}
              title="Throw away these surfaces and detect again from the photo"
            >
              ↻ Detect again
            </button>
          </div>
          {regionsDirty && (
            <div className="toolbar-group">
              <button className="tool active" onClick={() => void saveRegions()}>
                ✓ Save changes
              </button>
            </div>
          )}
        </div>

        <CanvasEditor
          imageUrl={project?.image_url ?? null}
          mode="regions"
          regions={regions}
          selectedId={selectedRegionId}
          onSelect={selectRegion}
          onPolygonChange={(id, polygon) => patchRegion(id, { polygon })}
          drawCategory={drawCategory}
          onDrawComplete={onDrawComplete}
          onDrawCancel={() => setDrawCategory(null)}
          dimOthers
        />

        <div className="canvas-hint">
          {drawCategory
            ? "Click to place corners · click the first corner or press Enter to finish · Esc to cancel"
            : selected
              ? "Drag a white corner to adjust it · double-click a corner to remove it · click the dashed + on any edge to add one"
              : "Click a surface to select it, or use the drawing tools above"}
        </div>
      </div>

      <div className="side-pane">
        <div className="pane-head">
          <h2>Check the surfaces</h2>
          <p>
            These outlines were found automatically and are a starting point, not a
            survey. Fix anything that is wrong, delete what is not part of the
            renovation, and draw whatever was missed — every area and cost downstream
            is computed from exactly these shapes.
          </p>
        </div>

        <div className="pane-body">
          {regions.length === 0 && (
            <div className="empty">
              <div className="big">◰</div>
              No surfaces yet. Use the drawing tools to mark the walls you want to
              renovate.
            </div>
          )}

          {regions.map((region) => {
            const area = region.measurement?.area_sqft ?? 0;
            const length = region.measurement?.length_ft ?? 0;
            const parent = regions.find((r) => r.id === region.parent_id);
            return (
              <div
                key={region.id}
                className={`region-row ${region.id === selectedRegionId ? "selected" : ""}`}
                onClick={() => selectRegion(region.id)}
              >
                <span
                  className="swatch"
                  style={{ background: categoryColor(region.category) }}
                />
                <div className="meta">
                  <div className="name">{region.name}</div>
                  <div className="sub">
                    {region.category_label}
                    {area > 0 && ` · ${area.toFixed(0)} sq ft`}
                    {length > 0 && ` · ${length.toFixed(1)} rft`}
                    {parent && ` · cut from ${parent.name}`}
                    {region.source === "ai" && " · AI-detected"}
                    {region.source === "cv" && " · auto-detected (check this one)"}
                  </div>
                </div>
                <button
                  className="btn sm danger"
                  onClick={(event) => {
                    event.stopPropagation();
                    deleteRegion(region.id);
                  }}
                >
                  ✕
                </button>
              </div>
            );
          })}

          {selected && (
            <div className="card mt">
              <h3>Edit “{selected.name}”</h3>
              <p className="hint">
                {selected.source === "ai" &&
                  `Detected by AI with ${(selected.confidence * 100).toFixed(0)}% confidence. `}
                {selected.source === "cv" &&
                  `Detected by the offline (non-AI) tool with ${(selected.confidence * 100).toFixed(0)}% confidence - this method is rough, so check it carefully. `}
                {selected.polygon.length} corners.
              </p>

              <label className="field">
                <span>Name</span>
                <input
                  type="text"
                  value={selected.name}
                  onChange={(event) =>
                    patchRegion(selected.id, { name: event.target.value })
                  }
                />
              </label>

              <label className="field">
                <span>What is it?</span>
                <select
                  value={selected.category}
                  onChange={(event) => {
                    const value = event.target.value;
                    const info = categories.find((c) => c.value === value);
                    patchRegion(selected.id, {
                      category: value,
                      category_label: info?.label ?? selected.category_label,
                      measure: info?.measure ?? selected.measure,
                    });
                  }}
                >
                  {categories.map((category) => (
                    <option key={category.value} value={category.value}>
                      {category.label}
                    </option>
                  ))}
                </select>
              </label>

              {openings.includes(selected.category) && (
                <label className="field">
                  <span>Cut out of which surface?</span>
                  <select
                    value={selected.parent_id ?? ""}
                    onChange={(event) =>
                      patchRegion(selected.id, {
                        parent_id: event.target.value || null,
                      })
                    }
                  >
                    <option value="">Not cut out of anything</option>
                    {parentCandidates.map((candidate) => (
                      <option key={candidate.id} value={candidate.id}>
                        {candidate.name}
                      </option>
                    ))}
                  </select>
                  <span className="tiny muted" style={{ fontWeight: 400 }}>
                    An opening linked to a wall is subtracted from it, so you are not
                    billed for painting a window.
                  </span>
                </label>
              )}
            </div>
          )}

          {regionsDirty && (
            <button
              className="btn primary block mt"
              onClick={() => void saveRegions()}
              disabled={!!busy}
            >
              Save surface changes
            </button>
          )}
        </div>
      </div>
    </>
  );
}
