import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("next/navigation", () => ({
  usePathname: () => "/forward-tests",
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

const forwardTests = {
  forward_tests: [
    {
      id: 7,
      strategy_id: 1,
      name: "Trend follow",
      symbols: ["RELIANCE", "TCS"],
      status: "active",
      started_at: "2026-09-12T09:15:00+05:30",
      last_evaluated_at: "2026-09-17T15:30:00+05:30",
    },
    {
      id: 8,
      strategy_id: 2,
      name: "Mean reversion",
      symbols: ["INFY"],
      status: "stopped",
      started_at: "2026-08-01T09:15:00+05:30",
      stopped_at: "2026-08-20T15:30:00+05:30",
    },
  ],
  order_placement: "never",
};

const strategies = {
  strategies: [{ id: 1, name: "Trend follow", definition: { symbols: ["RELIANCE", "TCS"] } }],
};

const scorecardFixture = {
  forward_test: forwardTests.forward_tests[0],
  scorecard: {
    closed_trades: 2,
    wins: 1,
    losses: 1,
    win_rate_pct: 50,
    avg_return_pct: 1.1,
    best_return_pct: 3.2,
    worst_return_pct: -1.0,
    max_drawdown_pct: -1.0,
    compounded_return_pct: 2.1,
    basis: "Signal-to-signal closes, gross of costs. Paper observation only.",
  },
  trades: [
    { symbol: "RELIANCE", entry_at: "2026-09-12T09:15:00+05:30", exit_at: "2026-09-15T15:30:00+05:30", entry_price: 3100, exit_price: 3200, exit_reason: "target", return_pct: 3.2 },
    { symbol: "TCS", entry_at: "2026-09-13T09:15:00+05:30", exit_at: "2026-09-16T15:30:00+05:30", entry_price: 3900, exit_price: 3860, exit_reason: "stop_loss", return_pct: -1.0 },
  ],
  open_positions: [{ symbol: "TCS", entry_at: "2026-09-17T09:15:00+05:30", entry_price: 3880 }],
  observed_signals: 5,
  order_placement: "never",
  evidence: { basis: "Recorded end-of-session rule matches from the start date onward." },
  disclosures: ["No order is ever placed."],
};

function makeApiHandler() {
  return vi.fn(async (path: string, init?: RequestInit) => {
    if (path === "/api/v1/forward-tests") {
      if (init?.method === "POST") {
        return { id: 9, strategy_id: 1, name: "Trend follow", symbols: ["RELIANCE"], status: "active", started_at: "2026-09-18T09:15:00+05:30", order_placement: "never" };
      }
      return forwardTests;
    }
    if (path.includes("/scorecard")) return scorecardFixture;
    if (path === "/api/v1/strategies") return strategies;
    throw new Error(`unexpected call: ${path}`);
  });
}

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: makeApiHandler() };
});

import ForwardTestsPage from "@/app/forward-tests/page";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ForwardTestsPage />
    </QueryClientProvider>,
  );
}

describe("forward tests page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("lists running and stopped tests and labels order placement as never", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByRole("heading", { name: /forward tests/i })).toBeInTheDocument());
    expect(screen.getByText("Never places an order")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("Trend follow")).toBeInTheDocument());
    expect(screen.getByText("Mean reversion")).toBeInTheDocument();
  });

  it("can start a forward test and loads its scorecard afterward", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByLabelText("Strategy to forward test")).toBeInTheDocument());
    const select = screen.getByLabelText("Strategy to forward test");
    await waitFor(() => expect(screen.getByRole("option", { name: /trend follow/i })).toBeInTheDocument());
    await userEvent.selectOptions(select, "1");
    await userEvent.click(screen.getByRole("button", { name: /^start tracking$/i }));
    await waitFor(() => expect(screen.getByRole("heading", { name: /scorecard:/i })).toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders a scorecard built from closed pairs only", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByLabelText("Strategy to forward test")).toBeInTheDocument());
    await waitFor(() => expect(screen.getAllByRole("button", { name: /scorecard/i }).length).toBeGreaterThan(0));
    await userEvent.click(screen.getAllByRole("button", { name: /scorecard/i })[0]);
    await waitFor(() => expect(screen.getAllByText("50%").length).toBeGreaterThan(0));
    expect(screen.getByText("No order is ever placed.")).toBeInTheDocument();
    const openPosition = screen.getByText(/Open position in TCS/);
    expect(openPosition).toBeInTheDocument();
    expect(openPosition.textContent).toContain("excluded from the summary");
  });
});

describe("forward tests page when the flag is off", () => {
  it("explains that forward tracking is disabled without crashing", async () => {
    const { api } = await import("@/lib/api");
    const { ApiError } = await import("@/lib/api");
    vi.mocked(api).mockRejectedValue(new ApiError("Feature is disabled", 503, "feature_disabled", null, false));
    renderPage();
    await waitFor(() => expect(screen.getByText(/turned off by an operator feature flag/i)).toBeInTheDocument());
    expect(screen.queryByLabelText("Strategy to forward test")).not.toBeInTheDocument();
  });
});
