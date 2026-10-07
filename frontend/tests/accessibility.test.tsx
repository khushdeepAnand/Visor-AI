import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe, toHaveNoViolations } from "jest-axe";
import { describe, expect, it, vi } from "vitest";

import { MarginEstimateCard } from "@/components/MarginEstimateCard";
import { PayoffChart } from "@/components/PayoffChart";
import StrategyLabPage from "@/app/strategy-lab/page";
import { api } from "@/lib/api";
import type { MarginEstimate, PayoffResponse, PayoffMaxProfit, PayoffMaxLoss } from "@/lib/api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/strategy-lab",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/components/TerminalShell", () => ({
  TerminalShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
  default: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: vi.fn() };
});

const axeCatalog = {
  templates: [
    { name: "iron_condor", label: "Iron Condor", direction: "bullish_income", legs: 4, description: "Sell a put spread and a call spread.", params: { wing_width_pct: "Width of each wing" } },
  ],
  max_legs: 4,
  disclosures: [],
};

const axeBuild = {
  template: "iron_condor",
  label: "Iron Condor",
  legs: [
    { index: 1, type: "put", side: "sell", strike: 95, premium: 1.5, quantity: 1, lot_size: 50, contracts: 50, label: "sell put 95", premium_basis: "model" },
    { index: 2, type: "put", side: "buy", strike: 90, premium: 0.6, quantity: 1, lot_size: 50, contracts: 50, label: "buy put 90", premium_basis: "model" },
  ],
  metadata: { spot: 100, days_to_expiry: 30, all_model_priced: true, all_user_supplied: false },
  payoff: {
    analysis_label: "Expiry payoff arithmetic on supplied legs and premiums",
    spot: 100,
    net_premium: -45,
    position: "credit",
    payoff_at_spot: -45,
    max_profit: { unbounded: false, value: 45, at_price: 100, outside_plotted_range: false, note: null },
    max_loss: { unbounded: false, value: -305, at_price: 90, outside_plotted_range: false, note: null },
    breakevens: [89.25, 110.75],
    curve: Array.from({ length: 41 }, (_, i) => ({ price: 80 + i, payoff: Math.max(-305, Math.min(45, (80 + i - 100) * 5 - 45)) })),
    tails: {},
    is_forecast: false,
    is_recommendation: false,
  },
  margin: {
    total_margin: 12500,
    basis: "span_style_estimate",
    scenarios_evaluated: 36,
    short_premium_floor: 4000,
    sum_standalone: 17000,
    strategy_offset: 4500,
    worst_scenario: { underlying_move_pct: -15, volatility_change_pct: 50, loss: 12500 },
    per_leg: [
      { index: 1, label: "sell put 95", side: "sell", type: "put", standalone_margin: 8000, derivation: "max(short_premium, single_leg_scan)" },
    ],
    is_official_requirement: false,
    disclosures: ["Scan-based estimate, not a broker requirement."],
  },
  margin_unavailable_reason: null,
  disclosures: ["Payoff is computed at expiry from the premiums and strikes you supplied."],
};

const axePnl = {
  legs: [
    { index: 1, label: "sell put 95", type: "put", side: "sell", strike: 95, entry_premium: 1.5, current_mark: 1.2, mark_basis: "user_mark", pnl: 1500, contracts: 50 },
    { index: 2, label: "buy put 90", type: "put", side: "buy", strike: 90, entry_premium: 0.6, current_mark: null, mark_basis: "unavailable", mark_unavailable_reason: "supply mark_price or volatility_now to value this leg", pnl: null, contracts: 50 },
  ],
  total_pnl: 1500,
  total_pnl_basis: "partial",
  spot_now: 98,
  volatility_now: null,
  days_to_expiry_now: 14,
  disclaimer: "P&L on supplied inputs. Not a forecast.",
};

vi.mocked(api).mockImplementation(async (path: string) => {
  if (path.endsWith("/templates")) return axeCatalog;
  if (path.endsWith("/build")) return axeBuild;
  if (path.endsWith("/pnl")) return axePnl;
  throw new Error(`unexpected call: ${path}`);
});

