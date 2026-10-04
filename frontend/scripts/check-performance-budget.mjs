import { execSync } from "node:child_process";
import { resolve } from "node:path";
import { existsSync } from "node:fs";

const root = resolve(import.meta.dirname, "..");

if (!existsSync(resolve(root, ".next"))) {
  console.log("No .next build found; building Next.js production bundle...");
  try {
    execSync("npm run build", { cwd: root, stdio: "inherit", env: { ...process.env, CI: "true" } });
  } catch (error) {
    throw new Error(`Next.js build failed: ${error.message}`);
  }
}

console.log("Running Lighthouse CI performance budget check...");
try {
  execSync("npx lhci autorun --collect.url=http://localhost:3000 --collect.startServerCommand='npm run start' --collect.settings.headless=true", { cwd: root, stdio: "inherit", timeout: 300000 });
} catch (error) {
  throw new Error(`Lighthouse CI budget check failed: ${error.message}`);
}

console.log("Lighthouse CI performance budget check passed.");
