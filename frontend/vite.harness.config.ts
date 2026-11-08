import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: [{ find: /^\.\.\/api$/, replacement: "/src/api.mock.ts" }, { find: /^\.\/api$/, replacement: "/src/api.mock.ts" }] },
  build: { outDir: "dist-harness", rollupOptions: { input: "harness.html" } },
});
