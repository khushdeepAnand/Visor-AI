import type { Meta, StoryObj } from "@storybook/react-vite";
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
    { index: 1, label: "sell call 100", side: "sell", type: "call", standalone_margin: 8000, derivation: "max(short_premium, single_leg_scan)" },
    { index: 2, label: "buy put 95", side: "buy", type: "put", standalone_margin: 9000, derivation: "capital_at_risk" },
  ],
  is_official_requirement: false,
  is_forecast: false,
  disclosures: ["Scan-based estimate, not a broker requirement."],
};

const meta: Meta<typeof MarginEstimateCard> = {
  title: "Derivatives/MarginEstimateCard",
  component: MarginEstimateCard,
  args: { margin: estimate, disclosure: "Test disclosure" },
};

export default meta;
type Story = StoryObj<typeof MarginEstimateCard>;

export const WithEstimate: Story = {};

export const Unavailable: Story = {
  args: { margin: { state: "unavailable" as const, reason: "margin_scan_requires_volatility" }, disclosure: "Test disclosure" },
};