expect.extend(toHaveNoViolations);

const estimate: MarginEstimate = {
  total_margin: 12500,
  basis: "span_style_estimate",
  scenarios_evaluated: 63,
  short_premium_floor: 4000,
  sum_standalone: 17000,
  strategy_offset: 4500,
  worst_scenario: { underlying_move_pct: -15, volatility_change_pct: 50, loss: 12500 },
  per_leg: [
    { index: 1, label: "sell call 100", side: "sell", type: "call", standalone_margin: 8000, derivation: "max(short_premium, single_leg_scan)" },
    { index: 2, label: "buy put 95", side: "buy", type: "put", standalone_margin: 9000, derivation: "capital_at_risk" },
  ],
  is_official_requirement: false,
  is_forecast: false,
  disclosures: ["Scan-based estimate, not a broker requirement."],
};

const payoff: PayoffResponse = {
  underlying: "NIFTY 50",
  analysis_label: "Expiry payoff arithmetic on supplied legs and premiums",
  legs: [],
  spot: 100,
  net_premium: 700,
  position: "debit",
  payoff_at_spot: -700,
  max_profit: { unbounded: true, value: null, at_price: null, outside_plotted_range: false, note: "Profit increases without a modelled limit as the underlying rises." },
  max_loss: { unbounded: false, value: -700, at_price: 0, outside_plotted_range: false, note: "Reached only if the underlying falls to zero." },
  breakevens: [81, 114],
  curve: Array.from({ length: 41 }, (_, i) => {
    const price = 80 + i;
    return { price, payoff: Math.max(0, price - 100) * 50 - 700 };
  }),
  margin: { state: "unavailable", note: "Broker risk files are not held." },
  disclosures: ["Payoff is computed at expiry from the premiums and strikes you supplied."],
  costs_applied: 0,
  grid: { low: 80, high: 120, points: 41, span: 0.2 },
  tails: { upside_slope_per_point: 0, downside_slope_per_point: 0, profit_unbounded: true, loss_unbounded: false, downside_bounded_by_zero: true, reference_spot: 100 },
  valuation: { state: "not_requested" },
  is_forecast: false,
  is_recommendation: false,
  risk_reward_ratio: null,
};

describe("accessibility", () => {
  it("MarginEstimateCard has no axe violations when an estimate is present", async () => {
    const { container } = render(<MarginEstimateCard margin={estimate} disclosure="Test disclosure" />);
    expect(await axe(container)).toHaveNoViolations();
  });

  it("MarginEstimateCard has no axe violations in the unavailable state", async () => {
    const { container } = render(
      <MarginEstimateCard margin={{ state: "unavailable", reason: "margin_scan_requires_volatility" }} disclosure="Test disclosure" />,
    );
    expect(await axe(container)).toHaveNoViolations();
  });

  it("PayoffChart has no axe violations", async () => {
    const { container } = render(<PayoffChart data={payoff} />);
    expect(await axe(container)).toHaveNoViolations();
  });

  it("Strategy Lab has no axe violations in the rendered-result state", async () => {
    const { container } = render(<StrategyLabPage />);
    const catalog = await screen.findByLabelText("Strategy template");
    await waitFor(() => expect((catalog as HTMLSelectElement).value).toBe("iron_condor"));

    await userEvent.clear(screen.getByLabelText("Underlying spot"));
    await userEvent.type(screen.getByLabelText("Underlying spot"), "100");
    await userEvent.click(screen.getByRole("button", { name: /build structure/i }));
    await screen.findByRole("img", { name: /payoff at expiry/i });

    await userEvent.clear(screen.getByLabelText("Spot now"));
    await userEvent.type(screen.getByLabelText("Spot now"), "98");
    await userEvent.click(screen.getByRole("button", { name: /value position/i }));
    await screen.findByText("Total P&L");

    expect(await axe(container)).toHaveNoViolations();
  }, 15000);
});