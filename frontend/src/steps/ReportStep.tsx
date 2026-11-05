import { useState } from "react";
import { api } from "../api";
import { CompareSlider } from "../components/CompareSlider";
import { useMoney, useStore } from "../store";

export function ReportStep() {
  const { project, catalog, estimate, setError, setToast } = useStore();
  const money = useMoney();
  const [downloading, setDownloading] = useState(false);

  const variant = project?.variants.find((v) => v.id === project.active_variant_id);

  const download = async () => {
    if (!project) return;
    setDownloading(true);
    try {
      await api.downloadReport(project.id, `${project.name || "renovation"}-estimate.pdf`);
      setToast("Report downloaded");
    } catch (error) {
      setError(error instanceof Error ? error.message : "Could not build the report");
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="scroll-pane">
      <div style={{ maxWidth: 1000, margin: "0 auto" }}>
        <h1 style={{ fontSize: 24, marginBottom: 6 }}>{project?.name}</h1>
        <p className="muted mb">
          Everything below goes into a PDF you can hand to a contractor — the two
          photos, the measured surfaces with the method used for each, the
          quantities with their formulas, the full cost breakdown, and a page
          stating exactly what the estimate assumes.
        </p>

        <div className="card">
          <div className="row" style={{ justifyContent: "space-between" }} >
            <div>
              <h3>Renovation report</h3>
              <p className="hint" style={{ marginBottom: 0 }}>
                PDF · A4 · includes assumptions and limitations
              </p>
            </div>
            <button className="btn primary" onClick={download} disabled={downloading}>
              {downloading ? <span className="spinner" /> : "⤓"} Download PDF
            </button>
          </div>
        </div>

        {variant?.render_url && (
          <div className="card mt">
            <h3>Before and after</h3>
            <p className="hint">
              The redesigned image re-surfaces only the regions you marked. Geometry,
              windows and roofline are untouched.
            </p>
            <div style={{ display: "grid", placeItems: "center" }}>
              <CompareSlider
                beforeUrl={project?.image_url ?? null}
                afterUrl={variant.render_url}
              />
            </div>
          </div>
        )}

        {estimate && estimate.lines.length > 0 && (
          <>
            <div className="headline mt">
              <div className="label">Estimated project cost</div>
              <div className="value">{money(estimate.grand_total)}</div>
              <div className="range">
                Budget range {money(estimate.range_low)} – {money(estimate.range_high)}
              </div>
            </div>

            <div className="card mt">
              <h3>Cost by trade</h3>
              {Object.entries(estimate.group_subtotals).map(([group, amount]) => (
                <div className="kv" key={group}>
                  <span className="k">{group}</span>
                  <span className="v">{money(amount)}</span>
                </div>
              ))}
              <div className="kv">
                <span className="k">
                  <strong>Total including add-ons and tax</strong>
                </span>
                <span className="v">{money(estimate.grand_total)}</span>
              </div>
            </div>
          </>
        )}

        <div className="card mt">
          <h3>What this estimate assumes</h3>
          <p className="hint">
            Read these before you commit money. They are the honest boundaries of
            what a single photograph can tell you.
          </p>
          {(catalog?.limitations ?? []).map((limitation, index) => (
            <p key={index} className="small muted">
              • {limitation}
            </p>
          ))}
        </div>
      </div>
    </div>
  );
}
