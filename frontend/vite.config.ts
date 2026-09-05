import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  // Relative asset URLs so the built site works at the distribution root or
  // proxied under a path prefix such as taxhance.com/pii-redaction/.
  base: "",
  appType: "mpa",
  plugins: [react()],
  build: {
    assetsInlineLimit: 0,
    rollupOptions: {
      input: {
        landing: "index.html",
        app: "app/index.html",
        selfHosting: "self-hosting/index.html",
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/v1": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test-setup.ts",
  },
});
