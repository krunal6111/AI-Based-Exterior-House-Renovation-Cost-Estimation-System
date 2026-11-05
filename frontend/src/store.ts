import { create } from "zustand";
import { api, ApiError } from "./api";
import type {
  Catalog,
  Estimate,
  Health,
  Point,
  Project,
  ProjectSummary,
  RateOverride,
  Region,
} from "./types";

export type StepId = "upload" | "surfaces" | "measure" | "design" | "estimate" | "report";

export const STEPS: { id: StepId; label: string; blurb: string }[] = [
  { id: "upload", label: "Photo", blurb: "Upload a picture of the exterior" },
  { id: "surfaces", label: "Surfaces", blurb: "Check and correct the detected surfaces" },
  { id: "measure", label: "Measure", blurb: "Set the scale so areas are real" },
  { id: "design", label: "Design", blurb: "Try materials on the house" },
  { id: "estimate", label: "Estimate", blurb: "Quantities, rates and total cost" },
  { id: "report", label: "Report", blurb: "Download a report for your contractor" },
];

/** Stable colour per surface type, used on the canvas and in the lists. */
export const CATEGORY_COLORS: Record<string, string> = {
  wall: "#2f7d4f",
  window: "#1d5c96",
  door: "#b4572c",
  balcony: "#7b4fa8",
  pillar: "#c08a1e",
  parapet: "#2a8f8f",
  railing: "#c2407f",
  roof_edge: "#5a6b7d",
  gate: "#a93226",
  plinth: "#6d7f2f",
  sunshade: "#3f7fa8",
  other: "#76848f",
};

export function categoryColor(category: string): string {
  return CATEGORY_COLORS[category] ?? CATEGORY_COLORS.other;
}

let localIdCounter = 0;
const newLocalId = () => `new-${Date.now()}-${localIdCounter++}`;

interface State {
  health: Health | null;
  catalog: Catalog | null;
  projects: ProjectSummary[];
  project: Project | null;
  estimate: Estimate | null;

  step: StepId;
  selectedRegionId: string | null;
  regions: Region[];
  regionsDirty: boolean;
  assignmentsDirty: boolean;

  busy: string | null;
  error: string | null;
  toast: string | null;

  init: () => Promise<void>;
  setStep: (step: StepId) => void;
  setError: (message: string | null) => void;
  setToast: (message: string | null) => void;

  createProject: (file: File, name: string) => Promise<void>;
  openProject: (id: string) => Promise<void>;
  closeProject: () => void;
  deleteProject: (id: string) => Promise<void>;
  renameProject: (name: string) => Promise<void>;

  selectRegion: (id: string | null) => void;
  patchRegion: (id: string, patch: Partial<Region>) => void;
  addRegion: (polygon: Point[], category: string) => void;
  deleteRegion: (id: string) => void;
  saveRegions: () => Promise<void>;
  redetect: () => Promise<void>;

  setCalibration: (
    refKind: string,
    refLine: Point[],
    refRealFt: number,
    confirmed: boolean,
  ) => Promise<void>;

  assignMaterial: (
    regionId: string,
    materialId: string | null,
    params?: Record<string, unknown>,
  ) => Promise<void>;

  renderDesign: () => Promise<void>;
  refreshEstimate: () => Promise<void>;
  saveRates: (overrides: RateOverride[]) => Promise<void>;
  saveCostSettings: (settings: Record<string, number | boolean>) => Promise<void>;
}

function describe(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong";
}

