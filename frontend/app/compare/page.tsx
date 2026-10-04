"use client";

import { FormEvent, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { TerminalShell } from "@/components/TerminalShell";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { api } from "@/lib/api";
import { inr, pct } from "@/lib/utils";

type Item = {
  symbol: string;
  company: string;
  exchange: string;
  quote: { price: number; change_pct?: number | null; source: string; is_stale?: boolean };
  indicators: { rsi_14: number; macd: number; sma_20: number; sma_50: number; vwap: number; volatility_pct: number };
  forecast: { low: number; median_reference: number; high: number; confidence_level: number } | null;
  forecast_status?: string;
  abstention_reason?: string;
  confidence: { level: string; summary: string };
  uncertainty: { range_width_pct: number; band: string };
};

type Comparison = { items: Item[]; count: number };

export default function ComparePage() {
  const [symbols, setSymbols] = useState("RELIANCE,TCS");
  const [windowValue, setWindowValue] = useState("1y");
  const [submitted, setSubmitted] = useState({ symbols: "RELIANCE,TCS", trainingWindow: "1y" });
  const query = useQuery({
    queryKey: ["compare", submitted],
    queryFn: () => api<Comparison>(`/api/v1/compare?symbols=${encodeURIComponent(submitted.symbols)}&training_window=${submitted.trainingWindow}`),
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitted({ symbols, trainingWindow: windowValue });
  }

  return (
    <TerminalShell>
      <div className="mb-4">
        <div className="text-xs uppercase tracking-[.22em] text-accent">Range-first research</div>
        <h1 className="mt-1 text-2xl font-semibold">Compare NSE / BSE symbols</h1>
        <p className="mt-1 text-sm text-slate-500">Use the same data window, indicators and uncertainty model across 2–4 Indian instruments.</p>
      </div>
      <Card>
        <CardContent className="pt-4">
          <form onSubmit={submit} className="grid gap-2 md:grid-cols-[1fr_150px_120px]">
            <Input value={symbols} onChange={(event) => setSymbols(event.target.value)} placeholder="RELIANCE,TCS,INFY" />
            <Select value={windowValue} onChange={(event) => setWindowValue(event.target.value)}>
              <option value="1w">1 week</option><option value="1mo">1 month</option><option value="3mo">3 months</option><option value="1y">1 year</option><option value="5y">5 years</option>
            </Select>
            <Button disabled={query.isFetching}>{query.isFetching ? "Comparing…" : "Compare"}</Button>
          </form>
          {query.error && <p className="mt-3 text-xs text-loss">{(query.error as Error).message}</p>}
        </CardContent>
      </Card>
      <div className="mt-3 grid gap-3 xl:grid-cols-2">
        {query.data?.items?.map((item) => (
          <Card key={item.symbol}>
            <CardHeader>
              <div><CardTitle>{item.symbol}</CardTitle><div className="mt-1 text-xs text-slate-500">{item.company} · {item.exchange}</div></div>
              <div className="text-right"><div className="tabular text-lg font-semibold">{inr(item.quote.price)}</div><div className={`tabular text-xs ${(item.quote.change_pct || 0) >= 0 ? "signal-up" : "signal-down"}`}>{pct(item.quote.change_pct)}</div></div>
            </CardHeader>
            <CardContent>
              {item.forecast ? <div className="rounded-md border border-accent/20 bg-accent/5 p-4">
                <div className="text-[10px] uppercase tracking-wider text-slate-500">{Math.round(item.forecast.confidence_level * 100)}% forecast interval</div>
                <div className="mt-2 tabular text-xl font-semibold text-accent">{inr(item.forecast.low)} – {inr(item.forecast.high)}</div>
                <div className="mt-1 text-xs text-slate-400">Median reference {inr(item.forecast.median_reference)}</div>
              </div> : <div className="rounded-md border border-loss/30 bg-loss/5 p-4 text-xs text-slate-300"><b>No numerical corridor released.</b><div className="mt-1 text-slate-500">{item.abstention_reason || `Trust gate: ${item.forecast_status || "blocked"}`}</div></div>}
              <div className="mt-3 grid grid-cols-2 gap-2 text-xs md:grid-cols-3">
                <Metric label="RSI 14" value={item.indicators.rsi_14.toFixed(2)} />
                <Metric label="MACD" value={item.indicators.macd.toFixed(2)} />
                <Metric label="SMA 20" value={inr(item.indicators.sma_20)} />
                <Metric label="VWAP" value={item.indicators.vwap ? inr(item.indicators.vwap) : "—"} />
                <Metric label="Range width" value={`${item.uncertainty.range_width_pct.toFixed(2)}%`} />
                <Metric label="Confidence" value={item.confidence.level} />
              </div>
              <p className="mt-3 text-xs leading-5 text-slate-400">{item.confidence.summary}</p>
              <div className="mt-3 text-[10px] text-slate-600">Source: {item.quote.source}{item.quote.is_stale ? " · stale cache" : ""}. Research/paper trading only.</div>
            </CardContent>
          </Card>
        ))}
      </div>
    </TerminalShell>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="rounded border border-slate-800 bg-slate-950/40 p-2"><div className="text-[9px] uppercase tracking-wider text-slate-600">{label}</div><div className="mt-1 tabular text-slate-300">{value}</div></div>;
}
