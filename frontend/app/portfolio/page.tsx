"use client";

import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AuthGate } from "@/components/AuthGate";
import { PortfolioPnlChart } from "@/components/PortfolioPnlChart";
import { Stat } from "@/components/Stat";
import { TerminalShell } from "@/components/TerminalShell";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { API_BASE, api } from "@/lib/api";
import { useSessionReady } from "@/lib/auth";
import { inr, pct } from "@/lib/utils";
import { MiniTrend } from "@/components/MiniTrend";

type Holding = { id: number; symbol: string; company: string; shares: number; buy_price: number; mark_price: number; market_value: number; pnl: number; pnl_pct: number };
type Data = { holdings: Holding[]; summary: { cost: number; market_value: number; pnl: number; pnl_pct: number } };

export default function Portfolio() {
  const queryClient = useQueryClient();
  const sessionReady = useSessionReady();
  const [error, setError] = useState("");
  const query = useQuery({ queryKey: ["portfolio"], queryFn: () => api<Data>("/api/v1/portfolio"), enabled: sessionReady });
  const buy = useMutation({ mutationFn: (body: unknown) => api("/api/v1/portfolio", { method: "POST", body: JSON.stringify(body) }), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["portfolio"] }) });

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError("");
    const form = new FormData(event.currentTarget);
    try {
      await buy.mutateAsync({ symbol: form.get("symbol"), shares: Number(form.get("shares")), buy_price: Number(form.get("price")) });
      event.currentTarget.reset();
    } catch (reason) { setError((reason as Error).message); }
  }

  async function downloadPortfolioPdf() {
    setError("");
    try {
      const response = await fetch(`${API_BASE}/api/v1/reports/portfolio.pdf`, { credentials: "include" });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `${response.status} ${response.statusText}`);
      }
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a");
      anchor.href = url; anchor.download = "stockpilot-portfolio.pdf"; anchor.click();
      URL.revokeObjectURL(url);
    } catch (reason) { setError((reason as Error).message); }
  }

  const summary = query.data?.summary;
  return (
    <TerminalShell>
      <AuthGate>
        <div className="mb-4 flex flex-wrap items-end justify-between gap-3"><div><h1 className="text-2xl font-semibold">Portfolio</h1><p className="text-sm text-slate-500">Tracked NSE/BSE holdings and analytics; no real order execution.</p></div><Button variant="ghost" onClick={downloadPortfolioPdf} disabled={!query.data?.holdings?.length}>Export PDF</Button></div>
        <div className="grid gap-3 md:grid-cols-4"><Stat label="Cost" value={inr(summary?.cost)} /><Stat label="Market value" value={inr(summary?.market_value)} /><Stat label="P&L" value={inr(summary?.pnl)} tone={(summary?.pnl || 0) >= 0 ? "up" : "down"} /><Stat label="Return" value={pct(summary?.pnl_pct)} tone={(summary?.pnl_pct || 0) >= 0 ? "up" : "down"} /></div>
        <div className="mt-3 grid gap-3 xl:grid-cols-[420px_1fr]">
          <Card><CardHeader><CardTitle>Add holding</CardTitle></CardHeader><CardContent><form onSubmit={submit} className="space-y-2"><Input name="symbol" placeholder="INFY" required /><div className="grid grid-cols-2 gap-2"><Input name="shares" type="number" step="any" min="0.0001" placeholder="Shares" required /><Input name="price" type="number" step="any" min="0.01" placeholder="Buy price" required /></div><Button className="w-full" disabled={buy.isPending}>Add holding</Button></form>{error && <p className="mt-2 text-xs text-loss">{error}</p>}</CardContent></Card>
          <Card><CardHeader><CardTitle>P&amp;L attribution</CardTitle></CardHeader><CardContent><PortfolioPnlChart holdings={query.data?.holdings || []} /></CardContent></Card>
        </div>
        <Card className="mt-3"><CardHeader><CardTitle>Holdings</CardTitle></CardHeader><CardContent className="overflow-x-auto p-0"><table className="w-full text-sm"><thead className="text-left text-[10px] uppercase text-slate-600"><tr><th className="px-4 py-3">Symbol</th><th>Qty</th><th>Avg</th><th>Mark</th><th>Value</th><th>P&amp;L</th><th>Trend</th></tr></thead><tbody>{query.data?.holdings?.map((item) => <tr key={item.id} className="interactive-surface border-t border-slate-800"><td className="px-4 py-3 font-semibold">{item.symbol}</td><td className="tabular">{item.shares}</td><td className="tabular">{inr(item.buy_price)}</td><td className="tabular">{inr(item.mark_price)}</td><td className="tabular">{inr(item.market_value)}</td><td className={`tabular ${item.pnl >= 0 ? "signal-up" : "signal-down"}`}>{item.pnl >= 0 ? "▲ " : "▼ "}{inr(item.pnl)} · {pct(item.pnl_pct)}</td><td><MiniTrend value={item.pnl_pct} label={`${item.symbol} portfolio return`} /></td></tr>)}</tbody></table></CardContent></Card>
      </AuthGate>
    </TerminalShell>
  );
}
