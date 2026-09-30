import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Same-origin API calls: the browser only talks to the dev server, which
    // forwards /api to the backend. Avoids CORS and host-level web filters.
    proxy: {
      "/api": { target: "http://127.0.0.1:8001", changeOrigin: true },
    },
  },
});
