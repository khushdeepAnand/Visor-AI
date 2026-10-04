import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  usePathname: () => "/strategies",
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

// jsdom cannot measure container sizes, so ResponsiveContainer emits its
// "width(0) and height(0)" warning at mount. Hand it explicit dimensions so
// the equity curve render path is still exercised without console noise.
vi.mock("recharts", async () => {
  const actual = await vi.importActual<typeof import("recharts")>("recharts");
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) => {
      const sized = React.cloneElement(children as React.ReactElement<{ width?: number; height?: number }>, { width: 420, height: 200 });
      return <div data-testid="mock-responsive-container">{sized}</div>;
    },
  };
});

const metadata = {
  metrics: [
    { name: "close", label: "Close", unit: "inr", sessions_required: 1, basis: "Session close." },
    { name: "sma_20", label: "SMA 20", unit: "inr", sessions_required: 20, basis: "20-session simple moving average of close." },
    { name: "rsi_14", label: "RSI 14", unit: "index", sessions_required: 15, basis: "Wilder RSI over 14 sessions." },
  ],
  operators: [
    { name: "gt", label: "is greater than", arity: 1 },
    { name: "gte", label: "is greater than or equal to", arity: 1 },
    { name: "lt", label: "is less than", arity: 1 },
    { name: "lte", label: "is less than or equal to", arity: 1 },
    { name: "eq", label: "equals", arity: 1 },
    { name: "between", label: "is between", arity: 2 },
    { name: "cross_above", label: "crosses above", arity: 1 },
    { name: "cross_below", label: "crosses below", arity: 1 },
  ],
  starters: [
    {
      name: "Momentum",
      symbol: "named",
      symbols: ["RELIANCE", "TCS"],
      quantity: 1,
      entry: {},
      exit: null,
    },
  ],
  execution: "paper_only",
};

const saved = { strategies: [{ id: 1, name: "Trend follow", definition: { symbols: ["RELIANCE", "TCS"], quantity: 1 } }] };

const backtest = {
  generated_at: "2026-09-18T00:00:00Z",
  symbol: "RELIANCE",
  strategy: { name: "Test", direction: "long_only" },
  assumptions: { quantity: 1, spread_bps: 5, slippage_bps: 2, fill_basis: "Session close adjusted by half-spread plus slippage." },
  sessions_available: 260,
  summary: {
    trades: 3,
    wins: 2,
    losses: 1,
    win_rate_pct: 66.67,
    gross_pnl: 240,
    costs: 60,
    net_pnl: 180,
    avg_net_return_pct: 1.2,
    best_net_pnl: 120,
    worst_net_pnl: -40,
    max_drawdown_pct: -2.5,
  },
  trades: [
    { entry_at: "2026-01-05T09:15:00+05:30", exit_at: "2026-02-02T09:15:00+05:30", exit_reason: "target", quantity: 1, net_pnl: 120, net_return_pct: 2.4 },
    { entry_at: "2026-02-10T09:15:00+05:30", exit_at: "2026-03-02T09:15:00+05:30", exit_reason: "stop_loss", quantity: 1, net_pnl: -40, net_return_pct: -0.8 },
    { entry_at: "2026-03-10T09:15:00+05:30", exit_at: "2026-04-06T09:15:00+05:30", exit_reason: "exit_rules_matched", quantity: 1, net_pnl: 100, net_return_pct: 2.0 },
  ],
  open_position: null,
  evidence: { basis: "Realized OHLCV history only.", is_forecast: false, is_recommendation: false },
  disclosures: ["Signals describe rule matches. They are not investment advice."],
};

function makeApiHandler() {
  return vi.fn(async (path: string) => {
    if (path.includes("/strategies/metadata")) return metadata;
    if (path.includes("/strategies/backtest")) return backtest;
    if (path.includes("/strategies/run")) {
      return { strategy: { name: "Test" }, coverage: { requested: 2, evaluated: 2, excluded: [] }, results: [], evidence: { basis: "Realized OHLCV history only." }, disclosures: [] };
    }
    if (path.startsWith("/api/v1/strategies")) return saved;
    throw new Error(`unexpected call: ${path}`);
  });
}

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: makeApiHandler() };
});

import StrategiesPage from "@/app/strategies/page";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <StrategiesPage />
    </QueryClientProvider>,
  );
}

describe("strategy builder page", () => {
  it("drives the builder from backend metadata and loads starters", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByRole("heading", { name: /strategies/i })).toBeInTheDocument());
    await waitFor(() => expect(screen.getByLabelText("Metric for condition 1")).toBeInTheDocument());
    const metric = screen.getByLabelText("Metric for condition 1") as HTMLSelectElement;
    expect([...metric.options].map((option) => option.textContent)).toEqual(expect.arrayContaining(["SMA 20", "RSI 14"]));
    expect(screen.getByRole("button", { name: /load: momentum/i })).toBeInTheDocument();
    expect(screen.getByText("Paper only")).toBeInTheDocument();
  });

  it("runs a backtest and renders the equity curve from closed trades", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByLabelText("Metric for condition 1")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /^backtest$/i }));
    await waitFor(() => expect(screen.getByText("Cumulative net P&L")).toBeInTheDocument());
    expect(screen.getByTestId("strategy-equity-chart")).toBeInTheDocument();
    expect(screen.getAllByText("66.67%").length).toBeGreaterThan(0);
    const body = screen.getAllByText(/Signals describe rule matches/i);
    expect(body.length).toBeGreaterThan(0);
  });

  it("keeps AND/OR group join selectable per group", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByLabelText("Join for entry-0")).toBeInTheDocument());
    const join = screen.getByLabelText("Join for entry-0") as HTMLSelectElement;
    expect(join.value).toBe("and");
    await userEvent.selectOptions(join, "or");
    expect(join.value).toBe("or");
  });
});

describe("strategy builder page when the flag is off", () => {
  it("explains that the builder is disabled instead of crashing", async () => {
    const { api } = await import("@/lib/api");
    const { ApiError } = await import("@/lib/api");
    vi.mocked(api).mockRejectedValue(new ApiError("Feature is disabled", 503, "feature_disabled", null, false));
    renderPage();
    await waitFor(() => expect(screen.getByText(/turned off by an operator feature flag/i)).toBeInTheDocument());
    expect(screen.queryByLabelText("Metric for condition 1")).not.toBeInTheDocument();
  });
});
