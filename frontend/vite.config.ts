import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies /api and /ws to the Django backend so the same
// same-origin session-cookie + CSRF flow used in production works locally
// once the real backend is running on :8000.
export default defineConfig(({ command }) => ({
  plugins: [react()],
  // Production build is served by Django from STATICFILES_DIRS (see
  // fishofisho/settings.py) under /static/, so built asset URLs must be
  // rooted there. The dev server (vite dev) keeps the default root base.
  base: command === "build" ? "/static/" : "/",
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/ws": {
        target: "ws://127.0.0.1:8000",
        ws: true,
      },
    },
  },
}));
