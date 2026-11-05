import { useRef, useState } from "react";
import { useStore } from "../store";

/** Example photos for anyone trying the tool without a photo of their own handy. */
const SAMPLE_PHOTOS: { id: string; label: string; src: string; file: string; mime: string }[] = [
  {
    id: "sample-1",
    label: "Modern stone exterior",
    src: "/samples/sample-1-modern-stone.webp",
    file: "sample-1-modern-stone.webp",
    mime: "image/webp",
  },
  {
    id: "sample-2",
    label: "Brick bungalow",
    src: "/samples/sample-2-brick-bungalow.webp",
    file: "sample-2-brick-bungalow.webp",
    mime: "image/webp",
  },
  {
    id: "sample-3",
    label: "Gray cottage",
    src: "/samples/sample-3-gray-cottage.avif",
    file: "sample-3-gray-cottage.avif",
    mime: "image/avif",
  },
];

export function UploadStep() {
  const { createProject, projects, openProject, deleteProject, busy } = useStore();
  const [dragging, setDragging] = useState(false);
  const [name, setName] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const submit = (file: File | undefined) => {
    if (!file) return;
    const fallback = file.name.replace(/\.[^.]+$/, "").replace(/[-_]+/g, " ");
    void createProject(file, name.trim() || fallback || "Untitled renovation");
  };

  const useSample = async (sample: (typeof SAMPLE_PHOTOS)[number]) => {
    const response = await fetch(sample.src);
    const blob = await response.blob();
    const file = new File([blob], sample.file, { type: sample.mime });
    void createProject(file, name.trim() || sample.label);
  };

  return (
    <div className="center-wrap">
      <div className="uploader">
        <h1 style={{ fontSize: 26, marginBottom: 6 }}>Plan your exterior renovation</h1>
        <p className="muted" style={{ marginBottom: 22 }}>
          Upload one photo of the front of the house. You will mark its surfaces, try
          materials on them, and get measured quantities with a transparent cost
          breakdown — all computed on your own machine.
        </p>

        <div
          className={`dropzone ${dragging ? "drag" : ""}`}
          onClick={() => inputRef.current?.click()}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            submit(event.dataTransfer.files?.[0]);
          }}
        >
          <div className="big">📷</div>
          <h3>Drop a photo here, or click to choose</h3>
          <p>
            JPG, PNG, WEBP, AVIF or HEIC · up to 25 MB
            <br />
            Stand square-on to the facade in daylight for the best result.
          </p>
          <input
            ref={inputRef}
            type="file"
            accept="image/*,.avif,.heic,.heif,.jfif"
            hidden
            onChange={(event) => submit(event.target.files?.[0])}
          />
        </div>

        <div className="section-title" style={{ marginTop: 22 }}>
          Don't have a photo handy? Try one of these
        </div>
        <div className="project-grid sample-grid">
          {SAMPLE_PHOTOS.map((sample) => (
            <button
              key={sample.id}
              className="project-tile sample-tile"
              onClick={() => void useSample(sample)}
              disabled={!!busy}
            >
              <img className="thumb" src={sample.src} alt={sample.label} loading="lazy" />
              <div className="body">
                <div style={{ fontWeight: 650, fontSize: 13 }}>{sample.label}</div>
              </div>
            </button>
          ))}
        </div>

        <label className="field" style={{ marginTop: 22 }}>
          <span>Project name (optional)</span>
          <input
            type="text"
            value={name}
            placeholder="e.g. Front elevation refresh"
            onChange={(event) => setName(event.target.value)}
          />
        </label>

        {projects.length > 0 && (
          <>
            <div className="section-title" style={{ marginTop: 28 }}>
              Your saved projects
            </div>
            <div className="project-grid">
              {projects.map((item) => (
                <div key={item.id} className="project-tile">
                  <button
                    className="project-tile"
                    style={{ border: "none", width: "100%" }}
                    onClick={() => void openProject(item.id)}
                    disabled={!!busy}
                  >
                    <div
                      className="thumb"
                      style={{
                        background: "var(--surface-2)",
                        display: "grid",
                        placeItems: "center",
                        fontSize: 26,
                      }}
                    >
                      🏠
                    </div>
                    <div className="body">
                      <div style={{ fontWeight: 650, fontSize: 13 }}>{item.name}</div>
                      <div className="tiny muted">
                        {new Date(item.updated_at).toLocaleDateString()}
                      </div>
                    </div>
                  </button>
                  <div style={{ padding: "0 12px 10px" }}>
                    <button
                      className="btn sm danger"
                      onClick={() => void deleteProject(item.id)}
                      disabled={!!busy}
                    >
                      Delete
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
