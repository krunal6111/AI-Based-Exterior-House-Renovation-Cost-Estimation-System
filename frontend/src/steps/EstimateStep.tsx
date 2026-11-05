import { useEffect, useMemo, useState } from "react";
import { useMoney, useStore } from "../store";
import type { RateOverride } from "../types";

export function EstimateStep() {
  const { project, catalog, estimate, refreshEstimate, saveRates, saveCostSettings, busy } =
    useStore();
  const money = useMoney();
  const [drafts, setDrafts] = useState<Record<string, { material?: number; labour?: number }>>({});

  useEffect(() => {
    if (!estimate) void refreshEstimate();
  }, [estimate, refreshEstimate]);

  const existing = useMemo(
    () => new Map((project?.rate_overrides ?? []).map((o) => [o.material_id, o])),
    [project?.rate_overrides],
  );

  const settings = project?.cost_settings ?? {};
  const dirty = Object.keys(drafts).length > 0;

  const commitRates = () => {
    const merged = new Map<string, RateOverride>();
    existing.forEach((value, key) => merged.set(key, { ...value }));
    Object.entries(drafts).forEach(([materialId, patch]) => {
      const row = merged.get(materialId) ?? {
        material_id: materialId,
        material_rate: null,
        labour_rate: null,
        wastage_pct: null,
      };
      if (patch.material !== undefined) row.material_rate = patch.material;
      if (patch.labour !== undefined) row.labour_rate = patch.labour;
      merged.set(materialId, row);
    });
    void saveRates([...merged.values()]);
    setDrafts({});
  };

  const setSetting = (key: string, value: number | boolean) =>
    void saveCostSettings({ ...(settings as Record<string, number | boolean>), [key]: value });

  if (!estimate) {
    return (
      <div className="scroll-pane">
        <div className="empty">
          <div className="spinner dark" style={{ margin: "0 auto 12px" }} />
          Working out quantities and costs…
        </div>
      </div>
    );
  }

  const hasWork = estimate.lines.length > 0;

  return (
    <div className="scroll-pane">
      <div style={{ maxWidth: 1080, margin: "0 auto" }}>
        {estimate.warnings.map((warning, index) => (
          <div key={index} className="notice warn">
            <span className="icon">!</span>
            <div>{warning}</div>
          </div>
        ))}

        {!hasWork && (
          <div className="notice info">
            <span className="icon">i</span>
            <div>
              <strong>Nothing to cost yet.</strong>
              Go back to the Material Design step and apply a material to at least one surface.
            </div>
          </div>
        )}

        {hasWork && (
          <>
            <div className="headline">
              <div className="label">Estimated project cost</div>
              <div className="value">{money(estimate.grand_total)}</div>
              <div className="range">
                Likely between {money(estimate.range_low)} and {money(estimate.range_high)},
                allowing for uncertainty in the measured areas
              </div>
            </div>

            <div className="stat-row">
              <div className="stat">
                <div className="label">Surface</div>
                <div className="value">{estimate.total_area_sqft.toFixed(0)} sq ft</div>
              </div>
              {estimate.total_length_ft > 0 && (
                <div className="stat">
                  <div className="label">Linear work</div>
                  <div className="value">{estimate.total_length_ft.toFixed(0)} rft</div>
                </div>
              )}
              <div className="stat">
                <div className="label">Materials</div>
                <div className="value">{money(estimate.material_subtotal)}</div>
              </div>
              <div className="stat">
                <div className="label">Labour</div>
                <div className="value">{money(estimate.labour_subtotal)}</div>
              </div>
            </div>

            {/* ---------------------------------------------- quantities -- */}
            <div className="section-title">What you need to buy</div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Material</th>
                    <th className="num">Quantity</th>
                    <th>To purchase</th>
                    <th>How it was worked out</th>
                  </tr>
                </thead>
                <tbody>
                  {estimate.quantities.map((q) => (
                    <tr key={q.material_id} className={q.derived ? "derived" : ""}>
                      <td>
                        {q.material_name}
                        {q.derived && " (included)"}
                        {q.region_names.length > 0 && (
                          <span className="formula">{q.region_names.join(", ")}</span>
                        )}
                      </td>
                      <td className="num">
                        {q.quantity.toLocaleString("en-IN", {
                          maximumFractionDigits: 2,
                        })}{" "}
                        {q.unit}
                      </td>
                      <td>
                        {q.packs.length > 0
                          ? q.packs.map((p) => `${p.count} × ${p.size} L`).join(" + ")
                          : q.boxes
                            ? `${q.pieces} tiles (${q.boxes} boxes)`
                            : `${q.purchase_quantity.toFixed(1)} ${q.unit}`}
                      </td>
                      <td>
                        <span className="formula">{q.formula}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* --------------------------------------------------- rates -- */}
            <div className="section-title">
              Rates — edit any of these to match your own quotations
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Item</th>
                    <th className="num">Material rate</th>
                    <th className="num">Labour rate</th>
                    <th className="num">Material</th>
                    <th className="num">Labour</th>
                    <th className="num">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {estimate.lines.map((line) => {
                    const material = catalog?.materials.find((m) => m.id === line.material_id);
                    const draft = drafts[line.material_id] ?? {};
                    return (
                      <tr key={line.material_id} className={line.derived ? "derived" : ""}>
                        <td>
                          {line.material_name}
                          {line.overridden && (
                            <span className="badge accent" style={{ marginLeft: 6 }}>
                              edited
                            </span>
                          )}
                          <span className="formula">{line.formula}</span>
                        </td>
                        <td className="num">
                          <input
                            className="sm num"
                            type="number"
                            style={{ width: 84, textAlign: "right" }}
                            value={draft.material ?? line.material_rate}
                            onChange={(event) =>
                              setDrafts((d) => ({
                                ...d,
                                [line.material_id]: {
                                  ...d[line.material_id],
                                  material: Number(event.target.value),
                                },
                              }))
                            }
                          />
                          <span className="formula">
                            per {material?.material_rate.unit ?? line.unit}
                            {material &&
                              ` · market ${material.material_rate.min}–${material.material_rate.max}`}
                          </span>
                        </td>
                        <td className="num">
                          <input
                            className="sm num"
                            type="number"
                            style={{ width: 76, textAlign: "right" }}
                            value={draft.labour ?? line.labour_rate}
                            onChange={(event) =>
                              setDrafts((d) => ({
                                ...d,
                                [line.material_id]: {
                                  ...d[line.material_id],
                                  labour: Number(event.target.value),
                                },
                              }))
                            }
                          />
                          <span className="formula">per {line.labour_unit}</span>
                        </td>
                        <td className="num">{money(line.material_cost)}</td>
                        <td className="num">{money(line.labour_cost)}</td>
                        <td className="num">{money(line.total)}</td>
                      </tr>
                    );
                  })}
                  <tr className="total">
                    <td colSpan={3}>Works subtotal</td>
                    <td className="num">{money(estimate.material_subtotal)}</td>
                    <td className="num">{money(estimate.labour_subtotal)}</td>
                    <td className="num">{money(estimate.subtotal)}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            {dirty && (
              <div className="btn-row mt">
                <button className="btn primary" onClick={commitRates} disabled={!!busy}>
                  Recalculate with these rates
                </button>
                <button className="btn ghost" onClick={() => setDrafts({})}>
                  Discard changes
                </button>
              </div>
            )}

            {/* ------------------------------------------------- add-ons -- */}
            <div className="section-title">On top of the works</div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Item</th>
                    <th>Basis</th>
                    <th className="num">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>Works subtotal</td>
                    <td className="muted">materials + labour</td>
                    <td className="num">{money(estimate.subtotal)}</td>
                  </tr>
                  {estimate.addons.map((addon) => (
                    <tr key={addon.key}>
                      <td>{addon.label}</td>
                      <td className="muted tiny">{addon.basis}</td>
                      <td className="num">{money(addon.amount)}</td>
                    </tr>
                  ))}
                  <tr className="total">
                    <td colSpan={2}>Estimated project total</td>
                    <td className="num">{money(estimate.grand_total)}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div className="card mt">
              <h3>Cost settings</h3>
              <p className="hint">
                These are applied in order: scaffolding, transport, overhead,
                contingency, then GST on the running total.
              </p>
              <div className="inline-fields">
                <label className="field">
                  <span>Scaffolding (per sq ft)</span>
                  <input
                    type="number"
                    step="0.5"
                    value={Number(settings.scaffolding_rate_per_sqft ?? 0)}
                    onChange={(event) =>
                      setSetting("scaffolding_rate_per_sqft", Number(event.target.value))
                    }
                  />
                </label>
                <label className="field">
                  <span>Transport (lump sum)</span>
                  <input
                    type="number"
                    step="100"
                    value={Number(settings.transport_lumpsum ?? 0)}
                    onChange={(event) =>
                      setSetting("transport_lumpsum", Number(event.target.value))
                    }
                  />
                </label>
                <label className="field">
                  <span>Contractor overhead (%)</span>
                  <input
                    type="number"
                    step="1"
                    value={Number(settings.contractor_overhead_pct ?? 0)}
                    onChange={(event) =>
                      setSetting("contractor_overhead_pct", Number(event.target.value))
                    }
                  />
                </label>
                <label className="field">
                  <span>Contingency (%)</span>
                  <input
                    type="number"
                    step="1"
                    value={Number(settings.contingency_pct ?? 0)}
                    onChange={(event) =>
                      setSetting("contingency_pct", Number(event.target.value))
                    }
                  />
                </label>
                <label className="field">
                  <span>GST (%)</span>
                  <input
                    type="number"
                    step="1"
                    value={Number(settings.gst_pct ?? 0)}
                    onChange={(event) => setSetting("gst_pct", Number(event.target.value))}
                  />
                </label>
              </div>
              <label className="checkline">
                <input
                  type="checkbox"
                  checked={Boolean(settings.scaffolding_enabled)}
                  onChange={(event) =>
                    setSetting("scaffolding_enabled", event.target.checked)
                  }
                />
                Include scaffolding / access
              </label>
              <label className="checkline">
                <input
                  type="checkbox"
                  checked={Boolean(settings.gst_enabled)}
                  onChange={(event) => setSetting("gst_enabled", event.target.checked)}
                />
                Apply GST
              </label>
            </div>

            <div className="card mt">
              <h3>How to read this</h3>
              {estimate.notes.map((note, index) => (
                <p key={index} className="small muted">
                  • {note}
                </p>
              ))}
              {catalog?.disclaimer && (
                <p className="tiny muted mt">{catalog.disclaimer}</p>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
