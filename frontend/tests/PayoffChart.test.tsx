import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PayoffChart, type PayoffResponse } from "@/components/PayoffChart";

const bullSpread: PayoffResponse = {
  curve: [
    { price: 80, payoff: -400 },
    { price: 100, payoff: -400 },
    { price: 104, payoff: 0 },
    { price: 110, payoff: 600 },
    { price: 120, payoff: 600 },
  ],
  spot: 100,
  net_premium: 400,
  breakevens: [104],
  max_profit: { unbounded: false, value: 600, at_price: 110 },
  max_loss: { unbounded: false, value: -400, at_price: 100 },
  margin: {
    state: "unavailable",
    reason: "broker_risk_parameters_not_held",
    note: "Margin is not shown because broker risk parameters are not held by this app.",
  },
  disclosures: ["Payoff is expiry arithmetic on your inputs. It is not a forecast."],
  is_forecast: false,
  is_recommendation: false,
};

describe("PayoffChart", () => {
  it("draws a labelled curve with the backend's extremes", () => {
    render(<PayoffChart data={bullSpread} />);
    expect(screen.getByRole("img", { name: /Payoff at expiry/i })).toBeInTheDocument();
    expect(screen.getByText("Max profit")).toBeInTheDocument();
    expect(screen.getByText(/600/)).toBeInTheDocument();
  });

  it("states the net premium direction rather than leaving it implied", () => {
    render(<PayoffChart data={bullSpread} />);
    expect(screen.getByText(/Net premium debit/)).toBeInTheDocument();
  });

  it("says margin is unavailable instead of estimating it", () => {
    render(<PayoffChart data={bullSpread} />);
    expect(screen.getByText(/broker risk parameters are not held/)).toBeInTheDocument();
  });

  it("repeats the backend disclosure", () => {
    render(<PayoffChart data={bullSpread} />);
    expect(screen.getByText(/not a forecast/)).toBeInTheDocument();
  });

  it("reports a credit structure as a credit", () => {
    render(<PayoffChart data={{ ...bullSpread, net_premium: -250 }} />);
    expect(screen.getByText(/Net premium credit/)).toBeInTheDocument();
  });

  it("labels an unbounded profit without inventing a number", () => {
    render(<PayoffChart data={{ ...bullSpread, max_profit: { unbounded: true, value: null, at_price: null } }} />);
    expect(screen.getByText("Unbounded")).toBeInTheDocument();
  });

  it("falls back to a status message when no curve is returned", () => {
    render(<PayoffChart data={{ ...bullSpread, curve: [] }} />);
    expect(screen.getByRole("status")).toHaveTextContent(/No payoff curve/i);
  });
});
