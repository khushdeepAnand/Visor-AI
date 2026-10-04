import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import StrategyLabPage from "@/app/strategy-lab/page";
import { ApiError, api } from "@/lib/api";

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

const templates = {
  templates: [
    { name: "iron_condor", label: "Iron Condor", direction: "bullish_income", legs: 4, description: "Sell a put spread and a call spread.", params: { short_offset_pct: "Percent above spot for the short strikes", wing_width_pct: "Width of each wing" } },
    { name: "straddle", label: "Straddle", direction: "long_volatility", legs: 2, description: "Buy a call and a put at one strike.", params: {} },
  ],
  max_legs: 4,
  disclosures: ["Templates structure your inputs; they are not recommendations and carry no probability."],
};

const buildOk = {
  template: "iron_condor",
  label: "Iron Condor",
  legs: [
    { index: 1, type: "put", side: "sell", strike: 95, premium: 1.5, quantity: 1, lot_size: 50, contracts: 50, label: "sell put 95", premium_basis: "model" },
    { index: 2, type: "put", side: "buy", strike: 90, premium: 0.6, quantity: 1, lot_size: 50, contracts: 50, label: "buy put 90", premium_basis: "model" },
    { index: 3, type: "call", side: "sell", strike: 105, premium: 1.4, quantity: 1, lot_size: 50, contracts: 50, label: "sell call 105", premium_basis: "model" },
    { index: 4, type: "call", side: "buy", strike: 110, premium: 0.55, quantity: 1, lot_size: 50, contracts: 50, label: "buy call 110", premium_basis: "model" },
  ],
  premium_basis: ["model", "model", "model", "model"],
  metadata: { spot: 100, strike_step: 1, quantity: 1, lot_size: 50, volatility: 0.18, days_to_expiry: 30, all_model_priced: true, all_user_supplied: false },
  payoff: {
    analysis_label: "Expiry payoff arithmetic on supplied legs and premiums",
    spot: 100,
    net_premium: -37.5,
    position: "credit",
    payoff_at_spot: -37.5,
    max_profit: { unbounded: false, value: 37.5, at_price: 100, outside_plotted_range: false, note: null },
    max_loss: { unbounded: false, value: -375, at_price: 90, outside_plotted_range: false, note: null },
    risk_reward_ratio: 0.1,
    breakevens: [89.25, 110.75],
    grid: { low: 80, high: 120, points: 3, span: 0.2 },
    curve: [{ price: 80, payoff: -162.5 }, { price: 100, payoff: -37.5 }, { price: 120, payoff: -162.5 }],
    tails: {},
    valuation: { state: "available", model: "black_scholes", is_predictive: false },
    is_forecast: false,
    is_recommendation: false,
  },
  margin: {
    total_margin: 412.5,
    basis: "span_style_estimate",
    valuation_basis: "expiry_settlement_when_expired_else_mark_to_model",
    base_portfolio_value: -37.5,
    worst_scenario: { underlying_move_pct: -15, volatility_change_pct: 50, portfolio_value: 412.5, loss: 450, valuation: "mark_to_model" },
    scenarios_evaluated: 36,
    underlying_shocks_pct: [0, -3, 3],
    volatility_shocks_rel: [0, 0.25],
    short_premium_floor: 145,
    per_leg: [
      { index: 1, label: "sell put 95", side: "sell", type: "put", standalone_margin: 75, derivation: "max(short_premium, single_leg_scan)" },
      { index: 2, label: "buy put 90", side: "buy", type: "put", standalone_margin: 30, derivation: "capital_at_risk" },
      { index: 3, label: "sell call 105", side: "sell", type: "call", standalone_margin: 70, derivation: "max(short_premium, single_leg_scan)" },
      { index: 4, label: "buy call 110", side: "buy", type: "call", standalone_margin: 27.5, derivation: "capital_at_risk" },
    ],
    sum_standalone: 202.5,
    strategy_offset: 0,
    is_official_requirement: false,
    is_forecast: false,
    disclosures: ["SPAN-style estimate from a scenario grid; not the exchange's official SPAN requirement, which needs broker risk files this product does not hold."],
  },
  margin_unavailable_reason: null,
  disclosures: [
    "Payoff is computed at expiry from the premiums and strikes you supplied. It is not a forecast and carries no probability.",
    "Template output structures your inputs only; it is not a recommendation.",
  ],
};

