import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/**
 * The studio is served from `web/dist` by the FastAPI service, so the build
 * output directory is `dist` and the asset paths are absolute (the app is
 * mounted at the root of the origin).
 *
 * In development the same origin is proxied to the service on port 8000, so
 * the socket URL never has to know whether it is talking to Vite or to the
 * container.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/ws": {
        target: "ws://127.0.0.1:8000",
        ws: true,
      },
      "/.well-known": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
});
