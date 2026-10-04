import { expect, type Page } from "@playwright/test";

const ORIGIN = "http://localhost:3000";

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

export async function registerAcknowledgedUser(page: Page, prefix: string, name: string): Promise<string> {
  const email = `${prefix}-${Date.now()}-${Math.floor(Math.random() * 10000)}@example.com`;
  const registration = await page.request.post("/api/v1/auth/register", {
    data: { name, email, password: "StockPilot!E2E2026", date_of_birth: "1990-01-01" },
  });
  expect(registration.ok()).toBeTruthy();

  const system = await getSystemWithRetry(page);
  if (!system.ok()) {
    const body = await system.text();
    throw new Error(`/api/v1/system failed with ${system.status()}: ${body}`);
  }
  const version = (await system.json()).research_acknowledgment.version;
  const acknowledgment = await page.request.post("/api/v1/auth/research-acknowledgment", {
    data: { version, accepted: true },
    headers: { Origin: ORIGIN },
  });
  expect(acknowledgment.ok(), `Acknowledgment ${acknowledgment.status()}: ${await acknowledgment.text()}`).toBeTruthy();
  return email;
}
