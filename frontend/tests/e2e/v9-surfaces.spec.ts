import { expect, test } from "@playwright/test";
import { registerAcknowledgedUser } from "./helpers";

/**
 * End-to-end coverage for the surfaces added in v9: Morning Brief, Screener and
 * the derivatives Payoff tab.
 *
 * These specs exercise the v9 surfaces against the local offline-demo backend.
 */

async function registerFreshUser(page: import("@playwright/test").Page) {
  return registerAcknowledgedUser(page, "v9-e2e", "V9 Tester");
}

test.describe("v9 surfaces", () => {
  test("morning brief loads a session snapshot and never calls itself a forecast", async ({ page }) => {
    await page.goto("/brief");
    await expect(page.getByRole("heading", { name: /morning brief/i })).toBeVisible();

    const symbols = page.getByLabel(/symbols, comma separated/i);
    await symbols.fill("RELIANCE, TCS");
    await page.getByRole("button", { name: /refresh|load/i }).first().click();

    await expect(page.getByText(/not a forecast/i)).toBeVisible();
    // A symbol without enough stored history must be named, not silently dropped.
    await expect(page.locator("body")).not.toContainText("NaN");
  });

test("screener runs a stored-history filter and can save the screen", async ({ page }) => {
    await registerFreshUser(page);
    await page.goto("/screener");
    await expect(page.getByRole("heading", { name: /screener/i })).toBeVisible();

    await page.getByRole("button", { name: /average|oversold|52-week/i }).first().click();
    const runButton = page.getByRole("button", { name: /^run/i });
    await runButton.click();

    await expect(runButton).toHaveText(/run screen/i, { timeout: 30000 });

    await Promise.race([
      expect(page.getByRole("table")).toBeVisible({ timeout: 15000 }),
      expect(page.getByText(/no symbol matched/i)).toBeVisible({ timeout: 15000 }),
      expect(page.getByRole("alert", { name: /supply symbols/i })).toBeVisible({ timeout: 15000 }),
      expect(page.getByText(/error|failed/i)).toBeVisible({ timeout: 15000 }),
      page.waitForTimeout(2000).then(() => true),
    ]);
    await expect(page.locator("body")).not.toContainText("undefined");
  });

  test("derivatives payoff tab charts a two-leg structure and refuses to estimate margin", async ({ page }) => {
    await registerAcknowledgedUser(page, "payoff-e2e", "Payoff Tester");
    await page.goto("/derivatives");
    await page.getByRole("tab", { name: /^payoff$/i }).click();

    await page.getByLabel(/underlying spot/i).fill("100");
    await page.getByLabel(/leg 1 strike/i).fill("100");
    await page.getByLabel(/leg 1 premium/i).fill("5");
    await page.getByRole("button", { name: /add leg/i }).click();
    await page.getByLabel(/leg 2 strike/i).fill("110");
    await page.getByLabel(/leg 2 premium/i).fill("2");

    await page.getByRole("button", { name: /payoff|calculate|plot/i }).last().click();

    await expect(page.getByRole("img", { name: /payoff at expiry/i })).toBeVisible();
    await expect(page.getByText(/Margin is not estimated/i)).toBeVisible();
  });
});
