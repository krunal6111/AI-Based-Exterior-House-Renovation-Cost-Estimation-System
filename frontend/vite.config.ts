import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    // 5173 is Vite's default and is often already taken by another project.
    // strictPort makes a clash fail loudly instead of silently binding the
    // same port on a different interface, which is very confusing to debug.
    host: "127.0.0.1",
    port: 5175,
    strictPort: true,
    proxy: {
      // Everything under /api goes to the FastAPI backend, so the app is
      // same-origin in development and needs no CORS juggling.
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
