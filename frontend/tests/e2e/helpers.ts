import { expect, type APIResponse, type Page } from "@playwright/test";

async function getSystemWithRetry(page: Page): Promise<any> {
  // Retry indefinitely on 429, with exponential backoff cap
  for (let attempt = 0; ; attempt++) {
    const system = await page.request.get("/api/v1/system");
    if (system.ok()) {
      return system;
    }
    if (system.status() === 429) {
      const retryAfter = parseInt(system.headers()["retry-after"] || "1", 10);
      const delay = Math.min(retryAfter * 1000, 10000);
      await page.waitForTimeout(delay);
      continue;
    }
    // Non-429 error, return immediately
    return system;
  }
}

async function postWithRateLimitRetry(page: Page, url: string, options: Parameters<Page["request"]["post"]>[1]): Promise<APIResponse> {
  // The limiter rejects with 429 *before* processing the request, so a retry
  // is safe. Honor Retry-After (capped) like getSystemWithRetry does.
  for (let attempt = 0; ; attempt++) {
    const response = await page.request.post(url, options);
    if (response.status() !== 429) {
      return response;
    }
    const retryAfter = parseInt(response.headers()["retry-after"] || "1", 10);
    await page.waitForTimeout(Math.min(retryAfter * 1000, 10000));
  }
}

export async function registerAcknowledgedUser(page: Page, prefix: string, name: string): Promise<string> {
  const email = `${prefix}-${Date.now()}-${Math.floor(Math.random() * 10000)}@example.com`;
  const registration = await postWithRateLimitRetry(page, "/api/v1/auth/register", {
    data: { name, email, password: "StockPilot!E2E2026", date_of_birth: "1990-01-01" },
  });
  expect(registration.ok()).toBeTruthy();

  const system = await getSystemWithRetry(page);
  if (!system.ok()) {
    const body = await system.text();
    throw new Error(`/api/v1/system failed with ${system.status()}: ${body}`);
  }
  const version = (await system.json()).research_acknowledgment.version;
  const acknowledgment = await postWithRateLimitRetry(page, "/api/v1/auth/research-acknowledgment", {
    data: { version, accepted: true },
    headers: { Origin: new URL(registration.url()).origin },
  });
  expect(acknowledgment.ok(), `Acknowledgment ${acknowledgment.status()}: ${await acknowledgment.text()}`).toBeTruthy();
  return email;
}
