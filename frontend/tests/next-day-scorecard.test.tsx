import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { NextDayScorecard, type NextDayEvidence } from "@/components/PublicScorecard";

const empty: NextDayEvidence = { total_forecasts: 0, evidence_tier: "insufficient", coverage: null, target_coverage: null, winkler_score: null, mase: null, mean_pinball_loss: null, data_tiers: {}, specialist_status: "research_only_pending_next_day_promotion", basis: "Automatically settled daily horizon=1 forecasts only." };

describe("next-day scorecard", () => {
  it("shows missing evidence without inventing accuracy or promotion", () => {
    render(<NextDayScorecard evidence={empty} />);
    expect(screen.getByRole("region", { name: "Next-day forecast evidence" })).toHaveTextContent("0 settled forecasts");
    expect(screen.getByText(/Daily horizon=1 only/)).toHaveTextContent("insufficient evidence");
    expect(screen.getByText(/Specialist:/)).toHaveTextContent("research only pending next day promotion");
    expect(screen.getByText("N/A / N/A")).toBeInTheDocument();
  });
  it("renders next-day measured calibration and sample tiers", () => {
    render(<NextDayScorecard evidence={{ ...empty, total_forecasts: 120, evidence_tier: "substantial", coverage: .81, target_coverage: .8, winkler_score: 25.25, mase: .9, mean_pinball_loss: 1.2, data_tiers: { T3: 120 } }} />);
    expect(screen.getByText("81.0% / 80.0%")).toBeInTheDocument();
    expect(screen.getByText("0.9000")).toBeInTheDocument();
    expect(screen.getByText(/Data tiers:/)).toHaveTextContent("T3: 120");
  });
});
