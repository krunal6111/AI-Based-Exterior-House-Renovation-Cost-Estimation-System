/** Mirrors the backend Pydantic schemas. */

export type Point = [number, number];

export interface RateBand {
  unit: string;
  min: number;
  default: number;
  max: number;
}

export interface Material {
  id: string;
  name: string;
  short: string;
  kind: string;
  group: string;
  applies_to: string[];
  render: Record<string, unknown> | null;
  palette: string[];
  coverage: Record<string, unknown>;
  wastage_pct: number;
  material_rate: RateBand;
  labour_rate: RateBand;
  durability_years: number[];
  maintenance: string;
  source_note: string;
  assignable: boolean;
}

export interface CategoryInfo {
  value: string;
  label: string;
  measure: "area" | "length";
  is_opening: boolean;
}

export interface ReferenceObject {
  id: string;
  label: string;
  real_ft: number;
  tolerance_ft: number;
  note: string;
}

export interface Catalog {
  currency: string;
  currency_symbol: string;
  disclaimer: string;
  updated: string;
  materials: Material[];
  reference_objects: ReferenceObject[];
  categories: CategoryInfo[];
  default_cost_settings: Record<string, number | boolean | string>;
  limitations: string[];
}

export interface Measurement {
  area_sqft: number;
  length_ft: number;
  width_ft: number;
  height_ft: number;
  aspect_ratio: number;
  method: string | null;
  method_label: string;
  confidence: number;
  error_low: number;
  error_high: number;
  notes: string[];
}

export interface Region {
  id: string;
  name: string;
  category: string;
  category_label: string;
  measure: "area" | "length";
  polygon: Point[];
  parent_id: string | null;
  source: string;
  confidence: number;
  user_edited: boolean;
  override_width_ft: number | null;
  override_height_ft: number | null;
  override_area_sqft: number | null;
  measurement: Measurement | null;
}

export interface Calibration {
  ref_kind: string;
  ref_line: Point[];
  ref_real_ft: number;
  confirmed: boolean;
  ft_per_px: number | null;
  note: string;
}

export interface Assignment {
  id: string;
  region_id: string;
  material_id: string;
  params: Record<string, unknown>;
}

export interface Variant {
  id: string;
  name: string;
  is_active: boolean;
  render_url: string | null;
  assignments: Assignment[];
}

export interface QualityIssue {
  code: string;
  severity: "error" | "warning" | "info";
  message: string;
  guidance: string;
}

export interface Quality {
  usable: boolean;
  score: number;
  width: number;
  height: number;
  sharpness: number;
  mean_luma: number;
  clipped_fraction: number;
  building_fraction: number;
  issues: QualityIssue[];
}

export interface RateOverride {
  material_id: string;
  material_rate: number | null;
  labour_rate: number | null;
  wastage_pct: number | null;
}

export interface Project {
  id: string;
  name: string;
  notes: string;
  currency: string;
  currency_symbol: string;
  created_at: string;
  updated_at: string;
  image_url: string | null;
  image_width: number;
  image_height: number;
  quality: Quality | null;
  regions: Region[];
  calibration: Calibration | null;
  variants: Variant[];
  active_variant_id: string | null;
  cost_settings: Record<string, number | boolean | string>;
  rate_overrides: RateOverride[];
}

export interface ProjectSummary {
  id: string;
  name: string;
  currency: string;
  created_at: string;
  updated_at: string;
  thumbnail_url: string | null;
}

export interface Pack {
  size: number;
  count: number;
}

export interface QuantityLine {
  material_id: string;
  material_name: string;
  group: string;
  unit: string;
  quantity: number;
  purchase_quantity: number;
  basis_area_sqft: number;
  basis_length_ft: number;
  wastage_pct: number;
  formula: string;
  derived: boolean;
  region_names: string[];
  packs: Pack[];
  pieces: number | null;
  boxes: number | null;
  notes: string;
}

export interface CostLine {
  material_id: string;
  material_name: string;
  group: string;
  quantity: number;
  unit: string;
  material_rate: number;
  material_cost: number;
  labour_basis: number;
  labour_unit: string;
  labour_rate: number;
  labour_cost: number;
  total: number;
  derived: boolean;
  overridden: boolean;
  formula: string;
  region_names: string[];
}

export interface AddOn {
  key: string;
  label: string;
  amount: number;
  basis: string;
}

export interface Estimate {
  currency: string;
  currency_symbol: string;
  quantities: QuantityLine[];
  lines: CostLine[];
  group_subtotals: Record<string, number>;
  material_subtotal: number;
  labour_subtotal: number;
  subtotal: number;
  addons: AddOn[];
  grand_total: number;
  range_low: number;
  range_high: number;
  total_area_sqft: number;
  total_length_ft: number;
  notes: string[];
  warnings: string[];
}

export interface RenderResult {
  render_url: string;
  engine: string;
  layers_applied: number;
  warnings: string[];
}

export interface Health {
  status: string;
  version: string;
  offline_mode: boolean;
  ai_segmentation_available: boolean;
  ai_render_available: boolean;
}
