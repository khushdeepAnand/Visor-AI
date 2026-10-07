import { defineConfig } from "@playwright/test";
import path from "node:path";
import { randomBytes } from "node:crypto";

const frontendDirectory = import.meta.dirname;
const projectRoot = path.resolve(frontendDirectory, "..");
const apiPort = Number(process.env.STOCKPILOT_E2E_API_PORT || 8000);
const frontendPort = Number(process.env.STOCKPILOT_E2E_FRONTEND_PORT || 3000);
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
  use: { baseURL: process.env.PLAYWRIGHT_BASE_URL || `http://127.0.0.1:${frontendPort}`, trace: "retain-on-failure" },
  webServer: [
    {
      command: `${shellQuote(venvPython)} scripts/run_e2e_backend.py`,
      cwd: projectRoot,
      env: {
        ...process.env,
        STOCKPILOT_PROVIDER_MODE: "OFFLINE_DEMO",
        STOCKPILOT_ENV: "test",
        STOCKPILOT_CORS_ORIGINS: `http://localhost:${frontendPort},http://127.0.0.1:${frontendPort}`,
        STOCKPILOT_WEBAUTHN_ORIGINS: `http://localhost:${frontendPort}`,
        STOCKPILOT_E2E_API_PORT: String(apiPort),
        STOCKPILOT_COOKIE_SECURE: "false",
        STOCKPILOT_JWT_SECRET: randomBytes(48).toString("hex"),
        // The e2e suite registers a fresh account per spec, which exceeds the
        // shipped abuse-control default (5 accounts / 5 min per IP). Raise the
        // test-environment register budget only; production defaults are untouched.
        STOCKPILOT_REGISTER_RATE_LIMIT: "200",
      },
      url: `http://127.0.0.1:${apiPort}/api/v1/ready`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `npm run ${process.env.STOCKPILOT_E2E_PRODUCTION === "true" ? "start" : "dev"} -- --hostname 127.0.0.1 --port ${frontendPort}`,
      cwd: frontendDirectory,
      env: { ...process.env, NEXT_PUBLIC_API_ORIGIN: `http://127.0.0.1:${apiPort}`, STOCKPILOT_NEXT_DIST_DIR: process.env.STOCKPILOT_NEXT_DIST_DIR || ".next-e2e" },
      url: `http://127.0.0.1:${frontendPort}`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
