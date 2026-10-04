import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  usePathname: () => "/brief",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("@/components/TerminalShell", () => ({
  TerminalShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
  default: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ user: { id: 1, email: "user@example.com", is_admin: false }, loading: false }),
}));

const brief = {
  generated_at: "2026-09-09T03:30:00+05:30",
  session: { state: "pre_open", label: "Pre-open, cash market opens at 09:15 IST", as_of: "2026-09-08" },
  movers: {
    basis: "Sorted by realized 1-session percentage change.",
    gainers: [{ symbol: "TCS", change_pct_1d: 2.4 }],
    losers: [{ symbol: "ITC", change_pct_1d: -1.8 }],
    volume_leaders: [],
    widest_realized_range: [],
  },
  symbols: [
    { symbol: "TCS", last_close: 3900, change_pct_1d: 2.4, change_pct_5d: 3.1, volume_vs_20d_average: 1.4, freshness: { stale: false } },
    { symbol: "ITC", last_close: 410, change_pct_1d: -1.8, change_pct_5d: -2.2, volume_vs_20d_average: 0.8, freshness: { stale: true } },
  ],
  headlines: { state: "unavailable", items: [], note: "No configured news source." },
  coverage: { requested: 3, considered: 3, included: 2, excluded: [{ symbol: "NEWCO", reason: "insufficient_history" }], max_symbols: 50, truncated: false },
  evidence: { basis: "Stored daily candles only.", min_sessions_required: 21, stale_symbols: ["ITC"], is_forecast: false, is_recommendation: false },
  disclosures: ["This brief describes what already happened. It is not a forecast."],
};

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: vi.fn(async () => brief) };
});

import BriefPage from "@/app/brief/page";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <BriefPage />
    </QueryClientProvider>,
  );
}

describe("morning brief page", () => {
  it("shows the session state, movers and covered symbols", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText(/Pre-open/)).toBeInTheDocument());
    expect(screen.getByRole("heading", { name: /Morning brief/i })).toBeInTheDocument();
    expect(screen.getByText("Gainers")).toBeInTheDocument();
    expect(screen.getAllByText("TCS").length).toBeGreaterThan(0);
  });

  it("marks stale rows and names excluded symbols instead of hiding them", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("stale")).toBeInTheDocument());
    expect(screen.getByText(/NEWCO \(insufficient_history\)/)).toBeInTheDocument();
  });

  it("repeats the backend disclosure that the brief is not a forecast", async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/It is not a forecast/)).toBeInTheDocument(),
    );
  });

  it("exposes a labelled symbols field so the covered set can be changed", async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByLabelText("Override symbols, comma separated")).toBeInTheDocument(),
    );
  });
});