const pnlOk = {
  legs: [
    { index: 1, label: "sell put 95", type: "put", side: "sell", strike: 95, entry_premium: 1.5, current_mark: 1.2, mark_basis: "user_mark", mark_unavailable_reason: null, pnl: 1500, contracts: 50 },
    { index: 2, label: "buy put 90", type: "put", side: "buy", strike: 90, entry_premium: 0.6, current_mark: 0.4, mark_basis: "model_mark", pnl: -1000, contracts: 50 },
    { index: 3, label: "sell call 105", type: "call", side: "sell", strike: 105, entry_premium: 1.4, current_mark: null, mark_basis: "unavailable", mark_unavailable_reason: "supply mark_price or volatility_now to value this leg", pnl: null, contracts: 50 },
    { index: 4, label: "buy call 110", type: "call", side: "buy", strike: 110, entry_premium: 0.55, current_mark: 0.5, mark_basis: "model_mark", pnl: -250, contracts: 50 },
  ],
  total_pnl: 250,
  total_pnl_basis: "partial",
  spot_now: 98,
  volatility_now: 0.21,
  days_to_expiry_now: 14,
  is_forecast: false,
  is_recommendation: false,
  disclaimer: "P&L on supplied inputs. User marks are your observations; model marks are theoretical values under the supplied volatility, not quotes. Not a forecast.",
};

function defaultHandler(path: string) {
  if (path === "/api/v1/derivatives/strategies/templates") return templates;
  if (path === "/api/v1/derivatives/strategies/build") return buildOk;
  if (path === "/api/v1/derivatives/strategies/pnl") return pnlOk;
  throw new Error(`unexpected call: ${path}`);
}

beforeEach(() => {
  vi.mocked(api).mockImplementation(async (path: string) => defaultHandler(path));
});

afterEach(() => {
  vi.mocked(api).mockReset();
});

async function fillSpot(value = "100") {
  await userEvent.clear(screen.getByLabelText("Underlying spot"));
  await userEvent.type(screen.getByLabelText("Underlying spot"), value);
}

async function buildStructure() {
  await fillSpot();
  await userEvent.click(screen.getByRole("button", { name: /build structure/i }));
  await screen.findByRole("img", { name: /payoff at expiry/i });
}

