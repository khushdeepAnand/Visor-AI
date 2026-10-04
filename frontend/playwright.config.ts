import { defineConfig } from "@playwright/test";
import path from "node:path";
import { randomBytes } from "node:crypto";

const frontendDirectory = import.meta.dirname;
const projectRoot = path.resolve(frontendDirectory, "..");
const venvPython = process.platform === "win32"
  ? path.join(projectRoot, ".venv", "Scripts", "python.exe")
  : path.join(projectRoot, ".venv", "bin", "python");

function shellQuote(value: string): string {
  if (process.platform === "win32") {
    return `"${value.replaceAll('"', '""')}"`;
  }
  return `'${value.replaceAll("'", "'\\''")}'`;
}

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  use: { baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:3000", trace: "retain-on-failure" },
  webServer: [
    {
      command: `${shellQuote(venvPython)} -m uvicorn api.main:app --host 127.0.0.1 --port 8000`,
      cwd: projectRoot,
      env: {
        ...process.env,
        STOCKPILOT_PROVIDER_MODE: "OFFLINE_DEMO",
        STOCKPILOT_ENV: "test",
        STOCKPILOT_CORS_ORIGINS: "http://localhost:3000,http://127.0.0.1:3000",
        STOCKPILOT_COOKIE_SECURE: "false",
        STOCKPILOT_JWT_SECRET: randomBytes(48).toString("hex"),
        // The e2e suite registers a fresh account per spec, which exceeds the
        // shipped abuse-control default (5 accounts / 5 min per IP). Raise the
        // test-environment register budget only; production defaults are untouched.
        STOCKPILOT_REGISTER_RATE_LIMIT: "200",
      },
      url: "http://127.0.0.1:8000/api/v1/ready",
       reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: "npm run dev -- --hostname 127.0.0.1 --port 3000",
      cwd: frontendDirectory,
      url: "http://127.0.0.1:3000",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
