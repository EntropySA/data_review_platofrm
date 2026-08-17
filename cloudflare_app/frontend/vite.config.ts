import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In production one Worker serves this app and /api/*. Proxying /api to the
// local Worker keeps development same-origin too, so neither environment needs
// CORS handling and the two behave identically.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8787" } },
});
