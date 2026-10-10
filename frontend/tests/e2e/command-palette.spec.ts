import { test, expect } from "@playwright/test";

test("command palette supports keyboard search, navigation and focus return", async ({ page }) => {
  await page.goto("/");
  const trigger = page.getByRole("button", { name: "Open search and command palette" });
  await trigger.focus();
  await page.keyboard.press("Control+k");
  const dialog = page.getByRole("dialog", { name: "Search and command palette" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("searchbox").fill("track");
  await expect(dialog.getByRole("button", { name: /Track Record/ })).toBeVisible();
  await expect(dialog.getByRole("button", { name: /Portfolio/ })).toHaveCount(0);
  await dialog.getByRole("button", { name: "Close command palette" }).focus();
  await page.keyboard.press("Shift+Tab");
  await expect(dialog.getByRole("searchbox")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(trigger).toBeFocused();
  await trigger.click();
  await dialog.getByRole("searchbox").fill("track");
  await dialog.getByRole("button", { name: /Track Record/ }).click();
  await expect(page).toHaveURL(/track-record/);
});
