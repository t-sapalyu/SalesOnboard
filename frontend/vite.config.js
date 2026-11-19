// Vite runs the React dev server on http://localhost:5173.
// Every request to /api is forwarded to the FastAPI backend on port 8000,
// so the browser sees one site and the login cookie works without CORS setup.
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
