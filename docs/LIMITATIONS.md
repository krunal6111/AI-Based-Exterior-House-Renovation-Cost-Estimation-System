# Limitations

Written for whoever has to decide how far to trust this system's output.

The short version: **this is a planning aid, not a survey and not a
quotation.** Everything below is a real constraint, not a disclaimer.

---

## 1. One photograph cannot see the whole building

A single image shows one or two faces of a house. Anything not in frame — the
rear wall, the side elevation, a courtyard — is **not measured and not
costed**. A homeowner who budgets the whole house from a front-elevation photo
will be short.

*What to do:* run a separate project per elevation and add the totals, or
treat the number as "front elevation only" and say so.

## 2. Scale is an assumption until you confirm it

A photograph has no inherent sense of size. The system fixes this by scaling
from an object of known real size, and it reports which tier each measurement
used:

| Tier | How the size was found | Declared band |
|---|---|---|
| A | You typed a real dimension | −5% / +8% |
| B | Scaled from a confirmed reference object | −12% / +20% |
| C | No reference; building height assumed at 20 ft | −25% / +40% |

The automatic reference placement is **a guess** and is deliberately saved as
unconfirmed. In testing on the sample cottage, the auto-placed guess
under-estimated areas by roughly 2.5× compared with a correctly placed door
reference. Confirming the reference is the single highest-leverage action a
user takes in the whole workflow.

## 3. Perspective recovery assumes flat, rectangular surfaces

Areas are computed by rectifying each surface's bounding quadrilateral, which
recovers the camera's focal length from the perspective in that quad
(Zhang & He, 2006). Against synthetically projected rectangles this is exact.

It degrades when:

- **The photo is square-on.** With no perspective cue the focal length cannot
  be recovered, so a typical lens is assumed (0.85 × the image's long edge).
  A 20% error in that assumption moves the area by roughly 5%.
- **The surface is not planar or not rectangular** — curved bays, heavily
  ornamented facades, deep mouldings. The fitted quad will not describe them.
- **The camera was tilted hard** or the lens is strongly distorted. No lens
  distortion correction is applied.

## 4. Automatic surface detection without an AI key is unreliable, not just imperfect

The offline detector uses classical computer vision: sky/vegetation
separation, straight-line structure density to find the building, then
superpixel colour clustering. On a clean, well-lit facade it can isolate the
building envelope and find walls, a door and openings. On a messier photo it
can fail outright: tested against a real renovation-in-progress facade
(peeling paint, bare wood trim, a cracked driveway in the foreground), its
**highest-confidence "wall" was the driveway** — material got rendered onto
the pavement, not the house. See `docs/offline-vs-gemini-detection.png` for
the actual result next to the same photo through Gemini segmentation.

This is a ceiling, not a tuning problem. Colour-clustering has no concept of
"building" beyond contrast and line density; it cannot be taught to recognise
a facade the way a vision-language model already can. Single-image facade
parsing is an open research problem for classical CV specifically.

If any Gemini credential is configured (`ENABLE_AI_SEGMENTATION=true`, see
the README), detection runs through Gemini instead and falls back to the
offline detector only if that call fails or returns nothing — this is a large
and consistent quality improvement in every photo tested so far, not a
marginal one. The offline detector remains the default and the only option
with no network access at all, because the project is required to work fully
offline, not because it is adequate on its own.

Either way, the workflow routes every user through a non-skippable review
step with a full polygon editor, including edge-midpoint `+` handles so a
detected region's corner count is never a ceiling on correcting it. Treat
even a good detection as a time-saver over an empty canvas, not as a result
to accept unread.

Detection is also sensitive to upload resolution. Scene masks were made
resolution-invariant (computed at a fixed 760 px working size), and k-means is
seeded for reproducibility, but the superpixel clustering still shifts
somewhat with input size. This applies only to the offline path; Gemini
segmentation does not have this sensitivity.

## 5. The visualisation re-surfaces; it does not redesign

The renderer replaces the *appearance* of regions you marked, inside the
outlines you drew, using lighting extracted from your own photograph. That is
a deliberate choice: it guarantees the building's geometry, windows and
roofline are untouched, which is what requirement 5.4 asks for.

Consequences:

- It cannot add a feature that is not already there (a new balcony, a changed
  window shape, a different roof).
- Render quality depends on the quality of your outlines. A sloppy polygon
  will visibly bleed onto trim.
- Materials are **procedurally generated**, not photographs of real products.
  They are representative of a material family, not of a specific SKU.
- Reflections, inter-reflection between new surfaces, and cast shadows from
  newly added relief are not simulated.

## 6. Rates are indicative, regional and volatile

The seeded catalog is a midpoint of public Indian supplier and contractor
listings from 2026. Research for this project found the *same item* quoted
across a 3–5× range — stone cladding alone spans ₹60–₹1,500/sq ft depending on
stone, thickness, finish and city.

Two entries (ACP panel, wood-look WPC/HPL) are marked **"INDICATIVE ONLY — not
verified"** in their source notes because no dated 2026 source was found.

Every rate is editable per project, and the report prints which rates were
used. Replace them with your own quotations before the number means anything.

## 7. What is deliberately out of scope

- Interior work of any kind
- Structural engineering, load paths, or whether a change is buildable
- Waterproofing diagnosis, crack repair, or condition assessment
- Statutory approvals, setbacks, or bye-law compliance
- Scheduling, phasing, or contractor selection
- Legally binding quotations

## 8. Privacy and data handling

With no API key configured the system makes **no network calls at all** —
images never leave the machine. Uploads, renders and reports are written to
`storage/<project-id>/` on local disk.

Enabling the optional Gemini provider sends your photograph to Google. That is
off by default and must be switched on explicitly.

Projects are separated by a browser-local `owner_token`, which keeps
concurrent users from seeing each other's work. **This is not authentication.**
Anyone who learns a token can read that project. Do not deploy this to a
public network as-is.
