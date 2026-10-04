import { expect, test } from "@playwright/test";

const stamp = Date.now();
const email = `stockpilot-e2e-${stamp}@example.com`;
const password = "StockPilot!E2E2026";

async function mockDemoHealth(page: import("@playwright/test").Page) {
  await page.route("**/api/v1/health", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ status: "operational", market_data: { provider_mode: "OFFLINE_DEMO", providers: [{ provider: "demo_india", configured: true }] } }),
  }));
}

test("core research and paper-trading flow", async ({ page }) => {
  test.slow();
  await mockDemoHealth(page);
  await page.goto("/register");
  await page.getByPlaceholder(/name/i).fill("E2E Trader");
  await page.getByPlaceholder(/email/i).fill(email);
const passwords = page.locator('input[type="password"]');
  await passwords.nth(0).fill(password);
  if (await passwords.count() > 1) await passwords.nth(1).fill(password);
  await page.locator("#register-dob").fill("1990-01-01");
  await page.getByRole("button", { name: /register|create/i }).click();

  await expect(page).toHaveURL(/\/onboarding/, { timeout: 30_000 });
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByLabel(/coverage is not/i).check();
  await page.getByLabel(/paper simulation is not/i).check();
  await page.getByRole("button", { name: "Open StockPilot" }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("status").filter({ hasText: "Demo / synthetic data" })).toBeVisible();
  await page.getByRole("button", { name: /Log out/i }).click();
  await expect(page).toHaveURL(/\/$/);
  await page.getByRole("link", { name: /sign in/i }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.getByPlaceholder(/email/i).fill(email);
  await page.getByPlaceholder(/password/i).fill(password);
  await page.getByRole("button", { name: /Sign in/i }).click();
  await expect(page).toHaveURL(/\/$/);

  await page.goto("/markets/RELIANCE");
  await expect(page.getByRole("status").filter({ hasText: "Demo / synthetic data" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "RELIANCE" })).toBeVisible();
  await expect(page.getByRole("button", { name: "1m" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Calm" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("heading", { name: "Forecast Corridor" })).toBeVisible();
  await page.getByRole("button", { name: "Pro mosaic" }).click();
  await expect(page.locator(".mosaic-window-title").filter({ hasText: "Live chart + research range" }).first()).toBeVisible();
  await expect(page.locator(".mosaic-window-title").filter({ hasText: "Technical indicators" }).first()).toBeVisible();

  await page.goto("/paper-trading");
  await expect(page.getByRole("status").filter({ hasText: "Demo / synthetic data" })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Paper Trading Lab/i })).toBeVisible();
  await page.getByLabel("Shared market symbol").fill("RELIANCE");
  await page.getByLabel("Paper order side").selectOption("BUY");
  await page.getByPlaceholder(/Quantity/i).fill("1");
  await page.getByLabel("Required paper trade thesis").fill("Testing a deliberate paper-only decision.");
  await page.getByRole("button", { name: /Review paper order/i }).click();
  await page.getByRole("button", { name: /Confirm simulation/i }).click();
  await expect(page.getByText(/Paper trade only/i)).toBeVisible();

  await page.goto("/portfolio");
  await expect(page.getByRole("status").filter({ hasText: "Demo / synthetic data" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Portfolio" })).toBeVisible();

  await page.getByRole("button", { name: /Log out/i }).click();
  await expect(page).toHaveURL(/\/$/);
});

test("futures lab posts analytical scenario inputs", async ({ page }) => {
  let submitted: Record<string, unknown> | undefined;
  await page.route("**/api/v1/derivatives/futures/analyse", async (route) => {
    submitted = route.request().postDataJSON();
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({
      spot_price: 22000, futures_price: 22100, days_to_expiry: 30, basis: 100, basis_pct: .45,
      annualized_basis_pct: 5.53, theoretical_fair_value: 22118, fair_value_mispricing_pct: -.08,
      open_interest: 120000, change_in_open_interest_pct: 20, futures_price_change_pct: 1.37,
      classification: "Long buildup", interpretation: "Price and open interest are rising.",
      data_status: "Analytical calculation using supplied inputs.",
    }) });
  });
  await page.goto("/derivatives");
  // The source banner is rendered after the client health query, so this also
  // ensures tab handlers have hydrated before the interaction below.
  await expect(page.getByRole("status").filter({ hasText: "Demo / synthetic data" })).toBeVisible();
  await page.getByRole("tab", { name: "Futures Basis" }).click();
  await page.getByLabel("Spot price").fill("22000");
  await page.getByLabel("Futures price", { exact: true }).fill("22100");
  await page.getByLabel("Previous futures").fill("21800");
  await page.getByLabel("Open interest", { exact: true }).fill("120000");
  await page.getByLabel("Previous OI").fill("100000");
  await page.getByRole("button", { name: "Run analytical scenario" }).click();
  await expect(page.getByText(/Analytical scenario, not a validated forecast/)).toBeVisible();
  expect(submitted).toMatchObject({ spot_price: 22000, futures_price: 22100, previous_futures_price: 21800, open_interest: 120000, previous_open_interest: 100000 });
});

test("offline demo banner persists on every supported route", async ({ page }) => {
  await mockDemoHealth(page);
  const routes = [
    "/", "/login", "/register", "/markets/RELIANCE", "/compare", "/watchlist",
    "/portfolio", "/paper-trading", "/alerts", "/risk", "/derivatives", "/system",
  ];
  for (const route of routes) {
    await page.goto(route);
    await expect(page.getByRole("status").filter({ hasText: "Demo / synthetic data" }), route).toBeVisible();
  }
});

test("invalid live credentials remain unavailable without demo fallback", async ({ page }) => {
  await page.route("**/api/v1/health", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ status: "operational", market_data: { provider_mode: "LIVE_ONLY", providers: [] } }),
  }));
  await page.route("**/api/v1/market/**", (route) => route.fulfill({
    status: 503,
    contentType: "application/json",
    body: JSON.stringify({ detail: "Market data is temporarily unavailable." }),
  }));
  await page.route("**/api/v1/predict/**", (route) => route.fulfill({
    status: 503,
    contentType: "application/json",
    body: JSON.stringify({ detail: "Market data is temporarily unavailable." }),
  }));
  await page.route("**/api/v1/indicators/**", (route) => route.fulfill({
    status: 503,
    contentType: "application/json",
    body: JSON.stringify({ detail: "Market data is temporarily unavailable." }),
  }));

  await page.goto("/markets/RELIANCE");
  await expect(page.locator('[aria-label^="Live data unavailable"]')).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "Demo / synthetic data" })).toHaveCount(0);
});

