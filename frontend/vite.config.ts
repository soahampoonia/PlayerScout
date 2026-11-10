import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In Docker the API is another container (http://api:8000); outside it, localhost.
const api = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    watch: { usePolling: process.env.VITE_USE_POLLING === "true" },
    proxy: {
      "/api": api,
      "/ws": { target: api, ws: true }, // live updates, from build step 4
    },
  },
});
