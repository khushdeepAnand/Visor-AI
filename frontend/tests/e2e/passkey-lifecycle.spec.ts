import { test, expect } from "@playwright/test";

test("real virtual passkey enrollment and second-factor login", async ({ page, context, baseURL }) => {
  const origin = (baseURL || "http://127.0.0.1:3000").replace("127.0.0.1", "localhost");
  const cdp = await context.newCDPSession(page);
  await cdp.send("WebAuthn.enable");
  await cdp.send("WebAuthn.addVirtualAuthenticator", { options: {
    protocol: "ctap2", transport: "internal", hasResidentKey: true,
    hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true,
  } });
  const email = `passkey-${Date.now()}@example.test`;
  await page.goto(`${origin}/register?next=/account`);
  await page.getByLabel("Name", { exact: true }).fill("Passkey test");
  await page.getByLabel("Email address", { exact: true }).fill(email);
  await page.getByLabel("Password", { exact: true }).fill("PasskeyPassword123!");
  await page.getByLabel("Date of birth").fill("1990-01-01");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/onboarding/);
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.getByLabel("Forecast coverage is not the probability of making a profit.").check();
  await page.getByLabel("Paper simulation is not live order execution.").check();
  await page.getByRole("button", { name: "Acknowledge and open StockPilot" }).click();
  await expect(page).toHaveURL(/account$/);
  await page.getByRole("button", { name: "Add passkey", exact: true }).click();
  await expect(page.getByText("Passkey added", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page).toHaveURL(/login/);
  await page.getByLabel("Email address", { exact: true }).fill(email);
  await page.getByLabel("Password", { exact: true }).fill("PasskeyPassword123!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Verify your sign-in" })).toBeVisible();
  await page.getByRole("button", { name: "Verify with passkey" }).click();
  await expect(page).not.toHaveURL(/login/);
});
