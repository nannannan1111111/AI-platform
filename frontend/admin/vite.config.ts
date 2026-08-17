import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { resolve } from "node:path";

export default defineConfig({
  plugins: [vue()],
  define: {
    "process.env.NODE_ENV": JSON.stringify("production"),
  },
  build: {
    outDir: resolve(__dirname, "../../backend/app/webui/static/admin-vue"),
    emptyOutDir: true,
    lib: {
      entry: resolve(__dirname, "src/main.ts"),
      formats: ["iife"],
      name: "LeyunAdmin",
      fileName: () => "admin.js",
    },
    rollupOptions: {
      output: {
        assetFileNames: "admin.[ext]",
      },
    },
  },
});