export const useStore = create<State>((set, get) => {
  /** Run an async action with a busy label and uniform error handling. */
  async function withBusy<T>(label: string, fn: () => Promise<T>): Promise<T | undefined> {
    set({ busy: label, error: null });
    try {
      return await fn();
    } catch (error) {
      set({ error: describe(error) });
      return undefined;
    } finally {
      set({ busy: null });
    }
  }

  const adopt = (project: Project) =>
    set({
      project,
      regions: project.regions.map((r) => ({ ...r })),
      regionsDirty: false,
      assignmentsDirty: false,
    });

  return {
    health: null,
    catalog: null,
    projects: [],
    project: null,
    estimate: null,
    step: "upload",
    selectedRegionId: null,
    regions: [],
    regionsDirty: false,
    assignmentsDirty: false,
    busy: null,
    error: null,
    toast: null,

    async init() {
      await withBusy("Starting up", async () => {
        const [health, catalog, projects] = await Promise.all([
          api.health(),
          api.catalog(),
          api.listProjects(),
        ]);
        set({ health, catalog, projects });
      });
    },

    setStep(step) {
      set({ step });
      if (step === "estimate" || step === "report") void get().refreshEstimate();
    },
    setError(error) {
      set({ error });
    },
    setToast(toast) {
      set({ toast });
      if (toast) setTimeout(() => set((s) => (s.toast === toast ? { toast: null } : s)), 3200);
    },

    async createProject(file, name) {
      const project = await withBusy("Reading the photo and finding surfaces", () =>
        api.createProject(file, name),
      );
      if (!project) return;
      adopt(project);
      set({ step: project.quality?.usable ? "surfaces" : "upload", estimate: null });
      const projects = await api.listProjects().catch(() => get().projects);
      set({ projects });
    },

    async openProject(id) {
      const project = await withBusy("Opening project", () => api.getProject(id));
      if (!project) return;
      adopt(project);
      set({ step: "surfaces", estimate: null, selectedRegionId: null });
    },

    closeProject() {
      set({
        project: null,
        regions: [],
        estimate: null,
        step: "upload",
        selectedRegionId: null,
        regionsDirty: false,
      });
      void api.listProjects().then((projects) => set({ projects }));
    },

    async deleteProject(id) {
      await withBusy("Deleting", async () => {
        await api.deleteProject(id);
        const projects = await api.listProjects();
        set({ projects });
        if (get().project?.id === id) get().closeProject();
      });
    },

    async renameProject(name) {
      const project = get().project;
      if (!project || !name.trim() || name === project.name) return;
      const updated = await withBusy("Saving", () =>
        api.patchProject(project.id, { name: name.trim() }),
      );
      if (updated) set({ project: { ...updated, regions: get().project?.regions ?? [] } });
    },

    selectRegion(id) {
      set({ selectedRegionId: id });
    },

    patchRegion(id, patch) {
      set((state) => ({
        regions: state.regions.map((r) => (r.id === id ? { ...r, ...patch, user_edited: true } : r)),
        regionsDirty: true,
      }));
    },

    addRegion(polygon, category) {
      const catalog = get().catalog;
      const info = catalog?.categories.find((c) => c.value === category);
      const existing = get().regions.filter((r) => r.category === category).length;
      const region: Region = {
        id: newLocalId(),
        name: `${info?.label ?? "Surface"}${existing ? ` ${existing + 1}` : ""}`,
        category,
        category_label: info?.label ?? "Surface",
        measure: info?.measure ?? "area",
        polygon,
        parent_id: null,
        source: "manual",
        confidence: 1,
        user_edited: true,
        override_width_ft: null,
        override_height_ft: null,
        override_area_sqft: null,
        measurement: null,
      };
      set((state) => ({
        regions: [...state.regions, region],
        selectedRegionId: region.id,
        regionsDirty: true,
      }));
    },

    deleteRegion(id) {
      set((state) => ({
        // Anything parented to the deleted surface is orphaned, not deleted.
        regions: state.regions
          .filter((r) => r.id !== id)
          .map((r) => (r.parent_id === id ? { ...r, parent_id: null } : r)),
        selectedRegionId: state.selectedRegionId === id ? null : state.selectedRegionId,
        regionsDirty: true,
      }));
    },

    async saveRegions() {
      const { project, regions, regionsDirty } = get();
      if (!project || !regionsDirty) return;
      const updated = await withBusy("Saving surfaces", () =>
        api.putRegions(project.id, regions),
      );
      if (updated) {
        adopt(updated);
        set({ estimate: null });
      }
    },

    async redetect() {
      const project = get().project;
      if (!project) return;
      const updated = await withBusy("Detecting surfaces again", () =>
        api.redetect(project.id),
      );
      if (updated) {
        adopt(updated);
        set({ estimate: null, selectedRegionId: null });
      }
    },

    async setCalibration(refKind, refLine, refRealFt, confirmed) {
      const project = get().project;
      if (!project) return;
      const updated = await withBusy("Updating scale", () =>
        api.putCalibration(project.id, {
          ref_kind: refKind,
          ref_line: refLine,
          ref_real_ft: refRealFt,
          confirmed,
        }),
      );
      if (updated) {
        adopt(updated);
        set({ estimate: null });
      }
    },

    async assignMaterial(regionId, materialId, params = {}) {
      const { project } = get();
      if (!project) return;
      const variant = project.variants.find((v) => v.id === project.active_variant_id);
      const current = variant?.assignments ?? [];

      const next = current
        .filter((a) => a.region_id !== regionId)
        .map((a) => ({
          region_id: a.region_id,
          material_id: a.material_id,
          params: a.params ?? {},
        }));
      if (materialId) next.push({ region_id: regionId, material_id: materialId, params });

      const updated = await withBusy("Applying material", () =>
        api.putAssignments(project.id, variant?.name ?? "Option A", next),
      );
      if (updated) {
        adopt(updated);
        set({ estimate: null });
      }
    },

    async renderDesign() {
      const project = get().project;
      if (!project) return;
      const result = await withBusy("Rendering your house", () => api.render(project.id));
      if (!result) return;
      const refreshed = await api.getProject(project.id).catch(() => null);
      if (refreshed) adopt(refreshed);
      if (result.warnings.length) set({ toast: result.warnings[0] });
    },

    async refreshEstimate() {
      const project = get().project;
      if (!project) return;
      const estimate = await withBusy("Calculating", () => api.estimate(project.id));
      if (estimate) set({ estimate });
    },

    async saveRates(overrides) {
      const project = get().project;
      if (!project) return;
      const updated = await withBusy("Saving rates", () =>
        api.putRates(project.id, overrides),
      );
      if (updated) {
        adopt(updated);
        await get().refreshEstimate();
      }
    },

    async saveCostSettings(settings) {
      const project = get().project;
      if (!project) return;
      const updated = await withBusy("Updating costs", () =>
        api.patchProject(project.id, { cost_settings: settings }),
      );
      if (updated) {
        adopt(updated);
        await get().refreshEstimate();
      }
    },
  };
});

/** Formats money using the project's currency symbol. */
export function useMoney() {
  const symbol = useStore((s) => s.project?.currency_symbol ?? s.catalog?.currency_symbol ?? "₹");
  return (value: number, decimals = 0) =>
    `${symbol}${value.toLocaleString("en-IN", {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    })}`;
}
