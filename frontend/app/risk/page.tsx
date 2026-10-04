"use client";

import { FormEvent, useState } from "react";
import { RiskScenarioChart } from "@/components/RiskScenarioChart";
import { Stat } from "@/components/Stat";
import { TerminalShell } from "@/components/TerminalShell";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { inr, pct } from "@/lib/utils";

type RiskData = {
  symbol: string;
  historical_metrics: { annualized_return: number; annualized_volatility: number; sharpe_ratio: number; sortino_ratio: number; max_drawdown: number; var_95: number; cvar_95: number; hit_rate: number };
  monte_carlo: { last_price: number; horizon_days: number; simulations: number; probability_finish_above_start: number; terminal_percentiles: Record<string, number>; sample_paths: number[][]; disclaimer: string };
};

export default function Risk() {
  const [data, setData] = useState<RiskData>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(""); setLoading(true);
    const symbol = String(new FormData(event.currentTarget).get("symbol") || "");
    try { setData(await api<RiskData>(`/api/v1/risk/${encodeURIComponent(symbol)}?window=5y&timeframe=1D`)); }
    catch (reason) { setError((reason as Error).message); }
    finally { setLoading(false); }
  }

  const metrics = data?.historical_metrics;
  return (
    <TerminalShell>
      <div className="mb-4"><h1 className="text-2xl font-semibold">Risk Lab</h1><p className="text-sm text-slate-500">Performance diagnostics and Monte Carlo scenarios from the retained analytics engine.</p></div>
      <Card><CardHeader><CardTitle>Analyze Symbol</CardTitle></CardHeader><CardContent><form onSubmit={submit} className="flex max-w-lg gap-2"><Input name="symbol" placeholder="RELIANCE" required /><Button disabled={loading}>{loading ? "Running…" : "Run risk analysis"}</Button></form>{error && <p className="mt-2 text-xs text-loss">{error}</p>}</CardContent></Card>
      {data && metrics && <>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4"><Stat label="Annual return" value={pct(metrics.annualized_return * 100)} tone={metrics.annualized_return >= 0 ? "up" : "down"} /><Stat label="Volatility" value={pct(metrics.annualized_volatility * 100)} /><Stat label="Sharpe" value={metrics.sharpe_ratio.toFixed(2)} /><Stat label="Max drawdown" value={pct(metrics.max_drawdown * 100)} tone="down" /></div>
        <div className="mt-3 grid gap-3 xl:grid-cols-[minmax(0,1.5fr)_minmax(320px,.5fr)]">
          <Card><CardHeader><CardTitle>{data.symbol} · Monte Carlo scenario fan</CardTitle><span className="text-[10px] text-slate-600">{data.monte_carlo.simulations.toLocaleString()} simulations · {data.monte_carlo.horizon_days} trading days</span></CardHeader><CardContent><RiskScenarioChart monteCarlo={data.monte_carlo} /><p className="mt-2 text-[10px] leading-4 text-slate-600">{data.monte_carlo.disclaimer}</p></CardContent></Card>
          <Card><CardHeader><CardTitle>Terminal distribution</CardTitle></CardHeader><CardContent className="space-y-3 text-xs">{Object.entries(data.monte_carlo.terminal_percentiles).map(([key, value]) => <div key={key} className="flex items-center justify-between border-b border-slate-900 pb-2"><span className="uppercase text-slate-600">{key}</span><span className="tabular text-slate-200">{inr(value)}</span></div>)}<div className="rounded border border-accent/20 bg-accent/5 p-3"><div className="text-[9px] uppercase tracking-wider text-slate-600">Probability above start</div><div className="tabular mt-1 text-xl text-accent">{pct(data.monte_carlo.probability_finish_above_start * 100)}</div></div></CardContent></Card>
        </div>
        <Card className="mt-3"><CardHeader><CardTitle>Risk metrics</CardTitle></CardHeader><CardContent><div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">{[["Sortino", metrics.sortino_ratio.toFixed(2)], ["Hit rate", pct(metrics.hit_rate * 100)], ["VaR 95", pct(metrics.var_95 * 100)], ["CVaR 95", pct(metrics.cvar_95 * 100)]].map(([label, value]) => <div key={label} className="rounded border border-slate-800 p-3"><div className="text-[9px] uppercase text-slate-600">{label}</div><div className="tabular mt-1 text-lg text-slate-200">{value}</div></div>)}</div></CardContent></Card>
      </>}
    </TerminalShell>
  );
}
