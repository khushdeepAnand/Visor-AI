import { test, expect, type Page } from "@playwright/test";

const user = { id: 999, name: "Mock User", email: "mock@example.test", role: "user", research_acknowledgment_required: false };
const sessions = [
  { id: 1, device_label: "Chrome on Windows", issued_at: "2026-10-01T10:00:00Z", expires_at: "2026-10-10T10:00:00Z", current: true },
  { id: 2, device_label: "Firefox on Mac", issued_at: "2026-10-01T09:00:00Z", expires_at: "2026-10-10T09:00:00Z", current: false },
];

async function mockAccount(page: Page) {
  await page.route("**/api/v1/auth/me", route => route.fulfill({ json: { user } }));
  await page.route("**/api/v1/auth/mfa", route => route.fulfill({ json: { enabled: false, recovery_codes_remaining: 0 } }));
  await page.route("**/api/v1/auth/sessions", route => route.fulfill({ json: { items: sessions } }));
}

test("password login transitions to MFA and verifies a mocked code", async ({ page }) => {
  let signedIn = false;
  await page.route("**/api/v1/auth/me", route => route.fulfill(signedIn ? { json: { user } } : { status: 401, json: { detail: "Sign in required" } }));
  await page.route("**/api/v1/auth/login", route => route.fulfill({ json: { mfa_required: true } }));
  await page.route("**/api/v1/auth/mfa/verify", async route => {
    expect(route.request().postDataJSON().code).toBe("123456");
    signedIn = true;
    await route.fulfill({ json: { next: "/brief" } });
  });
  await page.goto("/login?next=/brief");
  await page.getByLabel("Email address", { exact: true }).fill(user.email);
  await page.getByLabel("Password", { exact: true }).fill("not-a-real-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByLabel("Authenticator or recovery code").fill("123456");
  await page.getByRole("button", { name: "Verify and sign in" }).click();
  await expect(page).toHaveURL(/\/brief$/);
});

test("registration creates account and enters onboarding", async ({ page }) => {
  let signedIn = false;
  await page.route("**/api/v1/auth/me", route => route.fulfill(signedIn ? { json: { user } } : { status: 401, json: { detail: "Sign in required" } }));
  await page.route("**/api/v1/auth/register", async route => {
    signedIn = true;
    await route.fulfill({ json: { user } });
  });
  await page.goto("/register?next=/brief");
  await page.getByLabel("Name", { exact: true }).fill(user.name);
  await page.getByLabel("Email address", { exact: true }).fill(user.email);
  await page.getByLabel("Password", { exact: true }).fill("not-a-real-password");
  await page.getByLabel("Date of birth").fill("1990-01-01");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/onboarding\?next=/);
});

test("account enables MFA and exposes one-time recovery codes", async ({ page }) => {
  await mockAccount(page);
  await page.route("**/api/v1/auth/mfa/setup", route => route.fulfill({ json: { secret: "MOCK-SETUP-KEY", provisioning_uri: "otpauth://totp/Mock" } }));
  await page.route("**/api/v1/auth/mfa/enable", async route => {
    expect(route.request().postDataJSON().code).toBe("123456");
    await route.fulfill({ json: { enabled: true, recovery_codes: ["MOCK-CODE-1", "MOCK-CODE-2"] } });
  });
  await page.goto("/account");
  await page.getByRole("button", { name: "Set up authenticator" }).click();
  await expect(page.getByText("MOCK-SETUP-KEY")).toBeVisible();
  await page.getByLabel("Current 6-digit code").fill("123456");
  await page.getByRole("button", { name: "Verify and enable", exact: true }).click();
  await expect(page.getByText("MOCK-CODE-1")).toBeVisible();
});

test("session list displays current and other sessions", async ({ page }) => {
  await mockAccount(page);
  await page.goto("/account");
  await expect(page.getByText("Chrome on Windows · current")).toBeVisible();
  await expect(page.getByText("Firefox on Mac", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Current session" })).toBeDisabled();
});

test("session revoke removes the selected other session", async ({ page }) => {
  await mockAccount(page);
  let revoked = false;
  await page.route("**/api/v1/auth/sessions", route => route.fulfill({ json: { items: revoked ? sessions.slice(0, 1) : sessions } }));
  await page.route("**/api/v1/auth/sessions/2", async route => {
    expect(route.request().method()).toBe("DELETE");
    revoked = true;
    await route.fulfill({ json: { revoked: true } });
  });
  await page.goto("/account");
  await page.getByRole("button", { name: "Revoke Firefox on Mac session" }).click();
  await expect(page.getByText("Firefox on Mac", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Chrome on Windows · current")).toBeVisible();
});

test("revoke all active sessions returns to login", async ({ page }) => {
  await mockAccount(page);
  await page.route("**/api/v1/auth/logout-all", async route => {
    expect(route.request().method()).toBe("POST");
    await page.route("**/api/v1/auth/me", inner => inner.fulfill({ status: 401, json: { detail: "Sign in required" } }));
    await route.fulfill({ json: { revoked: true } });
  });
  await page.goto("/account");
  await page.getByRole("button", { name: "Revoke all active sessions" }).click();
  await expect(page).toHaveURL(/\/($|account|login)/);
});
