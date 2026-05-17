import { defineConfig } from "vite";

export default defineConfig({
  root: "site",
  publicDir: "../public",
  base: "./",
  build: {
    outDir: "../dist",
    emptyOutDir: true,
    assetsDir: "assets",
  },
});
