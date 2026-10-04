import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError, api } from "@/lib/api";
import SectorsPage from "@/app/sectors/page";

vi.mock("next/navigation", () => ({
  usePathname: () => "/sectors",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/components/TerminalShell", () => ({
  TerminalShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
  default: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ user: { id: 1, email: "user@example.com", is_admin: false }, loading: false }),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: vi.fn() };
});

const partialPayload = {
  generated_at: "2026-09-17T00:00:00+00:00",
  partial_coverage: true,
  sector_count: 2,
  sectors: [
    {
      sector: "Energy",
      symbol_count: 1,
      measured_count: 1,
      avg_change_pct_5d: 10,
      symbols: [{ symbol: "RELIANCE", change_pct_5d: 10 }],
    },
    {
      sector: "Financials",
      symbol_count: 2,
      measured_count: 1,
      avg_change_pct_5d: -3.5,
      symbols: [
        { symbol: "HDFCBANK", change_pct_5d: -3.5 },
        { symbol: "ICICIBANK", change_pct_5d: null },
      ],
    },
  ],
  coverage: {
    catalogue: { total: 100, sector_populated: 3, sector_populated_pct: 3, distinct_sectors: 2 },
    runtime_eq_total: null,
    basis: "Sector metadata is read from the search catalogue's `symbols.sector` column.",
  },
  disclosures: ["PARTIAL COVERAGE: sector metadata exists for 3 of 100 catalogue symbols (3.0%)."],
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SectorsPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(api).mockReset();
});

describe("sectors page (K3)", () => {
  it("shows an explicit partial-coverage banner when catalogue metadata is thin", async () => {
    vi.mocked(api).mockResolvedValue(partialPayload as never);
    renderPage();
    expect(await screen.findByText(/partial coverage/i)).toBeInTheDocument();
    expect(await screen.findByText(/exists for 3 of 100 catalogue symbols \(3.0%\)/i)).toBeInTheDocument();
  });

  it("sorts sectors by average realized change and renders disclosures", async () => {
    vi.mocked(api).mockResolvedValue(partialPayload as never);
    renderPage();
    const energy = await screen.findByText("Energy");
    const financials = screen.getByText("Financials");
    expect(energy.compareDocumentPosition(financials) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText(/PARTIAL COVERAGE: sector metadata exists/i)).toBeInTheDocument();
  });

  it("expands symbols with per-symbol change links", async () => {
    vi.mocked(api).mockResolvedValue(partialPayload as never);
    renderPage();
    await userEvent.click(await screen.findByText("Financials"));
    expect(await screen.findByText("HDFCBANK")).toBeInTheDocument();
    const link = screen.getByText("HDFCBANK").closest("a");
    expect(link).toHaveAttribute("href", "/markets/HDFCBANK");
    expect(screen.getByText("n/a")).toBeInTheDocument();
  });

  it("renders the operator-flag disabled banner when the endpoint is gated", async () => {
    vi.mocked(api).mockRejectedValue(new ApiError("Feature is disabled", 503, "feature_disabled", null, false));
    renderPage();
    expect(await screen.findByRole("status")).toHaveTextContent(/turned off by an operator feature flag/i);
  });

  it("renders an empty state when no sectors are assigned", async () => {
    vi.mocked(api).mockResolvedValue({ ...partialPayload, sector_count: 0, sectors: [] } as never);
    renderPage();
    expect(await screen.findByText(/No symbols have sector assignments/i)).toBeInTheDocument();
  });
});