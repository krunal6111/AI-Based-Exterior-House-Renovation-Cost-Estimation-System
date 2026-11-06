import type {
  Calibration,
  Catalog,
  Estimate,
  Health,
  Project,
  ProjectSummary,
  RateOverride,
  RenderResult,
  Region,
} from "./types";

// Empty for local dev: Vite's proxy forwards same-origin "/api" to the
// backend (see vite.config.ts). In production (frontend and backend on
// different origins, e.g. Vercel + a standalone API host) this is set at
// build time to the backend's own origin, e.g. "https://api.example.com".
const API_ORIGIN = import.meta.env.VITE_API_BASE ?? "";
const BASE = `${API_ORIGIN}/api`;
const TOKEN_KEY = "reno.owner-token";

/** Stable per-browser identity. This is what keeps concurrent users apart. */
export function ownerToken(): string {
  let token = localStorage.getItem(TOKEN_KEY);
  if (!token) {
    token =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `u${Date.now()}${Math.random().toString(36).slice(2)}`;
    localStorage.setItem(TOKEN_KEY, token);
  }
  return token;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("X-Owner-Token", ownerToken());
  if (init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(
      "Cannot reach the server. Is the backend running on port 8000?",
      0,
    );
  }

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = body.detail;
      else if (Array.isArray(body?.detail))
        detail = body.detail.map((d: { msg?: string }) => d.msg).join("; ");
    } catch {
      /* keep the status line */
    }
    throw new ApiError(detail, response.status);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  health: () => request<Health>("/health"),
  catalog: () => request<Catalog>("/catalog"),

  listProjects: () => request<ProjectSummary[]>("/projects"),
  getProject: (id: string) => request<Project>(`/projects/${id}`),
  deleteProject: (id: string) =>
    request<void>(`/projects/${id}`, { method: "DELETE" }),

  createProject: (file: File, name: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("name", name);
    return request<Project>("/projects", { method: "POST", body: form });
  },

  patchProject: (
    id: string,
    payload: {
      name?: string;
      notes?: string;
      cost_settings?: Record<string, number | boolean | string>;
    },
  ) =>
    request<Project>(`/projects/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  redetect: (id: string) =>
    request<Project>(`/projects/${id}/detect`, { method: "POST" }),

  putRegions: (id: string, regions: Region[]) =>
    request<Project>(`/projects/${id}/regions`, {
      method: "PUT",
      body: JSON.stringify({
        regions: regions.map((r) => ({
          id: r.id,
          name: r.name,
          category: r.category,
          polygon: r.polygon,
          parent_id: r.parent_id,
          source: r.source,
          confidence: r.confidence,
          user_edited: r.user_edited,
          override_width_ft: r.override_width_ft,
          override_height_ft: r.override_height_ft,
          override_area_sqft: r.override_area_sqft,
        })),
      }),
    }),

  putCalibration: (id: string, calibration: Omit<Calibration, "ft_per_px" | "note">) =>
    request<Project>(`/projects/${id}/calibration`, {
      method: "PUT",
      body: JSON.stringify(calibration),
    }),

  putAssignments: (
    id: string,
    name: string,
    assignments: {
      region_id: string;
      material_id: string;
      params: Record<string, unknown>;
    }[],
  ) =>
    request<Project>(`/projects/${id}/assignments`, {
      method: "PUT",
      body: JSON.stringify({ name, assignments }),
    }),

  putRates: (id: string, overrides: RateOverride[]) =>
    request<Project>(`/projects/${id}/rates`, {
      method: "PUT",
      body: JSON.stringify({ overrides }),
    }),

  estimate: (id: string) => request<Estimate>(`/projects/${id}/estimate`),

  render: (id: string) =>
    request<RenderResult>(`/projects/${id}/render`, { method: "POST" }),

  reportUrl: (id: string) => `${BASE}/projects/${id}/report.pdf`,

  /** The PDF route needs the owner header, so fetch it rather than linking. */
  downloadReport: async (id: string, filename: string) => {
    const response = await fetch(api.reportUrl(id), {
      headers: { "X-Owner-Token": ownerToken() },
    });
    if (!response.ok) throw new ApiError("Could not build the report", response.status);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },

  /**
   * Images are behind the owner-token check too. `url` is whatever the API
   * put in image_url/render_url/thumbnail_url -- already rooted at
   * settings.api_prefix (e.g. "/api/projects/.../files/display.jpg"), so it
   * only needs the backend's origin prepended, not the full BASE.
   */
  authedImage: async (url: string) => {
    const response = await fetch(`${API_ORIGIN}${url}`, {
      headers: { "X-Owner-Token": ownerToken() },
    });
    if (!response.ok) throw new ApiError("Image not available", response.status);
    return URL.createObjectURL(await response.blob());
  },
};
