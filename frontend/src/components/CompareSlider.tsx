import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { useAuthedImage } from "./CanvasEditor";

interface Props {
  beforeUrl: string | null;
  afterUrl: string | null;
}

/**
 * Before/after wipe.
 *
 * Both images are rendered at the exact same pixel box, computed once from
 * the container size and the photo's own aspect ratio -- never from a CSS
 * percentage. That matters because the "after" image sits inside a div that
 * is only `position * 100%` wide (the clip window); if its own width is ever
 * expressed as a percentage of *that* box -- or clamped against it by a
 * shared `max-width: 100%` rule, which is what the previous version did --
 * the browser scales the image down to fit the clip window instead of just
 * clipping it, so the two photos visibly stop lining up as you drag. Fixed
 * pixel widths on both images, with `maxWidth`/`maxHeight: none` to defeat
 * any inherited constraint, avoids that entirely.
 */
export function CompareSlider({ beforeUrl, afterUrl }: Props) {
  const before = useAuthedImage(beforeUrl);
  const after = useAuthedImage(afterUrl);
  const hostRef = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState({ width: 0, height: 0 });
  const [position, setPosition] = useState(0.5);
  const [dragging, setDragging] = useState(false);

  // Fit the photo's own aspect ratio inside whatever space the parent grants,
  // the same way CanvasEditor sizes the canvas -- so layout never depends on
  // the <img> element's own intrinsic sizing, which is what CSS was fighting.
  useEffect(() => {
    const host = hostRef.current;
    if (!host || !before) return;

    const parent = host.parentElement;
    if (!parent) return;

    const compute = () => {
      const availW = parent.clientWidth;
      const availH = parent.clientHeight || window.innerHeight * 0.7;
      const ratio = before.naturalWidth / before.naturalHeight || 1;
      let width = availW;
      let height = width / ratio;
      if (height > availH) {
        height = availH;
        width = height * ratio;
      }
      setBox({ width: Math.round(width), height: Math.round(height) });
    };

    compute();
    const observer = new ResizeObserver(compute);
    observer.observe(parent);
    return () => observer.disconnect();
  }, [before]);

  const update = useCallback((clientX: number) => {
    const host = hostRef.current;
    if (!host) return;
    const rect = host.getBoundingClientRect();
    setPosition(Math.min(1, Math.max(0, (clientX - rect.left) / rect.width)));
  }, []);

  useEffect(() => {
    if (!dragging) return;
    const move = (event: PointerEvent) => update(event.clientX);
    const up = () => setDragging(false);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
  }, [dragging, update]);

  if (!before) {
    return (
      <div className="empty">
        <div className="spinner dark" style={{ margin: "0 auto 10px" }} />
        Loading photo…
      </div>
    );
  }

  const ready = box.width > 0 && box.height > 0;
  const imgStyle: CSSProperties = ready
    ? {
        position: "absolute",
        top: 0,
        left: 0,
        width: box.width,
        height: box.height,
        maxWidth: "none",
        maxHeight: "none",
        display: "block",
      }
    : { display: "none" };

  return (
    <div
      className="compare"
      ref={hostRef}
      style={ready ? { width: box.width, height: box.height } : { width: 1, height: 1 }}
      onPointerDown={(event) => {
        if (!after) return;
        setDragging(true);
        update(event.clientX);
      }}
    >
      <img src={before.src} alt="The house as it is today" draggable={false} style={imgStyle} />

      {after && (
        <>
          <div
            className="after"
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: Math.round(box.width * position),
              height: box.height,
              overflow: "hidden",
            }}
          >
            <img
              src={after.src}
              alt="The house with the selected materials"
              draggable={false}
              style={imgStyle}
            />
          </div>
          <div
            className="handle"
            style={{
              position: "absolute",
              top: 0,
              height: box.height,
              left: Math.round(box.width * position) - 1.5,
            }}
          />
        </>
      )}

      <span className="tag" style={{ left: 10 }}>
        Original
      </span>
      {after && (
        <span className="tag" style={{ right: 10 }}>
          Redesigned
        </span>
      )}
    </div>
  );
}
