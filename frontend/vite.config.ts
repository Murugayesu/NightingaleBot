import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Local development: the FastAPI app runs on :8000 and Vite proxies /api to it,
// so the browser sees one origin, exactly as behind Caddy in production.
// The Host header is kept (no changeOrigin): the API's CSRF check compares the
// request's Origin with its Host.
const api = process.env.NURSINGBOT_API_URL ?? "http://127.0.0.1:8000";
const proxy = {
  "/api": { target: api },
  "/health": { target: api },
};

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true, proxy },
  preview: { port: 4173, strictPort: true, proxy },
  build: {
    outDir: "dist",
    sourcemap: false,
    // Every asset is a separate file, so the strict CSP needs no data: URLs
    // for scripts or styles.
    assetsInlineLimit: 0,
  },
});
