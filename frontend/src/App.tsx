import { useEffect, useState } from "react";
import { STEPS, useStore, type StepId } from "./store";
import { UploadStep } from "./steps/UploadStep";
import { SurfacesStep } from "./steps/SurfacesStep";
import { DesignStep } from "./steps/DesignStep";
import { EstimateStep } from "./steps/EstimateStep";
import { ReportStep } from "./steps/ReportStep";

/** Steps that use the split canvas + panel layout. */
const SPLIT_STEPS: StepId[] = ["surfaces", "design"];

export default function App() {
  const {
    init,
    health,
    project,
    step,
    setStep,
    busy,
    error,
    setError,
    toast,
    closeProject,
    renameProject,
    regions,
    regionsDirty,
    saveRegions,
  } = useStore();

  const [nameDraft, setNameDraft] = useState("");

  useEffect(() => {
    void init();
  }, [init]);

  useEffect(() => {
    setNameDraft(project?.name ?? "");
  }, [project?.id, project?.name]);

  const index = STEPS.findIndex((s) => s.id === step);
  const hasProject = !!project;
  const hasRegions = regions.length > 0;

  const canVisit = (id: StepId) => {
    if (id === "upload") return true;
    if (!hasProject) return false;
    if (id === "surfaces") return true;
    return hasRegions;
  };

  /** Flush unsaved outline edits whenever the user leaves an editing step. */
  const go = async (id: StepId) => {
    if (regionsDirty && step === "surfaces") {
      await saveRegions();
    }
    setStep(id);
  };

  const body = () => {
    if (!hasProject || step === "upload") return <UploadStep />;
    switch (step) {
      case "surfaces":
        return <SurfacesStep />;
      case "design":
        return <DesignStep />;
      case "estimate":
        return <EstimateStep />;
      case "report":
        return <ReportStep />;
      default:
        return <UploadStep />;
    }
  };

  const split = hasProject && SPLIT_STEPS.includes(step) && step !== "upload";

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">⌂</span>
          Exterior Renovation Planner
        </div>

        {hasProject && (
          <input
            className="project-name"
            value={nameDraft}
            onChange={(event) => setNameDraft(event.target.value)}
            onBlur={() => void renameProject(nameDraft)}
            onKeyDown={(event) => {
              if (event.key === "Enter") event.currentTarget.blur();
            }}
            aria-label="Project name"
          />
        )}

        <span className="spacer" />

        {health && (
          <span
            className={`badge ${health.offline_mode ? "neutral" : "accent"}`}
            title={
              health.offline_mode
                ? "No AI keys configured — detection, rendering and costing all run locally."
                : "Optional AI providers are configured."
            }
          >
            <span className="dot" />
            {health.offline_mode ? "Runs offline" : "AI enabled"}
          </span>
        )}

        {hasProject && (
          <button className="btn sm ghost" onClick={closeProject}>
            Close project
          </button>
        )}
      </header>

      {hasProject && (
        <nav className="steps">
          {STEPS.map((item, i) => (
            <div key={item.id} style={{ display: "contents" }}>
              {i > 0 && <span className="step-sep" />}
              <button
                className={`step ${item.id === step ? "active" : ""} ${
                  i < index ? "done" : ""
                }`}
                onClick={() => void go(item.id)}
                disabled={!canVisit(item.id) || !!busy}
                title={item.blurb}
              >
                <span className="step-num">{i < index ? "✓" : i + 1}</span>
                {item.label}
              </button>
            </div>
          ))}
        </nav>
      )}

      <div className={`workspace ${split ? "" : "single"}`} style={{ position: "relative" }}>
        {body()}

        {busy && (
          <div className="overlay">
            <div className="box">
              <div className="spinner dark" />
              {busy}…
            </div>
          </div>
        )}
      </div>

      {hasProject && (
        <footer className="footer-bar">
          <button
            className="btn"
            onClick={() => void go(STEPS[Math.max(0, index - 1)].id)}
            disabled={index <= 0 || !!busy}
          >
            ← Back
          </button>

          <span className="spacer" />

          {regionsDirty && <span className="badge warn">Unsaved outline changes</span>}

          <button
            className="btn primary"
            onClick={() => void go(STEPS[Math.min(STEPS.length - 1, index + 1)].id)}
            disabled={index >= STEPS.length - 1 || !!busy || !hasRegions}
          >
            {STEPS[Math.min(STEPS.length - 1, index + 1)].label} →
          </button>
        </footer>
      )}

      {error && (
        <div
          className="notice bad"
          style={{
            position: "fixed",
            bottom: 18,
            left: "50%",
            transform: "translateX(-50%)",
            zIndex: 100,
            maxWidth: 560,
            boxShadow: "var(--shadow-lg)",
            background: "#fff",
          }}
        >
          <span className="icon">✕</span>
          <div style={{ flex: 1 }}>{error}</div>
          <button className="btn sm ghost" onClick={() => setError(null)}>
            Dismiss
          </button>
        </div>
      )}

      {toast && (
        <div
          className="badge accent"
          style={{
            position: "fixed",
            bottom: 18,
            left: "50%",
            transform: "translateX(-50%)",
            zIndex: 100,
            padding: "9px 16px",
            boxShadow: "var(--shadow-lg)",
          }}
        >
          {toast}
        </div>
      )}
    </div>
  );
}
