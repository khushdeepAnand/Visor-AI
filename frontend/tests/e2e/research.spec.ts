import { expect, test } from "@playwright/test";
import { registerAcknowledgedUser } from "./helpers";

/**
 * End-to-end coverage for the K5 grounded research assistant.
 *
 * The offline-demo build has no OpenAI-compatible provider configured, so the
 * page must render the sourced fact table plus an explicit refusal - never a
 * fabricated answer. Advice-shaped questions must be refused the same way.
 */

test.describe("research assistant", () => {
  test("refuses advice and still shows the sourced fact table", async ({ page }) => {
    await registerAcknowledgedUser(page, "research-e2e", "Research Tester");
    await page.goto("/research");
    await expect(page.getByRole("heading", { name: /research assistant/i })).toBeVisible();

    await page.getByLabel("question").fill("Should I buy RELIANCE?");
    await page.getByRole("button", { name: /grounded only/i }).click();

    // Wait for the grounded-only response to complete
    await expect(page.getByText(/does not give investment advice/i)).toBeVisible({ timeout: 15000 });
    await expect(page.getByText(/Sourced facts for RELIANCE/i)).toBeVisible();
    await expect(page.getByText(/last close/i)).toBeVisible();
    await expect(page.locator("body")).not.toContainText("undefined");
  });
});
