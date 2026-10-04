import type { Timeframe } from "@/components/TimeframeBar";

export type ChartOverlays = { ema: boolean; vwap: boolean; band: boolean };

export type ChartLayout = {
  id: number;
  name: string;
  symbol: string;
  timeframe: Timeframe;
  overlays: Partial<ChartOverlays>;
  visible_range?: { from: number; to: number } | null;
  created_at: string;
  updated_at: string;
};

export type LayoutDraft = {
  name: string;
  symbol: string;
  timeframe: Timeframe;
  overlays: ChartOverlays;
  visible_range?: { from: number; to: number } | null;
};

export function buildLayoutPayload(draft: Omit<LayoutDraft, "name">, name: string): LayoutDraft {
  return {
    name: name.trim(),
    symbol: draft.symbol.trim().toUpperCase(),
    timeframe: draft.timeframe,
    overlays: { ema: Boolean(draft.overlays.ema), vwap: Boolean(draft.overlays.vwap), band: Boolean(draft.overlays.band) },
    visible_range: draft.visible_range ?? undefined,
  };
}

/** A saved range is only safe to apply when it was recorded for this symbol+timeframe. */
export function canApplyRange(
  layout: Pick<ChartLayout, "symbol" | "timeframe" | "visible_range">,
  symbol: string,
  timeframe: Timeframe,
): boolean {
  return (
    typeof layout.visible_range?.from === "number"
    && typeof layout.visible_range?.to === "number"
    && layout.symbol.toUpperCase() === symbol.trim().toUpperCase()
    && layout.timeframe === timeframe
  );
}