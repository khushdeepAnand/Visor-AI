import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MarginEstimateCard } from "@/components/MarginEstimateCard";
import type { MarginEstimate } from "@/lib/api";

const estimate: MarginEstimate = {
  total_margin: 12500,
  basis: "span_style_estimate",
  valuation_basis: "expiry_settlement_when_expired_else_mark_to_model",
  scenarios_evaluated: 63,
  short_premium_floor: 4000,
  sum_standalone: 17000,
  strategy_offset: 4500,
  worst_scenario: {
    underlying_move_pct: -15,
    volatility_change_pct: 50,
    portfolio_value: -2500,
    loss: 12500,
  },
  per_leg: [
    {
      index: 1,
      label: "sell call 100",
      side: "sell",
      type: "call",
      standalone_margin: 8000,
      derivation: "max(short_premium, single_leg_scan)",
    },
    {
      index: 2,
      label: "buy put 95",
      side: "buy",
      type: "put",
      standalone_margin: 9000,
      derivation: "capital_at_risk",
    },
  ],
  is_official_requirement: false,
  is_forecast: false,
  disclosures: ["Scan-based estimate, not a broker requirement."],
};

describe("MarginEstimateCard", () => {
  it("labels the figure as an estimate rather than a broker requirement", () => {
    render(<MarginEstimateCard margin={estimate} disclosure="Test disclosure" />);
    expect(screen.getByText(/Margin estimate \(not a broker requirement\)/i)).toBeInTheDocument();
    expect(screen.getByText("₹12,500")).toBeInTheDocument();
    expect(screen.getByText(/not available — broker risk files are not held/i)).toBeInTheDocument();
  });

  it("shows the floor, standalone sum, and strategy offset", () => {
    render(<MarginEstimateCard margin={estimate} disclosure="Test disclosure" />);
    expect(screen.getByText("Short premium floor")).toBeInTheDocument();
    expect(screen.getByText("₹4,000")).toBeInTheDocument();
    expect(screen.getByText("Standalone sum")).toBeInTheDocument();
    expect(screen.getByText("₹17,000")).toBeInTheDocument();
    expect(screen.getByText("Strategy offset")).toBeInTheDocument();
    expect(screen.getByText("₹4,500")).toBeInTheDocument();
  });

  it("reports the worst scenario that bound the estimate", () => {
    render(<MarginEstimateCard margin={estimate} disclosure="Test disclosure" />);
    expect(screen.getByText(/Underlying -15%, volatility \+50%/)).toBeInTheDocument();
    expect(screen.getByText(/over 63 scenarios/)).toBeInTheDocument();
  });

  it("renders a per-leg standalone margin table", () => {
    render(<MarginEstimateCard margin={estimate} disclosure="Test disclosure" />);
    const table = screen.getByRole("table");
    expect(within(table).getByText("sell call 100")).toBeInTheDocument();
    expect(within(table).getByText("buy put 95")).toBeInTheDocument();
    expect(within(table).getByText("₹8,000")).toBeInTheDocument();
    expect(within(table).getByText("₹9,000")).toBeInTheDocument();
  });

  it("shows the backend reason instead of a number when no estimate exists", () => {
    render(<MarginEstimateCard margin={{ state: "unavailable", reason: "margin_scan_requires_volatility" }} disclosure="Test disclosure" />);
    expect(screen.getByText("No margin estimate: margin_scan_requires_volatility")).toBeInTheDocument();
    expect(screen.queryByText("₹12,500")).not.toBeInTheDocument();
  });
});