describe("Strategy Lab", () => {
  it("loads the template catalog and preselects the first structure", async () => {
    render(<StrategyLabPage />);
    const select = await screen.findByLabelText("Strategy template");
    await waitFor(() => expect((select as HTMLSelectElement).value).toBe("iron_condor"));
    expect(screen.getByRole("option", { name: "Iron Condor" })).toBeInTheDocument();
    expect(screen.getByText("Sell a put spread and a call spread.")).toBeInTheDocument();
  });

  it("survives a malformed catalog response without crashing", async () => {
    vi.mocked(api).mockResolvedValue({} as never);
    render(<StrategyLabPage />);
    await waitFor(() => expect(vi.mocked(api)).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: /build structure/i })).toBeDisabled();
  });

  it("renders template parameter inputs only for the selected template", async () => {
    render(<StrategyLabPage />);
    await screen.findByLabelText("Strategy template");
    await waitFor(() => expect(screen.getByLabelText(/Template input wing width/)).toBeInTheDocument());

    await userEvent.selectOptions(screen.getByLabelText("Strategy template"), "straddle");
    await waitFor(() =>
      expect(screen.queryByLabelText(/Template input wing width/)).not.toBeInTheDocument()
    );
  });

  it("posts the build payload with volatility as a decimal and filled params", async () => {
    render(<StrategyLabPage />);
    await screen.findByLabelText("Strategy template");
    await waitFor(() => expect(screen.getByLabelText(/Template input wing width/)).toBeInTheDocument());

    await userEvent.clear(screen.getByLabelText("Volatility percent"));
    await userEvent.type(screen.getByLabelText("Volatility percent"), "18");
    await userEvent.clear(screen.getByLabelText(/Template input wing width/));
    await userEvent.type(screen.getByLabelText(/Template input wing width/), "5");

    await buildStructure();

    const call = vi.mocked(api).mock.calls.find(entry => entry[0].endsWith("/strategies/build"));
    expect(call).toBeDefined();
    const body = JSON.parse(String((call?.[1] as RequestInit).body));
    expect(body).toMatchObject({
      template: "iron_condor",
      spot: 100,
      volatility: 0.18,
      days_to_expiry: 30,
      risk_free_rate: 0.065,
      params: { wing_width_pct: 5 },
    });
    // Blank template inputs must not be sent so the backend default applies.
    expect(body.params.short_offset_pct).toBeUndefined();
  });

  it("shows the payoff curve, breakevens and legs after a build", async () => {
    render(<StrategyLabPage />);
    await screen.findByLabelText("Strategy template");
    await buildStructure();

    expect(screen.getByRole("img", { name: /payoff at expiry/i })).toBeInTheDocument();
    // Breakevens are surfaced on the chart and again in the summary metric.
    expect(screen.getAllByText(/89\.25/).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("cell", { name: "sell put 95" }).length).toBeGreaterThan(0);
    expect(screen.getAllByText("model value").length).toBeGreaterThan(0);
    expect(screen.queryByText(/undefined/)).not.toBeInTheDocument();
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument();
  });

  it("renders the SPAN-style margin estimate with its non-official disclosure", async () => {
    render(<StrategyLabPage />);
    await screen.findByLabelText("Strategy template");
    await buildStructure();

    expect(
      screen.getByRole("heading", { name: /margin estimate \(not a broker requirement\)/i })
    ).toBeInTheDocument();
    expect(screen.getByText("₹412.5")).toBeInTheDocument();
    expect(screen.getByText(/Basis: span_style_estimate over 36 scenarios/)).toBeInTheDocument();
    expect(
      screen.getByText(/not available — broker risk files are not held/)
    ).toBeInTheDocument();
    expect(screen.getByText("Worst scenario")).toBeInTheDocument();
    expect(screen.getByText(/Underlying -15%, volatility \+50% → loss ₹450/)).toBeInTheDocument();
  });

  it("explains why margin is unavailable when no margin block is returned", async () => {
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path.endsWith("/templates")) return templates;
      if (path.endsWith("/build")) {
        return { ...buildOk, margin: null, margin_unavailable_reason: "margin_scan_requires_volatility" };
      }
      return pnlOk;
    });
    render(<StrategyLabPage />);
    await screen.findByLabelText("Strategy template");
    await buildStructure();

    expect(screen.getByText(/No margin estimate: margin_scan_requires_volatility/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /not a broker requirement/i })).not.toBeInTheDocument();
  });

  it("surfaces a build failure as an alert and renders no legs", async () => {
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path.endsWith("/templates")) return templates;
      throw new ApiError("template is not supported", 400, "invalid_template", "abc-123", false);
    });
    render(<StrategyLabPage />);
    await screen.findByLabelText("Strategy template");
    await fillSpot();
    await userEvent.click(screen.getByRole("button", { name: /build structure/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("template is not supported");
    expect(screen.queryByRole("table", { name: /legs of the built structure/i })).not.toBeInTheDocument();
  });

  it("valuess the built legs and labels each mark basis", async () => {
    render(<StrategyLabPage />);
    await screen.findByLabelText("Strategy template");
    await buildStructure();

    await userEvent.clear(screen.getByLabelText("Spot now"));
    await userEvent.type(screen.getByLabelText("Spot now"), "98");
    await userEvent.clear(screen.getByLabelText("Volatility now percent"));
    await userEvent.type(screen.getByLabelText("Volatility now percent"), "21");
    await userEvent.clear(screen.getByLabelText("Mark for sell put 95"));
    await userEvent.type(screen.getByLabelText("Mark for sell put 95"), "1.2");
    await userEvent.click(screen.getByRole("button", { name: /value position/i }));

    await waitFor(() => expect(screen.getByText("Total P&L")).toBeInTheDocument());

    const call = vi.mocked(api).mock.calls.find(entry => entry[0].endsWith("/strategies/pnl"));
    const body = JSON.parse(String((call?.[1] as RequestInit).body));
    expect(body.spot_now).toBe(98);
    expect(body.volatility_now).toBe(0.21);
    expect(body.legs).toHaveLength(4);
    expect(body.legs[0]).toMatchObject({ type: "put", side: "sell", strike: 95, premium: 1.5, lot_size: 50, mark_price: 1.2 });
    // Legs without a user mark must be left unset so the endpoint reports basis honestly.
    expect(body.legs[2].mark_price).toBeUndefined();

    expect(screen.getByText("your mark")).toBeInTheDocument();
    expect(screen.getAllByText("model value").length).toBeGreaterThan(0);
    expect(screen.getByText(/Partial — some legs could not be valued/)).toBeInTheDocument();
    expect(screen.getByText(/supply mark_price or volatility_now to value this leg/)).toBeInTheDocument();
    expect(screen.getByText(/Not a forecast\.$/)).toBeInTheDocument();
  });

  it("hides the revaluation panel until a structure exists", async () => {
    render(<StrategyLabPage />);
    await screen.findByLabelText("Strategy template");
    expect(screen.queryByRole("heading", { name: /revalue this structure/i })).not.toBeInTheDocument();
  });
});