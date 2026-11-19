/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5180, strictPort: true, host: "127.0.0.1", proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } } },
  // Optimised build served for a public tunnel link (npm run serve:public): same /api proxy to the local backend.
  preview: { port: 4180, strictPort: true, host: "127.0.0.1", proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false, timeout: 600_000, proxyTimeout: 600_000 } } },
  test: { environment: "jsdom", globals: true, setupFiles: ["./src/test/setup.ts"], css: false },
});
