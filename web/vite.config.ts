import { fileURLToPath, URL } from "node:url";

import { loadEnv, type Plugin } from "vite";
import { configDefaults, defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// esbuild's JS API hands a transform payload to the native service through a
// temp file in %TEMP% as soon as the payload exceeds 1 MiB (see
// node_modules/esbuild/lib/main.js, the "input.length > 1024 * 1024" branch),
// and deletes that file afterwards. Hosts where the delete is denied abort the
// build at the very end, after the whole bundle has been produced. Keeping every
// chunk below the threshold keeps the payload on the stdio path instead, so the
// build never depends on temp-file deletion succeeding.
const ESBUILD_TEMP_FILE_THRESHOLD = 1024 * 1024;

function chunkSizeGuard(): Plugin {
  return {
    name: "chunk-size-guard",
    apply: "build",
    enforce: "pre",
    renderChunk(code, chunk) {
      const bytes = Buffer.byteLength(code, "utf8");
      const megabytes = (bytes / 1024 / 1024).toFixed(2);
      if (bytes > ESBUILD_TEMP_FILE_THRESHOLD) {
        this.warn(
          `${chunk.fileName} is ${megabytes} MB, over esbuild's 1 MiB temp-file threshold; split it in manualChunks below`,
        );
      } else {
        this.info(`${chunk.fileName} ${megabytes} MB`);
      }
      return null;
    },
  };
}

const vendorChunk = (id: string): string | undefined => {
  if (!id.includes("node_modules")) return undefined;
  const path = id.slice(id.lastIndexOf("node_modules") + "node_modules".length + 1);
  const [first, second] = path.split("/");
  const name = first.startsWith("@") ? `${first}/${second}` : first;
  if (["react", "react-dom", "scheduler", "use-sync-external-store"].includes(name)) return "vendor-react";
  if (name.startsWith("react-router") || name.startsWith("@remix-run")) return "vendor-router";
  if (name.startsWith("@tanstack")) return "vendor-query";
  if (name.startsWith("react-hook-form") || name.startsWith("@hookform") || name.startsWith("zod")) return "vendor-forms";
  if (name.startsWith("lucide-react")) return "vendor-icons";
  return "vendor-misc";
};

const sourceChunk = (id: string): string | undefined => {
  const marker = "/src/";
  const index = id.lastIndexOf(marker);
  if (index === -1) return undefined;
  const [top] = id.slice(index + marker.length).split("/");
  if (top === "pages") return "app-pages";
  if (top === "components") return "app-components";
  if (top === "lib" || top === "test") return "app-core";
  return undefined;
};

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiTarget =
    process.env.VITE_API_TARGET || env.VITE_API_TARGET || "http://127.0.0.1:3681";
  return {
  plugins: [react(), tailwindcss(), chunkSizeGuard()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    host: "127.0.0.1",
    port: Number(env.VITE_PORT) || 3680,
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: true,
      },
      "/v1": {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: (id: string) => vendorChunk(id) ?? sourceChunk(id),
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    exclude: [...configDefaults.exclude, "e2e/**"],
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    // The jsdom UI tests are render-heavy; the default 5s flakes when the
    // whole suite runs in parallel on a busy machine.
    testTimeout: 30_000,
    hookTimeout: 30_000,
  },
  };
});