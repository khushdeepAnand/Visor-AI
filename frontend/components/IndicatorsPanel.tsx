"use client";
import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type MarketDataContext } from "@/lib/api";
import { inr } from "@/lib/utils";
import { MarketSourceLabel, useMarket } from "@/components/MarketContext";
import type { Timeframe } from "@/components/TimeframeBar";

type IndicatorsResponse = {
  symbol: string;
  as_of: string;
  price: number;
  indicators: Record<string, number | null>;
  context?: MarketDataContext;
};

function fmt(value: number | null | undefined, digits = 2) {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

function Gauge({ label, value, low, high, invert }: { label: string; value: number | null | undefined; low: number; high: number; invert?: boolean }) {
  const hot = value != null && (invert ? value <= low : value >= high);
  const cold = value != null && (invert ? value >= high : value <= low);
  const tone = hot ? "text-loss" : cold ? "text-gain" : "text-slate-300";
  const pct = value == null ? 0 : Math.max(0, Math.min(100, value));
  return (
    <div className="rounded border border-slate-800 p-2">
      <div className="flex items-baseline justify-between">
        <span className="text-[9px] uppercase tracking-wide text-slate-600">{label}</span>
        <span className={`tabular text-sm font-semibold ${tone}`}>{fmt(value)}</span>
      </div>
      <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-slate-800">
        <div className={`h-full rounded-full ${hot ? "bg-loss" : cold ? "bg-gain" : "bg-accent"}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between border-t border-slate-900 py-1.5 first:border-t-0">
      <span className="text-[10px] uppercase tracking-wide text-slate-600">{label}</span>
      <span className="tabular text-xs text-slate-200">{value}</span>
    </div>
  );
}

const indicatorWindows: Record<Timeframe, string> = {
  "1m": "1w",
  "5m": "1mo",
  "15m": "3mo",
  "1h": "3mo",
  "4h": "1y",
  "1D": "1y",
  "1W": "5y",
};

export function IndicatorsPanel({ symbol, timeframe }: { symbol: string; timeframe: Timeframe }) {
  const { dispatchSurface } = useMarket();
  const query = useQuery({
    queryKey: ["indicators", symbol, timeframe],
    queryFn: () => api<IndicatorsResponse>(`/api/v1/indicators/${encodeURIComponent(symbol)}?timeframe=${timeframe}&window=${indicatorWindows[timeframe]}`),
    refetchInterval: 15000,
    retry: 1,
  });

  useEffect(() => {
    dispatchSurface({ type: "context", surface: "indicators", context: query.data?.context });
    if (query.error) dispatchSurface({ type: "error", surface: "indicators", error: (query.error as Error).message });
  }, [query.data, query.error, dispatchSurface]);

  if (query.isLoading) {
    return <div className="space-y-2 bg-terminal-950 p-3"><div className="skeleton h-14 w-full rounded" /><div className="skeleton h-14 w-full rounded" /><div className="skeleton h-24 w-full rounded" /></div>;
  }
  if (query.error) {
    return <div className="h-full bg-terminal-950 p-3 text-xs text-slate-600">Indicators unavailable — {(query.error as Error).message}</div>;
  }
  const ind = query.data?.indicators || {};
  const macdBullish = (ind.MACD ?? 0) >= (ind.MACD_Signal ?? 0);
  const bbUpper = ind.BB_Upper, bbLower = ind.BB_Lower, price = query.data?.price;
  const bbPosition = bbUpper != null && bbLower != null && price != null && bbUpper !== bbLower
    ? ((price - bbLower) / (bbUpper - bbLower)) * 100
    : null;

  return (
    <div className="h-full overflow-auto bg-terminal-950 text-xs">
      <MarketSourceLabel context={query.data?.context} />
      <div className="p-2">
      <div className="grid grid-cols-2 gap-2">
        <Gauge label="RSI (14)" value={ind.RSI} low={30} high={70} />
        <Gauge label="Stochastic %K" value={ind.Stochastic_K} low={20} high={80} />
        <Gauge label="Williams %R" value={ind.Williams_R != null ? ind.Williams_R + 100 : null} low={20} high={80} invert />
        <Gauge label="MFI (14)" value={ind.MFI} low={20} high={80} />
      </div>
      <div className="mt-2 rounded border border-slate-800 p-2">
        <div className="mb-1 flex items-center justify-between">
          <span className="text-[9px] uppercase tracking-wide text-slate-600">MACD (12,26,9)</span>
          <span className={`rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase ${macdBullish ? "bg-gain/10 text-gain" : "bg-loss/10 text-loss"}`}>{macdBullish ? "Bullish" : "Bearish"}</span>
        </div>
        <Row label="MACD" value={fmt(ind.MACD, 3)} />
        <Row label="Signal" value={fmt(ind.MACD_Signal, 3)} />
        <Row label="Histogram" value={fmt(ind.MACD_Histogram, 3)} />
      </div>
      <div className="mt-2 rounded border border-slate-800 p-2">
        <div className="mb-1 text-[9px] uppercase tracking-wide text-slate-600">Bollinger Bands (20, 2σ)</div>
        <Row label="Upper" value={inr(ind.BB_Upper ?? undefined)} />
        <Row label="Middle" value={inr(ind.BB_Middle ?? undefined)} />
        <Row label="Lower" value={inr(ind.BB_Lower ?? undefined)} />
        {bbPosition != null && (
          <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-slate-800">
            <div className="h-full rounded-full bg-accent" style={{ width: `${Math.max(0, Math.min(100, bbPosition))}%` }} />
          </div>
        )}
      </div>
      <div className="mt-2 rounded border border-slate-800 p-2">
        <Row label="ATR (14)" value={fmt(ind.ATR)} />
        <Row label="ADX (14)" value={fmt(ind.ADX)} />
        <Row label="CCI (20)" value={fmt(ind.CCI)} />
        <Row label="VWAP" value={inr(ind.VWAP ?? undefined)} />
        <Row label="CMF (20)" value={fmt(ind.CMF, 3)} />
      </div>
      <p className="mt-2 text-[10px] leading-4 text-slate-600">RSI/Stochastic/Williams %R/MFI gauges glow red above their overbought band and green below their oversold band. Not investment advice.</p>
      </div>
    </div>
  );
}
