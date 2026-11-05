# System architecture

Written for a developer picking this up cold.

---

## The shaping constraint

The system must work **with no API keys and no network**. That one decision
drives most of what follows — in particular, it rules out generating the
redesigned image with a diffusion or multimodal model, which turns out to suit
the requirement better anyway: requirement 5.4 asks that the original
building structure be *preserved*, and a generative model redraws geometry it
was only asked to repaint.

So visualisation is a **compositor**, not a generator. It never invents
geometry; it replaces the appearance of surfaces inside outlines the user
controls, lit by the photograph's own shading.

Optional Gemini providers sit behind feature flags for anyone who does have a
key, and degrade silently to the offline path on any failure.

---

## Pipeline

```
                 ┌──────────────────────────────────────────┐
  upload ──────► │ normalise (AVIF/HEIC/JFIF → sRGB JPEG)    │
                 │ EXIF rotate · cap long edge · display copy│
                 └───────────────┬──────────────────────────┘
                                 ▼
                 ┌──────────────────────────────────────────┐
                 │ quality gate   sharpness · exposure ·     │
                 │ resolution · how much frame is building   │
                 └───────────────┬──────────────────────────┘
                                 ▼
                 ┌──────────────────────────────────────────┐
                 │ scene masks   sky / vegetation / building │
                 │ (fixed 760 px working size)               │
                 └───────────────┬──────────────────────────┘
                                 ▼
                 ┌──────────────────────────────────────────┐
                 │ region detection  SLIC → LAB k-means →    │
                 │ components → classify → snap to quads     │
                 └───────────────┬──────────────────────────┘
                                 ▼
                 ╔══════════════════════════════════════════╗
                 ║  USER REVIEW  (non-skippable)            ║
                 ║  polygon editor: drag / add / delete      ║
                 ╚═══════════════┬══════════════════════════╝
                                 ▼
          ┌──────────────────────┴───────────────────────┐
          ▼                                              ▼
 ┌────────────────────┐                      ┌───────────────────────┐
 │ calibration + area │                      │ material assignment   │
 │ reference → ft/px  │                      │ per region, per variant│
 │ rectify → sq ft    │                      └───────────┬───────────┘
 └─────────┬──────────┘                                  ▼
           │                                  ┌───────────────────────┐
           │  real sizes drive texture scale  │ offline renderer      │
           ├─────────────────────────────────►│ shading × material    │
           │                                  └───────────┬───────────┘
           ▼                                              │
 ┌────────────────────┐    ┌──────────────┐               │
 │ quantities         │───►│ costing      │               │
 │ coverage · wastage │    │ + add-ons    │               │
 └────────────────────┘    └──────┬───────┘               │
                                  ▼                       ▼
                        ┌─────────────────────────────────────┐
                        │ PDF report (ReportLab)              │
                        └─────────────────────────────────────┘
```

Note the feedback edge: **measurement feeds rendering.** Knowing a wall is
9 ft tall is what lets a 600×300 mm tile be drawn 600×300 mm.

---

## Layout

```
backend/
  app/
    main.py          FastAPI app, CORS, lifespan
    config.py        pydantic-settings; feature flags default OFF
    db.py            SQLite engine, WAL, session dependency
    models.py        SQLAlchemy 2.0 models
    schemas.py       Pydantic request/response
    enums.py         shared vocabulary, no framework imports
    routers/
      catalog.py     materials, reference objects, assumptions
      projects.py    the whole project lifecycle
    services/
      image_io.py    format normalisation, thumbnails, BGR interop
      quality.py     the 5.1 gate
      scene.py       sky / vegetation / building masks, shading field
      geometry.py    polygons, quad fitting, perspective rectification
      scale.py       px → ft, per-region measurement + error tiers
      textures.py    procedural materials (numpy, no assets)
      renderer.py    the compositor
      quantities.py  coverage, wastage, pack solving
      costing.py     rates, add-ons, totals
      estimator.py   orchestration: measure → quantify → cost → layers
      catalog.py     loads materials.json / assumptions.json
      pdf_report.py  ReportLab report
    providers/
      base.py        SegmentationProvider / RenderProvider protocols
      cv_provider.py offline detection (default)
      gemini_provider.py  optional, flag-gated
      __init__.py    picks a backend, falls back offline
    data/
      materials.json    rates, coverage, palettes, source notes
      assumptions.json  reference sizes, thresholds, cost defaults
  tests/             71 tests
frontend/
  src/
    api.ts           typed client; owner token on every request
    store.ts         zustand store; one place for all mutations
    App.tsx          wizard shell + step gating
    components/
      CanvasEditor.tsx  react-konva polygon editor
      CompareSlider.tsx before/after wipe
    steps/           Upload · Surfaces · Measure · Design · Estimate · Report
docs/
storage/<project-id>/  original.jpg · display.jpg · render-*.jpg · report.pdf
```

---

## Key design decisions

### Geometry is stored normalised (0–1)

Regions persist as fractions of the image, never pixels. The same polygon is
then valid against the 1400 px display copy the browser edits, the 2048 px
working copy the renderer uses, and the 1000 px copy embedded in the PDF.

### Providers behind protocols