test("navigation and calm workflow adapt from 360 to 1440", async ({ page }) => {
  await mockDemoHealth(page);
  await page.setViewportSize({ width: 360, height: 800 });
  await page.goto("/markets/RELIANCE");
  const mobileNav = page.getByRole("navigation", { name: "Primary navigation" }).last();
  await expect(mobileNav.getByText("Home", { exact: true })).toBeVisible();
  await expect(mobileNav.getByText("Explore", { exact: true })).toBeVisible();
  await expect(mobileNav.getByText("Paper Trade", { exact: true })).toBeVisible();
  await expect(mobileNav.getByText("Portfolio", { exact: true })).toBeVisible();
  const moreButton = mobileNav.getByRole("button", { name: "More" });
  await moreButton.click();
  await expect(moreButton).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("link", { name: /Risk Lab/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /System/ })).toBeVisible();
  await page.getByRole("button", { name: "Close navigation" }).click();
  await expect(page.getByRole("button", { name: "Pro mosaic" })).toBeDisabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

  await page.setViewportSize({ width: 768, height: 900 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

  await page.setViewportSize({ width: 1024, height: 900 });
  await expect(page.getByRole("complementary")).toBeVisible();
  await expect(page.getByRole("button", { name: "Pro mosaic" })).toBeEnabled();
  const chartAt1024 = await page.getByTestId("market-chart-panel").boundingBox();
  const corridorAt1024 = await page.getByTestId("forecast-corridor-panel").boundingBox();
  expect(corridorAt1024!.y).toBeGreaterThan(chartAt1024!.y);

  await page.setViewportSize({ width: 1440, height: 900 });
  const chartAt1440 = await page.getByTestId("market-chart-panel").boundingBox();
  const corridorAt1440 = await page.getByTestId("forecast-corridor-panel").boundingBox();
  expect(Math.abs(corridorAt1440!.y - chartAt1440!.y)).toBeLessThan(5);
  expect(corridorAt1440!.x).toBeGreaterThan(chartAt1440!.x);
});
