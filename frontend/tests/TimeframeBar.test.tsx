import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { TimeframeBar } from "@/components/TimeframeBar";

describe("TimeframeBar", () => {
  it("shows every required market timeframe", () => {
    render(<TimeframeBar value="1D" onChange={() => undefined} />);
    for (const label of ["1m", "5m", "15m", "1h", "4h", "1D", "1W"]) expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
  });

  it("supports Alt+number keyboard shortcuts", () => {
    const onChange = vi.fn();
    render(<TimeframeBar value="1D" onChange={onChange} />);
    fireEvent.keyDown(window, { key: "3", altKey: true });
    expect(onChange).toHaveBeenCalledWith("15m");
  });
});
