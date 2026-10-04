import type { Meta, StoryObj } from "@storybook/react-vite";

import PayoffChart, { type PayoffResponse } from "@/components/PayoffChart";

const longStrangle: PayoffResponse = {
  underlying: "NIFTY",
  analysis_label: "Expiry payoff arithmetic on supplied legs and premiums",
  spot: 100,
  net_premium: 700,
  position: "debit",
  payoff_at_spot: -700,
  max_profit: { unbounded: true, value: null, at_price: null, note: "Profit increases without a modelled limit as the underlying rises." },
  max_loss: { unbounded: false, value: -700, at_price: 0, outside_plotted_range: true, note: "Reached only if the underlying falls to zero, far outside the plotted range." },
  risk_reward_ratio: null,
  breakevens: [81, 114],
  grid: { low: 80, high: 120, points: 121, span: 0.2 },
  curve: Array.from({ length: 121 }, (_, i) => {
    const price = 80 + (40 / 120) * i;
    const payoff = Math.max(0, price - 100) * 50 - 400 + Math.max(0, 95 - price) * 50 - 300;
    return { price: Number(price.toFixed(2)), payoff: Number(payoff.toFixed(2)) };
  }),
  tails: {
    upside_slope_per_point: 50,
    downside_slope_per_point: -50,
    profit_unbounded: true,
    loss_unbounded: false,
    downside_bounded_by_zero: true,
  },
  is_forecast: false,
  is_recommendation: false,
  margin: { state: "unavailable", note: "Exchange span/exposure margin requires broker risk files this product does not hold." },
  disclosures: ["Payoff is computed at expiry from the premiums and strikes you supplied."],
};

const meta: Meta<typeof PayoffChart> = {
  title: "Derivatives/PayoffChart",
  component: PayoffChart,
  args: { data: longStrangle },
};

export default meta;
type Story = StoryObj<typeof PayoffChart>;

export const ExpiryPayoff: Story = {};

export const EmptyCurve: Story = {
  args: { data: { ...longStrangle, curve: [] } },
};