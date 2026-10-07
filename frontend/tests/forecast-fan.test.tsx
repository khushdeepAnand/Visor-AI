import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ForecastFan } from "@/components/ForecastFan";
import { axe } from "jest-axe";

describe("forecast fan", () => {
  it("exposes actual bounds and labels visual interpolation without invented coverage", () => {
    render(<ForecastFan current={100} points={[{ sessions: 1, low: 90, median: 100, high: 110, nominal: .8 }, { sessions: 3, low: 80, median: 101, high: 120, nominal: .8 }]} />);
    expect(screen.getByRole("img", { name: /forecast uncertainty/i })).toBeInTheDocument();
    expect(screen.getByText(/connecting lines are visual guides/i)).toBeInTheDocument();
    expect(screen.getByText(/90.00.*110.00/)).toBeInTheDocument();
    expect(screen.getByText(/80.00.*120.00/)).toBeInTheDocument();
  });
  it("never draws an invalid or inverted numerical interval", () => {
    render(<ForecastFan current={100} points={[{ sessions: 1, low: 110, median: 100, high: 90, nominal: .8 }]} />);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByText(/no verified horizon bounds/i)).toBeInTheDocument();
  });
  it("passes the component axe audit", async () => {
    const { container } = render(<ForecastFan current={100} points={[{ sessions: 1, low: 90, median: 100, high: 110, nominal: .8 }]} />);
    expect((await axe(container)).violations).toEqual([]);
  });
});
