import { describe, expect, it } from "vitest";
import { buildLayoutPayload, canApplyRange } from "@/lib/chartLayouts";

describe("buildLayoutPayload", () => {
  it("normalizes symbol case, trims the name, and coerces overlay booleans", () => {
    const payload = buildLayoutPayload(
      { symbol: "  reliance ", timeframe: "1D", overlays: { ema: true, vwap: false, band: 1 as unknown as boolean } },
      "  Daily view  ",
    );
    expect(payload.symbol).toBe("RELIANCE");
    expect(payload.name).toBe("Daily view");
    expect(payload.overlays).toEqual({ ema: true, vwap: false, band: true });
    expect(payload.visible_range).toBeUndefined();
  });

  it("carries a visible range when captured", () => {
    const payload = buildLayoutPayload(
      { symbol: "TCS", timeframe: "15m", overlays: { ema: true, vwap: true, band: false }, visible_range: { from: 10, to: 20 } },
      "tcs-15m",
    );
    expect(payload.visible_range).toEqual({ from: 10, to: 20 });
  });
});

describe("canApplyRange", () => {
  it("only lets a range apply to the matching symbol and timeframe", () => {
    const layout = { symbol: "RELIANCE", timeframe: "1D" as const, visible_range: { from: 1, to: 2 } };
    expect(canApplyRange(layout, "reliance", "1D")).toBe(true);
    expect(canApplyRange(layout, "TCS", "1D")).toBe(false);
    expect(canApplyRange(layout, "RELIANCE", "15m")).toBe(false);
    expect(canApplyRange({ symbol: "RELIANCE", timeframe: "1D", visible_range: null }, "RELIANCE", "1D")).toBe(false);
  });
});