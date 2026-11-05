# How the estimation works

Written for a reviewer who needs to check the arithmetic, and for a developer
who has to change it.

Every formula below is implemented as a pure function with no I/O, and every
one is covered by `backend/tests/`. The UI and the PDF both print the formula
that produced each number, so nothing here is hidden from the user.

---

## Stage 1 — From pixels to feet

### 1.1 The scale factor

`backend/app/services/scale.py`

A photograph carries no absolute size. One known dimension fixes everything
else:

```
ft_per_px = reference_real_ft / reference_length_px
```

The user drags a two-point handle onto something of known size. Defaults come
from `app/data/assumptions.json`:

| Reference | Assumed size | Why |
|---|---|---|
| Main door height | 6.90 ft | Indian standard leaf is 2100 mm. The most dependable object in a facade photo. |
| Floor-to-floor | 10.0 ft | Typical low-rise residential is 3.0–3.2 m. |
| Window height | 4.0 ft | Common 1200 mm window, but sizes vary widely — weakest of the three. |
| Something you measured | user-entered | Best accuracy. |

If no reference is set at all, Tier C assumes the detected building is 20 ft
tall and says so loudly.

### 1.2 Removing perspective

A wall photographed at an angle is foreshortened; measuring its on-screen
width directly under-reads it. So each surface is rectified using its own
geometry.

**Step 1 — fit a quadrilateral.** `min_area_quad()` takes the convex hull of
the polygon and simplifies it (Douglas-Peucker, increasing tolerance) until
four vertices remain, falling back to the minimum-area rotated rectangle.

**Step 2 — recover the focal length.** Closed-form solution of Zhang & He
(2006), assuming square pixels and a principal point at the image centre. With
corners `m1..m4` (top-left, top-right, bottom-left, bottom-right) expressed
relative to the image centre:

```
k2 = (m1 × m4 · m3) / (m2 × m4 · m3)
k3 = (m1 × m4 · m2) / (m3 × m4 · m2)
n2 = k2·m2 − m1
n3 = k3·m3 − m1

f² = −(n2ₓ·n3ₓ + n2ᵧ·n3ᵧ) / (n2_z · n3_z)
```

If the quad is a parallelogram — the photo was taken square-on — then
`n2_z ≈ 0` and there is no perspective to invert. A typical lens is assumed
instead: `f = 0.85 × long_edge` (≈61° horizontal field of view), and the
result is marked lower-confidence.

**Step 3 — recover the true proportions.** Build the homography `H` mapping a
unit square onto the quad. Writing `H = K[r1 r2 t]` and imposing
`|r1| = |r2| = 1` on the real rectangle gives

```
width / height = |K⁻¹·h1| / |K⁻¹·h2|
```

Against synthetically projected rectangles this returns the true aspect ratio
to within rounding whenever the focal length is recoverable
(`test_perspective_aspect_recovered`).

### 1.3 Area

Vertical edges stay vertical in a level photograph, so height is the dimension
a single reference scales most reliably. Width is then derived from the
rectified proportions:

```
height_ft = quad_vertical_extent_px × ft_per_px
width_ft  = height_ft × aspect_ratio
area_sqft = width_ft × height_ft × fill_fraction × retained_fraction
```

- `fill_fraction` = polygon area ÷ bounding-quad area. An L-shaped or clipped
  wall is scaled down rather than treated as a full rectangle.
- `retained_fraction` = the share of the wall surviving after its openings are
  subtracted (Shapely boolean difference). This is what stops a window being
  painted and billed. Which regions count as "its openings" is found by
  `estimator.overlapping_openings()` -- geometric overlap first, an explicit
  link as a fallback for a sliver too small to clear the overlap threshold on
  its own -- so a window is excluded whether or not anyone linked it by hand.

Railings and roof edges are **linear**. Their region is rectified the same way
and the run length is taken as the rectified width, which beats measuring the
drawn polyline (that would be foreshortened).

### 1.4 Honest error bands

| Method | Trigger | Band |
|---|---|---|
| `user_dimension` | user typed a width, height or area | −5% / +8% |
| `reference_object` | confirmed reference | −12% / +20% |
| `pixel_ratio` | no reference, or an unconfirmed guess | −25% / +40% |

Project-level uncertainty is the **area-weighted blend** of each region's
band, so one small guessed surface does not drag down a project whose big
walls were measured properly.

---

## Stage 2 — From area to quantities

`backend/app/services/quantities.py`

### Paint and primer (priced per litre)

