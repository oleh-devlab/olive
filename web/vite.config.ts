import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The bot serves the page itself (modules/schedule_web.py): index.html at "/",
// everything else under "/static/" -- so the build goes where that looks, and
// asset URLs carry the prefix.
export default defineConfig({
  base: "/static/",
  plugins: [react()],
  build: {
    outDir: "../src/static/schedule",
    // Outside this directory, so Vite will only clear it when told to; stale
    // hashed bundles from an older build would otherwise pile up.
    emptyOutDir: true,
  },
});
