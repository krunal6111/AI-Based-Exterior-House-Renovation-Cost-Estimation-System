import { useEffect, useState } from "react";
import { CanvasEditor } from "../components/CanvasEditor";
import { useStore } from "../store";
import type { Point } from "../types";

const METHOD_BADGE: Record<string, { cls: string; text: string }> = {
  user_dimension: { cls: "", text: "Measured" },
  reference_object: { cls: "accent", text: "Scaled" },
  pixel_ratio: { cls: "warn", text: "Rough" },
};

export function MeasureStep() {
  const { project, catalog, regions, selectedRegionId, selectRegion, patchRegion, saveRegions, setCalibration, regionsDirty, busy } =
    useStore();

  const calibration = project?.calibration ?? null;
  const [line, setLine] = useState<Point[]>(calibration?.ref_line ?? []);
  const [refKind, setRefKind] = useState(calibration?.ref_kind ?? "door");
  const [refFt, setRefFt] = useState(calibration?.ref_real_ft ?? 6.9);

  useEffect(() => {
    if (calibration) {
      setLine(calibration.ref_line);
      setRefKind(calibration.ref_kind);
      setRefFt(calibration.ref_real_ft);
    }
  }, [calibration?.ref_line, calibration?.ref_kind, calibration?.ref_real_ft]);

  const references = catalog?.reference_objects ?? [];
  const chosen = references.find((r) => r.id === refKind);
  const confirmed = !!calibration?.confirmed;
  const selected = regions.find((r) => r.id === selectedRegionId) ?? null;

  const apply = (confirm: boolean) => {
    if (line.length < 2) return;
    void setCalibration(refKind, line, refFt, confirm);
  };

  return (
    <>
      <div className="stage-pane">
        <CanvasEditor
          imageUrl={project?.image_url ?? null}
          mode="reference"
          regions={regions}
          selectedId={selectedRegionId}
          onSelect={selectRegion}
          referenceLine={line}
          onReferenceChange={setLine}
        />
        <div className="canvas-hint">
          Drag the two orange handles onto something whose real size you know — the
          front door is usually the safest bet.
        </div>
      </div>

      <div className="side-pane">
        <div className="pane-head">
          <h2>Set the scale</h2>
          <p>
            A photo has no sense of size. Line the handles up against a known
            dimension and every area on this page becomes a real measurement instead
            of a guess.
          </p>
        </div>

        <div className="pane-body">
          {!confirmed && (
            <div className="notice warn">
              <span className="icon">!</span>
              <div>
                <strong>The scale has not been confirmed yet.</strong>
                Until you do, areas and costs are rough — typically out by a quarter
                or more. This is the single biggest lever on accuracy here.
              </div>
            </div>
          )}

          <div className="card">
            <h3>Reference object</h3>
            <p className="hint">{chosen?.note}</p>

            <label className="field">
              <span>What are the handles sitting on?</span>
              <select
                value={refKind}
                onChange={(event) => {
                  const value = event.target.value;
                  setRefKind(value);
                  const match = references.find((r) => r.id === value);
                  if (match) setRefFt(match.real_ft);
                }}
              >
                {references.map((reference) => (
                  <option key={reference.id} value={reference.id}>
                    {reference.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="field">
              <span>Its real size, in feet</span>
              <input
                type="number"
                step="0.1"
                min="0.5"
                value={refFt}
                onChange={(event) => setRefFt(Number(event.target.value))}
              />
            </label>

            {calibration?.note && <p className="tiny muted mono">{calibration.note}</p>}

            <div className="btn-row mt">
              <button
                className="btn primary"
                onClick={() => apply(true)}
                disabled={line.length < 2 || !!busy}
              >
                {confirmed ? "Update scale" : "Confirm this scale"}
              </button>
              <button className="btn" onClick={() => apply(false)} disabled={!!busy}>
                Save without confirming
              </button>
            </div>
          </div>

          <div className="section-title">Measured surfaces</div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Surface</th>
                  <th className="num">Size</th>
                  <th>How</th>
                </tr>
              </thead>
              <tbody>
                {regions
                  .filter((r) => r.measurement)
                  .sort(
                    (a, b) =>
                      (b.measurement?.area_sqft ?? b.measurement?.length_ft ?? 0) -
                      (a.measurement?.area_sqft ?? a.measurement?.length_ft ?? 0),
                  )
                  .map((region) => {
                    const m = region.measurement!;
                    const badge = METHOD_BADGE[m.method ?? "pixel_ratio"];
                    return (
                      <tr
                        key={region.id}
                        onClick={() => selectRegion(region.id)}
                        style={{ cursor: "pointer" }}
                      >
                        <td>
                          {region.name}
                          {m.width_ft > 0 && (
                            <span className="formula">
                              {m.width_ft.toFixed(1)} × {m.height_ft.toFixed(1)} ft
                            </span>
                          )}
                        </td>
                        <td className="num">
                          {m.area_sqft > 0
                            ? `${m.area_sqft.toFixed(0)} sq ft`
                            : `${m.length_ft.toFixed(1)} rft`}
                          <span className="formula">
                            −{(m.error_low * 100).toFixed(0)}% / +
                            {(m.error_high * 100).toFixed(0)}%
                          </span>
                        </td>
                        <td>
                          <span className={`badge ${badge?.cls ?? "warn"}`}>
                            {badge?.text ?? "Rough"}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>

          {selected && (
            <div className="card mt">
              <h3>Override “{selected.name}”</h3>
              <p className="hint">
                If you have measured this surface with a tape, type it in. A supplied
                dimension always beats anything estimated from the photo.
              </p>
              <div className="inline-fields">
                <label className="field">
                  <span>{selected.measure === "length" ? "Length (ft)" : "Width (ft)"}</span>
                  <input
                    type="number"
                    step="0.1"
                    value={selected.override_width_ft ?? ""}
                    placeholder={
                      selected.measurement
                        ? selected.measure === "length"
                          ? selected.measurement.length_ft.toFixed(1)
                          : selected.measurement.width_ft.toFixed(1)
                        : ""
                    }
                    onChange={(event) =>
                      patchRegion(selected.id, {
                        override_width_ft: event.target.value
                          ? Number(event.target.value)
                          : null,
                      })
                    }
                  />
                </label>
                <label className="field">
                  <span>Height (ft)</span>
                  <input
                    type="number"
                    step="0.1"
                    value={selected.override_height_ft ?? ""}
                    placeholder={selected.measurement?.height_ft.toFixed(1) ?? ""}
                    onChange={(event) =>
                      patchRegion(selected.id, {
                        override_height_ft: event.target.value
                          ? Number(event.target.value)
                          : null,
                      })
                    }
                  />
                </label>
              </div>
              <label className="field">
                <span>Or set the area directly (sq ft)</span>
                <input
                  type="number"
                  step="1"
                  value={selected.override_area_sqft ?? ""}
                  placeholder={selected.measurement?.area_sqft.toFixed(0) ?? ""}
                  onChange={(event) =>
                    patchRegion(selected.id, {
                      override_area_sqft: event.target.value
                        ? Number(event.target.value)
                        : null,
                    })
                  }
                />
              </label>

              {selected.measurement?.notes.map((note, index) => (
                <p key={index} className="tiny muted">
                  • {note}
                </p>
              ))}

              {regionsDirty && (
                <button
                  className="btn primary block mt"
                  onClick={() => void saveRegions()}
                  disabled={!!busy}
                >
                  Apply overrides
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
