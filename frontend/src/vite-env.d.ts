/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Backend origin for a split deployment, e.g. "https://api.example.com". Empty for local dev (same-origin via the Vite proxy). */
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
