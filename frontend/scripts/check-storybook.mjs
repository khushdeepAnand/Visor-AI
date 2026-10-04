import { execSync } from "node:child_process";
import { resolve } from "node:path";
import { existsSync } from "node:fs";

const root = resolve(import.meta.dirname, "..");

console.log("Building Storybook...");
try {
  execSync("npm run build-storybook", { cwd: root, stdio: "inherit", env: { ...process.env, CI: "true" } });
} catch (error) {
  throw new Error(`Storybook build failed: ${error.message}`);
}

const storybookStatic = resolve(root, "storybook-static");
if (!existsSync(storybookStatic)) {
  throw new Error("Storybook static build output not found at storybook-static");
}

console.log("Running axe-core accessibility audit on Storybook...");
try {
  execSync("npx axe-cli storybook-static --tags wcag2a,wcag2aa --save", { cwd: root, stdio: "inherit" });
} catch (error) {
  throw new Error(`axe-core accessibility audit failed: ${error.message}`);
}

console.log("Storybook build and accessibility audit passed.");
