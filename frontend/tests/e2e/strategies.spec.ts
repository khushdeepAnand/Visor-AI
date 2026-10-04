import { expect, test } from "@playwright/test";
import { registerAcknowledgedUser } from "./helpers";

/**
 * End-to-end coverage for the strategy surfaces added in v11 (Part J).
 *
 * Both features sit behind operator feature flags
 * (`strategy_builder` / `forward_test_tracking`) that default to ON for the
 * v5-compatible shipped build, so on a clean offline-demo backend these specs
 * verify the honest, active builder flows and the permanent paper-only
 * disclosure ("never places an order").
 */

async function registerFreshUser(page: import("@playwright/test").Page) {
  return registerAcknowledgedUser(page, "strategy-e2e", "Strategy Tester");
}

test.describe("strategy surfaces", () => {
  test("strategies page renders the active paper-only builder", async ({ page }) => {
    await registerFreshUser(page);
    await page.goto("/strategies");
    await expect(page.getByRole("heading", { name: /strategies/i })).toBeVisible();
    await expect(page.getByText(/paper only.*no broker order path/i)).toBeVisible();
    await expect(page.locator('[role="status"]').filter({ hasText: /turned off by an operator feature flag/i })).toHaveCount(0);
    await expect(page.locator("body")).not.toContainText("undefined");
    await expect(page.locator("body")).not.toContainText("NaN");
  });

  test("forward-tests page says tracking never places an order", async ({ page }) => {
    await registerFreshUser(page);
    await page.goto("/forward-tests");
    await expect(page.getByRole("heading", { name: /forward tests/i })).toBeVisible();
    await expect(page.getByText(/never place\w* an order/i)).toBeVisible();
    await expect(page.locator('[role="status"]').filter({ hasText: /turned off by an operator feature flag/i })).toHaveCount(0);
    await expect(page.locator("body")).not.toContainText("undefined");
  });
});
