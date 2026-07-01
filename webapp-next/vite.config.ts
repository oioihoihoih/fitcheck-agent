import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build to a self-contained static bundle that FastAPI serves from webapp-next/dist.
// Relative base so the assets resolve under whatever path the app is mounted at.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5199,
    // In dev, proxy API + SSE to the FastAPI backend so `npm run dev` works end-to-end.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8731",
        changeOrigin: true,
      },
      "/outputs": {
        target: "http://127.0.0.1:8731",
        changeOrigin: true,
      },
    },
  },
});
