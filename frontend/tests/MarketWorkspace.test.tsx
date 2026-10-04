import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MarketWorkspace } from "@/components/MarketWorkspace";
import { MarketProvider } from "@/components/MarketContext";
import { AuthProvider } from "@/lib/auth";
import { vi } from "vitest";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: vi.fn(() => new Promise(() => {})), requestForecast: vi.fn(() => new Promise(() => {})) };
});

// Regression test for a real crash: react-mosaic-component v7's `splitPercentages`
// must have exactly one entry per child of the split (an n-ary API), not one entry
// per split boundary like the old v6 API. A mismatch throws deep inside
// react-mosaic-component's BoundingBox util ("Cannot read properties of undefined
// (reading 'top')") the moment a nested split node is reached with no test to catch it.
describe("MarketWorkspace", () => {
  it("renders the calm view by default without mounting the mosaic", () => {
    const client = new QueryClient();
    expect(() =>
      render(
        <QueryClientProvider client={client}>
          <AuthProvider><MarketProvider>
            <MarketWorkspace onForecast={() => {}} />
          </MarketProvider></AuthProvider>
        </QueryClientProvider>,
      ),
    ).not.toThrow();
    expect(screen.getByRole("button", { name: "Calm" })).toHaveAttribute("aria-pressed", "true");
    expect(document.querySelector(".stockpilot-mosaic")).not.toBeInTheDocument();
  });
});
