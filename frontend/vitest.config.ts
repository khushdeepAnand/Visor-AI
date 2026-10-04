import { defineConfig } from "vitest/config";
import path from "path";
export default defineConfig({
  oxc: { jsx: { runtime: "automatic" } },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    dangerouslyIgnoreUnhandledErrors: false,
    pool: "forks",
    fileParallelism: false,
    maxWorkers: 1,
    exclude: ["node_modules/**", "tests/e2e/**"]
  },
  resolve: { alias: { "@": path.resolve(import.meta.dirname, ".") } }
});