```
litres = area_sqft × coats / coverage_sqft_per_litre_per_coat × (1 + wastage)
```

Then solved into real tin sizes (20 / 10 / 4 / 1 L), greedy from the largest
with a single tight top-up. The solver **minimises litres purchased**, so
17.5 L becomes 10 + 4 + 4 = 18 L rather than a single 20 L tin — less paint
left on the shelf. 21 L becomes 20 + 1, never 2 × 20.

### Putty (per kg)

```
kg = area_sqft / coverage_sqft_per_kg × (1 + wastage)
```

### Adhesive (per bag)

```
bags = ceil(area_sqft / coverage_sqft_per_bag × (1 + wastage))
```

### Cladding, texture, panels (per sq ft)

```
sqft = area_sqft × (1 + wastage)
```

### Tiles

Billed by area, but also reported as a piece and box count so you can order:

```
tile_sqft = (w_mm/1000) × (h_mm/1000) × 10.7639
pieces    = ceil(sqft / tile_sqft)
boxes     = ceil(pieces / pieces_per_box)
```

### Railings (per running foot)

```
rft = length_ft × (1 + wastage)
```

### Derived consumables

Each finish declares what it needs. Premium paint pulls in primer **and**
putty; tile pulls in adhesive. These appear as separate "included" lines, each
carrying the same area as its parent finish, so they are visible rather than
buried in a rate.

Wastage defaults: paint 5%, texture 8%, stone 10–12%, tile 10%, railing 5%.

---

## Stage 3 — From quantities to cost

`backend/app/services/costing.py`

```
material_cost = purchased_quantity × material_rate
labour_cost   = measured_basis    × labour_rate
line_total    = material_cost + labour_cost
```

Two deliberate asymmetries:

- **Material is priced on what you buy**, including wastage and rounded up to
  whole tins, bags and boxes — you pay for the whole tin.
- **Labour is priced on the measured surface, before wastage** — a painter is
  paid for the wall, not for the paint on the drop sheet.

### Add-ons, in this order

```
running  = materials + labour
running += scaffolding_rate × total_measured_area
running += transport_lumpsum
running *= (1 + contractor_overhead_pct/100)
running *= (1 + contingency_pct/100)
running *= (1 + gst_pct/100)
```

Scaffolding counts each surface **once** — derived consumables ride on their
parent's area and must not inflate it (`test_scaffolding_counts_each_surface_once`).

Defaults: scaffolding ₹8/sq ft, transport ₹3,500, overhead 10%, contingency
5%, GST 18%. All editable per project.

### The quoted range

```
range_low  = grand_total × (1 − blended_error_low)
range_high = grand_total × (1 + blended_error_high)
```

This covers **measurement uncertainty only**. It does not cover rate variation
between contractors, which is larger — see `LIMITATIONS.md` §6.

---

## Worked example

1000 sq ft of wall, premium exterior emulsion, catalog defaults:

| Step | Calculation | Result |
|---|---|---|
| Paint | 1000 × 2 coats ÷ 120 sq ft/L/coat × 1.05 | 17.50 L |
| Purchased | 10 + 4 + 4 | 18 L |
| Paint material | 18 × ₹620 | ₹11,160 |
| Paint labour | 1000 × ₹12 | ₹12,000 |
| Primer | 1000 × 1 ÷ 140 × 1.05 → 8 L purchased | ₹2,560 + ₹4,000 |
| Putty | 1000 ÷ 15 × 1.08 = 72 kg | ₹2,736 + ₹8,000 |
| **Works subtotal** | | **₹40,456** |
| Scaffolding | 1000 × ₹8 | ₹8,000 |
| Transport | lump sum | ₹3,500 |
| Overhead | 10% | ₹5,195.60 |
| Contingency | 5% | ₹2,857.58 |
| GST | 18% | ₹10,801.65 |
| **Total** | | **₹70,810.83** |

That is ₹70.81/sq ft all-in for a premium repaint with full preparation —
consistent with the ₹25–70/sq ft range public 2026 sources quote for
premium exterior painting, which typically excludes scaffolding, overhead and
GST.

---

## Where to change things

| Want to change | File |
|---|---|
| Rates, coverage, wastage, palettes | `backend/app/data/materials.json` |
| Reference sizes, quality thresholds, cost defaults | `backend/app/data/assumptions.json` |
| Area maths | `backend/app/services/scale.py`, `geometry.py` |
| Quantity formulas | `backend/app/services/quantities.py` |
| Cost build-up | `backend/app/services/costing.py` |
