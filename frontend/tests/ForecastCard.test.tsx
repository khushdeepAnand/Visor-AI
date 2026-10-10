import { render, screen } from "@testing-library/react";
import { ForecastCard } from "@/components/ForecastCard";

describe("ForecastCard", () => {
  it("renders a public research range without model internals", () => {
    render(<ForecastCard data={{
      symbol: "RELIANCE",
      title: "Research range",
      model_label: "calibrated interval ensemble",
      research_range: { low: 2410, median_reference: 2440, high: 2470, confidence_level: 0.8 },
      reference_price: 2435,
      confidence: { level: "moderate", summary: "The range mostly held on unseen history." },
      uncertainty: { range_width: 60, range_width_pct: 2.46, band: "moderate", summary: "The range is moderate." },
      scenarios: [
        { label: "Bear", low: 2410, high: 2435, description: "Lower scenario." },
        { label: "Base", low: 2435, high: 2440, description: "Central scenario." },
        { label: "Bull", low: 2440, high: 2470, description: "Upper scenario." },
      ],
      disclaimer: "Research support only, not financial advice.",
    }} />);
    expect(screen.getByText("80% range")).toBeInTheDocument();
    expect(screen.getByText("Research range")).toBeInTheDocument();
    expect(screen.getByText("Trust Stack")).toBeInTheDocument();
    expect(screen.getByText("Model supported")).toBeInTheDocument();
    expect(screen.queryByText(/conformal|quantile|gradient boosting/i)).not.toBeInTheDocument();
    expect(screen.getAllByText(/₹2,410/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/₹2,470/).length).toBeGreaterThan(0);
  });

  it("uses explicit non-color cues for low-evidence and abstained results", () => {
    const base = {
      symbol: "INFY", title: "Research range", model_label: "baseline reference",
      reference_price: 1500, confidence: { level: "low" as const, summary: "Evidence is limited." },
      uncertainty: { range_width: 0, range_width_pct: 0, band: "unavailable", summary: "No interval released." },
      scenarios: [], disclaimer: "Research support only.",
    };
    const view = render(<ForecastCard data={{ ...base, support_state: "low_evidence", research_range: { low: 1450, median_reference: 1500, high: 1550, confidence_level: .8 } }} />);
    expect(screen.getByText("Low evidence")).toBeInTheDocument();
    view.rerender(<ForecastCard data={{ ...base, support_state: "abstained", abstained: true, abstention_reason: "History did not meet the threshold." }} />);
    expect(screen.getByText("Abstained")).toBeInTheDocument();
    expect(screen.getByText(/No numerical corridor released/)).toBeInTheDocument();
  });

  it("shows trust badges: low-history, data freshness, retrained, and option-implied move", () => {
    render(<ForecastCard
      retrainedAt="2026-05-11T08:30:00Z"
      data={{
        symbol: "BANKNIFTY",
        title: "Research range",
        model_label: "calibrated interval ensemble",
        research_range: { low: 48000, median_reference: 48400, high: 48800, confidence_level: 0.8 },
        reference_price: 48300,
        confidence: { level: "moderate", summary: "The range mostly held on unseen history." },
        uncertainty: { range_width: 800, range_width_pct: 1.66, band: "moderate", summary: "The range is moderate." },
        scenarios: [],
        low_data: true,
        low_data_branch: "ridge_ets_fallback",
        expected_move: {
          available: true,
          expiry: "2026-06-25",
          days_to_expiry: 13,
          source: "NSE F&O",
          spot_price: 48300,
          atm_iv: 0.12,
          expected_moves: [
            { sigma: 1, low: 47800, high: 48800, move_pct: 1.03 },
            { sigma: 2, low: 47300, high: 49300, move_pct: 2.07 },
          ],
          model_crossover: {
            flag: "aligned",
            message: "The model range sits inside the options market's implied band.",
            model_range_width: 800,
            implied_1sigma_width: 1000,
            implied_2sigma_width: 2000,
            model_vs_1sigma_ratio: 0.8,
          },
          basis: "atm_implied_volatility",
          disclosure: "IV-derived move is a market expectation, not a model forecast.",
        },
        provenance: { source: "Upstox", as_of: "2026-05-11T10:45:00+05:30", is_live: true, market_state: "open" },
        disclaimer: "Research support only, not financial advice.",
      }} />);
    expect(screen.getByText("Low history")).toBeInTheDocument();
    expect(screen.getByText(/As of \d{1,2}:\d{2}( [AP]M)?/i)).toBeInTheDocument();
    expect(screen.getByTitle(/Model last \(re\)trained/)).toBeInTheDocument();
    expect(screen.getByText("Option-implied expected move")).toBeInTheDocument();
    expect(screen.getByText(/ATM IV/)).toBeInTheDocument();
    expect(screen.getAllByText(/±1σ|±2σ/).length).toBeGreaterThan(0);
    expect(screen.getByText(/options market's implied band/)).toBeInTheDocument();
  });

  it("shows a plain unavailable message when no option chain existed", () => {
    render(<ForecastCard data={{
      symbol: "TCS",
      title: "Research range",
      model_label: "calibrated interval ensemble",
      reference_price: 3500,
      confidence: { level: "moderate", summary: "The range mostly held on unseen history." },
      uncertainty: { range_width: 0, range_width_pct: 0, band: "unavailable", summary: "No interval released." },
      scenarios: [],
      expected_move: { available: false, reason: "chain_unavailable", message: "No live option chain was available when this range was built." },
      disclaimer: "Research support only.",
    }} />);
    expect(screen.getByText(/No live option chain was available/)).toBeInTheDocument();
    expect(screen.queryByText("Option-implied expected move")).not.toBeInTheDocument();
  });

  it("renders the model assessment block: probability, regime, agreement, confidence, explanation", () => {
    render(<ForecastCard data={{
      symbol: "RELIANCE",
      title: "Research range",
      model_label: "calibrated interval ensemble",
      research_range: { low: 2390, median_reference: 2440, high: 2490, confidence_level: 0.8 },
      reference_price: 2435,
      confidence: { level: "moderate", summary: "The range mostly held on unseen history." },
      uncertainty: { range_width: 100, range_width_pct: 4.1, band: "moderate", summary: "The range is moderate." },
      scenarios: [],
      disclaimer: "Research support only, not financial advice.",
      assessment: {
        probability: { up: 0.61, down: 0.39, basis: "6 member predictions, calibrated on the untouched test fold." },
        expected_return_pct: 0.21,
        expected_volatility_pct: 1.91,
        market_regime: { available: true, label: "Mild bullish trend / Normal volatility", trend: "mild_bullish", volatility: "normal", volatility_percentile: 0.5 },
        model_agreement: { available: true, score: 0.87, level: "high", dispersion_pct: 0.4, member_count: 6 },
        confidence_score: { score: 74, level: "high", components: { evidence: 40, calibration: 10, skill: 12, agreement: 9, data_quality: 8 }, basis: "weighted sum of measured components." },
        data_quality: { score: 0.98, level: "strong", notes: [], basis: "input defects only." },
        explanation: { available: true, reasons: ["Price holds 1.2% above its 20-day average.", "Regime: Mild bullish trend / Normal volatility."], summary: "Why the model leans this way." },
      },
    }} />);
    expect(screen.getByText("Model assessment")).toBeInTheDocument();
    expect(screen.getAllByText((_content, el) => el?.textContent?.replace(/\s+/g, " ").includes("Evidence diagnostic 74/100") ?? false).length).toBeGreaterThan(0);
    expect(screen.getByText(/not a calibrated probability/i)).toBeInTheDocument();
    expect(screen.getByText(/Upside 61%/)).toBeInTheDocument();
    expect(screen.getByText(/Downside 39%/)).toBeInTheDocument();
    expect(screen.getByText("Mild bullish trend / Normal volatility")).toBeInTheDocument();
    expect(screen.getByText(/Model agreement/)).toBeInTheDocument();
    expect(screen.getByText("Why this range?")).toBeInTheDocument();
    expect(screen.getByText(/20-day average/)).toBeInTheDocument();
  });

  it("does not render the assessment block when the payload carries none", () => {
    render(<ForecastCard data={{
      symbol: "TCS",
      title: "Research range",
      model_label: "calibrated interval ensemble",
      reference_price: 3500,
      confidence: { level: "moderate", summary: "The range mostly held on unseen history." },
      uncertainty: { range_width: 0, range_width_pct: 0, band: "unavailable", summary: "No interval released." },
      scenarios: [],
      disclaimer: "Research support only.",
    }} />);
    expect(screen.queryByText("Model assessment")).not.toBeInTheDocument();
  });

  it("surfaces cross-horizon disagreement as a confidence signal", () => {
    render(<ForecastCard data={{
      symbol: "TCS",
      title: "Research range",
      model_label: "calibrated interval ensemble",
      research_range: { low: 3450, median_reference: 3505, high: 3560, confidence_level: 0.8 },
      reference_price: 3500,
      confidence: { level: "moderate", summary: "Evidence is mixed." },
      uncertainty: { range_width: 110, range_width_pct: 3.14, band: "moderate", summary: "The range is moderate." },
      scenarios: [],
      horizons: [
        { sessions: 1, label: "next session", forecast: { low: 3450, median_reference: 3505, high: 3560, confidence_level: 0.8 } },
        { sessions: 5, label: "5 sessions ahead", forecast: { low: 3420, median_reference: 3470, high: 3520, confidence_level: 0.8 } },
      ],
      horizon_consistency: {
        available: true,
        score: 45,
        level: "low",
        signal: "weak",
        summary: "Cross-horizon caution: released horizons point in materially different directions.",
        flags: ["direction_conflict"],
        basis: "Direct horizon comparison.",
      },
      disclaimer: "Research support only.",
    }} />);

    expect(screen.getByText("Cross-horizon consistency")).toBeInTheDocument();
    expect(screen.getByText(/45\/100/)).toBeInTheDocument();
    expect(screen.getByText(/materially different directions/)).toBeInTheDocument();
    expect(screen.getByText(/no corridor is adjusted or hidden/i)).toBeInTheDocument();
  });
});
