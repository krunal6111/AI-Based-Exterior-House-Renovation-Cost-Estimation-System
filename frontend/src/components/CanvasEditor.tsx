import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Circle, Group, Image as KImage, Layer, Line, Stage, Text } from "react-konva";
import type Konva from "konva";
import { api } from "../api";
import { categoryColor } from "../store";
import type { Point, Region } from "../types";

/** Loads an image through the owner-token header and hands back an element. */
export function useAuthedImage(url: string | null) {
  const [image, setImage] = useState<HTMLImageElement | null>(null);

  useEffect(() => {
    if (!url) {
      setImage(null);
      return;
    }
    let cancelled = false;
    let objectUrl: string | null = null;

    api
      .authedImage(url)
      .then((blobUrl) => {
        objectUrl = blobUrl;
        const element = new window.Image();
        element.src = blobUrl;
        element.onload = () => {
          if (!cancelled) setImage(element);
        };
      })
      .catch(() => {
        if (!cancelled) setImage(null);
      });

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [url]);

  return image;
}

export type CanvasMode = "view" | "regions" | "reference";

interface Props {
  imageUrl: string | null;
  mode: CanvasMode;
  regions?: Region[];
  selectedId?: string | null;
  onSelect?: (id: string | null) => void;
  onPolygonChange?: (id: string, polygon: Point[]) => void;

  drawCategory?: string | null;
  onDrawComplete?: (polygon: Point[]) => void;
  onDrawCancel?: () => void;

  referenceLine?: Point[];
  onReferenceChange?: (line: Point[]) => void;

  dimOthers?: boolean;
}

export function CanvasEditor({
  imageUrl,
  mode,
  regions = [],
  selectedId = null,
  onSelect,
  onPolygonChange,
  drawCategory = null,
  onDrawComplete,
  onDrawCancel,
  referenceLine,
  onReferenceChange,
  dimOthers = false,
}: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const image = useAuthedImage(imageUrl);
  const [box, setBox] = useState({ width: 800, height: 600 });
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [draft, setDraft] = useState<Point[]>([]);
  const [cursor, setCursor] = useState<Point | null>(null);

  // --- fit the stage to its container ---------------------------------
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      if (width > 0 && height > 0) setBox({ width, height });
    });
    observer.observe(host);
    return () => observer.disconnect();
  }, []);

  const layout = useMemo(() => {
    if (!image) return { scale: 1, offsetX: 0, offsetY: 0, width: 0, height: 0 };
    const base = Math.min(box.width / image.width, box.height / image.height) * 0.95;
    const scale = base * zoom;
    const width = image.width * scale;
    const height = image.height * scale;
    return {
      scale,
      width,
      height,
      offsetX: (box.width - width) / 2 + pan.x,
      offsetY: (box.height - height) / 2 + pan.y,
    };
  }, [image, box, zoom, pan]);

  /** normalised (0..1) -> stage pixels */
  const toStage = useCallback(
    (p: Point): Point => [
      layout.offsetX + p[0] * layout.width,
      layout.offsetY + p[1] * layout.height,
    ],
    [layout],
  );

  /** stage pixels -> normalised (0..1), clamped to the image */
  const toNorm = useCallback(
    (x: number, y: number): Point => [
      Math.min(1, Math.max(0, (x - layout.offsetX) / (layout.width || 1))),
      Math.min(1, Math.max(0, (y - layout.offsetY) / (layout.height || 1))),
    ],
    [layout],
  );

  const flatten = useCallback(
    (polygon: Point[]) => polygon.flatMap((p) => toStage(p)),
    [toStage],
  );

  // --- drawing ---------------------------------------------------------
  const drawing = mode === "regions" && !!drawCategory;

  useEffect(() => {
    if (!drawing) {
      setDraft([]);
      setCursor(null);
    }
  }, [drawing]);

  const finishDraft = useCallback(() => {
    if (draft.length >= 3) onDrawComplete?.(draft);
    setDraft([]);
    setCursor(null);
  }, [draft, onDrawComplete]);

  useEffect(() => {
    if (!drawing) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Enter") finishDraft();
      if (event.key === "Escape") {
        setDraft([]);
        onDrawCancel?.();
      }
      if (event.key === "Backspace" && draft.length) {
        event.preventDefault();
        setDraft((d) => d.slice(0, -1));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawing, draft, finishDraft, onDrawCancel]);

  const handleStageClick = (event: Konva.KonvaEventObject<MouseEvent>) => {
    const stage = event.target.getStage();
    const pointer = stage?.getPointerPosition();
    if (!pointer) return;

    if (drawing) {
      const point = toNorm(pointer.x, pointer.y);
      // Click near the first point to close the shape.
      if (draft.length >= 3) {
        const [fx, fy] = toStage(draft[0]);
        if (Math.hypot(fx - pointer.x, fy - pointer.y) < 12) {
          finishDraft();
          return;
        }
      }
      setDraft((d) => [...d, point]);
      return;
    }

    if (event.target === stage || event.target.name() === "backdrop") {
      onSelect?.(null);
    }
  };

  const handleWheel = (event: Konva.KonvaEventObject<WheelEvent>) => {
    event.evt.preventDefault();
    setZoom((z) => Math.min(6, Math.max(1, z * (event.evt.deltaY > 0 ? 0.9 : 1.1))));
  };

  // --- vertex editing ---------------------------------------------------
  const moveVertex = (region: Region, index: number, x: number, y: number) => {
    const next = region.polygon.map((p, i) => (i === index ? toNorm(x, y) : p));
    onPolygonChange?.(region.id, next);
  };

  const removeVertex = (region: Region, index: number) => {
    if (region.polygon.length <= 3) return;
    onPolygonChange?.(
      region.id,
      region.polygon.filter((_, i) => i !== index),
    );
  };

  /**
   * Insert a vertex right after `afterIndex`, at `point`.
   *
   * This is what lets a polygon grow past its starting corner count, which
   * matters a lot in practice: an auto-detected wall is frequently snapped to
   * a clean quad, but the real wall is an L-shape or has a gable notch cut
   * into it. Exposed as a clickable "+" on every edge midpoint (rendered
   * below) rather than a keyboard-modifier gesture, since a hidden shortcut
   * is not a feature anyone actually finds.
   */
  const insertVertexAt = (region: Region, afterIndex: number, point: Point) => {
    const next = [...region.polygon];
    next.splice(afterIndex + 1, 0, point);
    onPolygonChange?.(region.id, next);
  };

  if (!imageUrl) {
    return (
      <div className="canvas-host" ref={hostRef}>
        <div className="empty">
          <div className="big">🏠</div>
          No photo loaded yet.
        </div>
      </div>
    );
  }

  const handleRadius = 6 / Math.sqrt(zoom);

  return (
    <div
      className="canvas-host"
      ref={hostRef}
      style={{ cursor: drawing ? "crosshair" : "default" }}
    >
      {!image && (
        <div className="overlay">
          <div className="box">
            <div className="spinner dark" />
            Loading photo…
          </div>
        </div>
      )}

      <Stage
        width={box.width}
        height={box.height}
        onClick={handleStageClick}
        onWheel={handleWheel}
        onMouseMove={(event) => {
          if (!drawing) return;
          const pointer = event.target.getStage()?.getPointerPosition();
          if (pointer) setCursor(toNorm(pointer.x, pointer.y));
        }}
        draggable={!drawing}
        onDragEnd={(event) => {
          if (event.target === event.target.getStage()) {
            setPan((p) => ({
              x: p.x + event.target.x(),
              y: p.y + event.target.y(),
            }));
            event.target.position({ x: 0, y: 0 });
          }
        }}
      >
        <Layer>
          {image && (
            <KImage
              image={image}
              x={layout.offsetX}
              y={layout.offsetY}
              width={layout.width}
              height={layout.height}
              name="backdrop"
              listening
            />
          )}
        </Layer>

        <Layer listening={mode !== "view"}>
          {regions.map((region) => {
            if (region.polygon.length < 2) return null;
            const selected = region.id === selectedId;
            const colour = categoryColor(region.category);
            const dim = dimOthers && selectedId !== null && !selected;

            return (
              <Group key={region.id}>
                <Line
                  points={flatten(region.polygon)}
                  closed
                  fill={`${colour}${selected ? "4D" : dim ? "14" : "2E"}`}
                  stroke={colour}
                  strokeWidth={selected ? 2.6 : 1.6}
                  opacity={dim ? 0.45 : 1}
                  dash={region.source === "manual" ? undefined : [7, 4]}
                  onClick={(event) => {
                    event.cancelBubble = true;
                    if (drawing) return;
                    onSelect?.(region.id);
                  }}
                  listening={mode === "regions"}
                />

                {selected && mode === "regions" && (
                  <>
                    {/* Edge midpoints: click to grow the polygon past its
                        starting corner count. Drawn first so the corner
                        handles always sit on top where edges meet. */}
                    {region.polygon.map((point, index) => {
                      const next = region.polygon[(index + 1) % region.polygon.length];
                      const mid: Point = [(point[0] + next[0]) / 2, (point[1] + next[1]) / 2];
                      const [mx, my] = toStage(mid);
                      const midRadius = Math.max(5, handleRadius * 0.75);
                      return (
                        <Group
                          key={`mid-${index}`}
                          x={mx}
                          y={my}
                          onClick={(event) => {
                            event.cancelBubble = true;
                            insertVertexAt(region, index, mid);
                          }}
                          onMouseEnter={(event) => {
                            const stage = event.target.getStage();
                            if (stage) stage.container().style.cursor = "copy";
                          }}
                          onMouseLeave={(event) => {
                            const stage = event.target.getStage();
                            if (stage) stage.container().style.cursor = "default";
                          }}
                        >
                          <Circle
                            radius={midRadius}
                            fill="#ffffff"
                            stroke={colour}
                            strokeWidth={1.4}
                            dash={[3, 2]}
                            opacity={0.85}
                          />
                          <Text
                            text="+"
                            fontSize={midRadius * 1.5}
                            fontStyle="bold"
                            fill={colour}
                            width={midRadius * 2}
                            height={midRadius * 2}
                            offsetX={midRadius}
                            offsetY={midRadius}
                            align="center"
                            verticalAlign="middle"
                            listening={false}
                          />
                        </Group>
                      );
                    })}

                    {region.polygon.map((point, index) => {
                      const [x, y] = toStage(point);
                      return (
                        <Circle
                          key={index}
                          x={x}
                          y={y}
                          radius={handleRadius}
                          fill="#ffffff"
                          stroke={colour}
                          strokeWidth={2.2}
                          draggable
                          onDragMove={(event) =>
                            moveVertex(region, index, event.target.x(), event.target.y())
                          }
                          onDblClick={() => removeVertex(region, index)}
                          onMouseEnter={(event) => {
                            const stage = event.target.getStage();
                            if (stage) stage.container().style.cursor = "grab";
                          }}
                          onMouseLeave={(event) => {
                            const stage = event.target.getStage();
                            if (stage) stage.container().style.cursor = "default";
                          }}
                        />
                      );
                    })}
                  </>
                )}
              </Group>
            );
          })}

          {/* in-progress polygon */}
          {drawing && draft.length > 0 && (
            <Group>
              <Line
                points={[
                  ...flatten(draft),
                  ...(cursor ? toStage(cursor) : []),
                ]}
                stroke={categoryColor(drawCategory ?? "other")}
                strokeWidth={2}
                closed={false}
                dash={[6, 4]}
              />
              {draft.map((point, index) => {
                const [x, y] = toStage(point);
                return (
                  <Circle
                    key={index}
                    x={x}
                    y={y}
                    radius={index === 0 ? handleRadius + 2 : handleRadius}
                    fill={index === 0 ? categoryColor(drawCategory ?? "other") : "#fff"}
                    stroke={categoryColor(drawCategory ?? "other")}
                    strokeWidth={2}
                  />
                );
              })}
            </Group>
          )}
        </Layer>

        {/* calibration reference */}
        {mode === "reference" && referenceLine && referenceLine.length >= 2 && (
          <Layer>
            <Line
              points={flatten(referenceLine)}
              stroke="#b4572c"
              strokeWidth={3}
              dash={[9, 5]}
            />
            {referenceLine.map((point, index) => {
              const [x, y] = toStage(point);
              return (
                <Circle
                  key={index}
                  x={x}
                  y={y}
                  radius={9}
                  fill="#fff"
                  stroke="#b4572c"
                  strokeWidth={3}
                  draggable
                  onDragMove={(event) => {
                    const next = referenceLine.map((p, i) =>
                      i === index ? toNorm(event.target.x(), event.target.y()) : p,
                    );
                    onReferenceChange?.(next);
                  }}
                  onMouseEnter={(event) => {
                    const stage = event.target.getStage();
                    if (stage) stage.container().style.cursor = "grab";
                  }}
                  onMouseLeave={(event) => {
                    const stage = event.target.getStage();
                    if (stage) stage.container().style.cursor = "default";
                  }}
                />
              );
            })}
            <Text
              x={toStage(referenceLine[0])[0] + 14}
              y={toStage(referenceLine[0])[1]}
              text="drag me onto something you know the size of"
              fontSize={12}
              fontStyle="600"
              fill="#b4572c"
            />
          </Layer>
        )}
      </Stage>

      {zoom !== 1 && (
        <div className="canvas-toolbar" style={{ top: "auto", bottom: 12, left: 12, right: "auto" }}>
          <div className="toolbar-group">
            <button
              className="tool"
              onClick={() => {
                setZoom(1);
                setPan({ x: 0, y: 0 });
              }}
            >
              Reset view ({zoom.toFixed(1)}×)
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
