import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  // the server mounts the built GUI at /gui
  base: "/gui/",
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    // served from the local server, not a CDN: the map data and main screen
    // chunks are large on purpose, so only warn above 2 MB
    chunkSizeWarningLimit: 2000,
  },
  server: {
    // api.js maps port 8080 to the server's port 5000 during development
    port: 8080,
  },
});
