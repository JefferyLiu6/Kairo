import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/** Python FastAPI default (AGENT_PORT); override: VITE_API_ORIGIN=http://127.0.0.1:8766 */
const apiOrigin = process.env.VITE_API_ORIGIN ?? "http://localhost:8766";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/ws/terminal": { target: apiOrigin.replace(/^http/, "ws"), ws: true },
      "/chat": apiOrigin,
      "/sessions": apiOrigin,
      "/master": apiOrigin,
      "/workspace": apiOrigin,
      "/health": apiOrigin,
      "/personal-manager": apiOrigin,
      "/orchestrator": apiOrigin,
      "/demo": apiOrigin,
    },
  },
});