`SegmentationProvider` and `RenderProvider` are `typing.Protocol`s.
`providers/__init__.py` asks for Gemini only when a credential *and* a flag
are present, and falls back to the offline implementation on an empty result
or any exception. No router or service knows which backend ran.

A credential is either an AI Studio `GEMINI_API_KEY` or a Vertex AI
`VERTEX_PROJECT` (with `GOOGLE_APPLICATION_CREDENTIALS` read as a real
process environment variable, since that's what Google's own credential
discovery looks for — not something pydantic-settings loading `.env` exports
on its own, which is a distinction that cost real debugging time while
building this). `Settings.ai_backend` picks Vertex when both are configured,
deterministically. `gemini_provider._client()` builds the matching
`genai.Client` for whichever one is set.

Two Vertex-specific quirks the client code works around:

* the Interactions API only answers on `location="global"`, `"us"` or
  `"eu"` — a regional location like `us-central1` 404s even though the
  model is listed in that region's catalog;
* `thinking_level: "minimal"` — documented for this model family, and
  accepted by AI Studio — is rejected by Vertex AI ("Thinking level is
  unsupported"). `"low"` works on both, so it's the configured default
  (`settings.ai_thinking_level`).

### Pure estimation core

`geometry.py`, `scale.py`, `quantities.py` and `costing.py` take plain data
and return plain data — no database, no image, no network. That is what makes
the money maths directly unit-testable, and it is why the same code serves the
API and the PDF without divergence.

### Shading is extracted, not invented

`scene.shading_field()` divides the LAB lightness by a heavily blurred copy of
itself. What survives is shadow, soft occlusion, streaking and surface grain —
a lighting map independent of what the surface is made of. Multiplying a new
material through it is what makes a repainted wall still look photographed.

A separate per-region illumination factor handles the low-frequency part the
shading field deliberately removes, so a wall standing in shade does not come
back sunlit.

### Textures are generated, never shipped

`textures.py` synthesises every material with numpy at the real-world scale
the estimator computed. No binary assets, no licensing, no tiling seams — the
whole rectified plane is drawn in one pass and then warped into the
photograph's perspective.

### Openings are found by geometry, not just by a manual link

`estimator.overlapping_openings()` finds the windows/doors/gates that belong
to a wall two ways: an explicit `parent_id` link (always honoured, useful for
a sliver of overlap too small to qualify on its own), and geometric overlap
-- any opening-category region whose own area sits mostly inside the wall's
polygon, regardless of whether anyone linked it.

The geometric path exists because the explicit-link-only version shipped
first and real testing showed its failure mode immediately: a window almost
nobody actually links gets painted straight over. Both `measure_project`
(for the Shapely boolean-difference area deduction) and `render_layers` (for
the render mask) call the same function, so the two numbers -- the area you
are billed for and the area that gets painted -- can never disagree with each
other. An opening with its own assigned material still renders on top
regardless, since it draws later in the z-order; it is never both deducted
and left blank.

### Identity without authentication

Each browser mints an `owner_token` into `localStorage` and sends it on every
request. It scopes projects per user, which satisfies "handle multiple users
simultaneously" without building auth. **It is not a security boundary** — see
`LIMITATIONS.md` §8.

---

## Data model

```
Project ──┬── ImageAsset   (original | display | render)
          ├── Region       (polygon 0–1, category, parent_id, overrides)
          ├── Calibration  (reference line + real size, confirmed flag)   1:1
          ├── DesignVariant ── Assignment (region → material + params)
          └── RateOverride (per-project rate edits)
```

SQLite with WAL journalling, `passive_deletes=True` so the database owns
cascade deletion rather than the ORM racing it.

---

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | status + which providers are live |
| GET | `/api/catalog` | materials, reference objects, categories, limits |
| POST | `/api/projects` | upload → normalise → gate → detect → seed scale |
| GET/PATCH/DELETE | `/api/projects/{id}` | open, rename, cost settings, delete |
| POST | `/api/projects/{id}/detect` | re-run detection |
| PUT | `/api/projects/{id}/regions` | replace the region set |
| PUT | `/api/projects/{id}/calibration` | set / confirm the scale |
| PUT | `/api/projects/{id}/assignments` | materials for the active variant |
| PUT | `/api/projects/{id}/rates` | per-project rate overrides |
| GET | `/api/projects/{id}/estimate` | quantities + costs |
| POST | `/api/projects/{id}/render` | composite the design |
| GET | `/api/projects/{id}/report.pdf` | the report |
| GET | `/api/projects/{id}/files/{name}` | owner-scoped image serving |

Interactive docs at `http://127.0.0.1:8000/docs`.

---

## Performance

Measured on the bundled 1636×2048 sample, Python 3.10 / OpenCV 4.14:

| Stage | Time |
|---|---|
| Normalise + quality gate | ~0.6 s |
| Scene masks | ~0.4 s |
| Region detection | ~3.5 s |
| Render (3 surfaces) | ~0.7 s |
| Estimate | < 50 ms |
| PDF | ~1 s |

Detection dominates, and is the obvious target if it needs to be faster — it
runs at a fixed 760 px and most of the cost is SLIC.
