"use client";

import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { TerminalShell } from "@/components/TerminalShell";
import { AuthGate } from "@/components/AuthGate";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { useSessionReady } from "@/lib/auth";
import { inr } from "@/lib/utils";

type Alert = { id: number; symbol: string; condition: string; threshold: number; training_window: string; confidence_level: number; is_active: boolean; email_enabled?: boolean; last_triggered_at?: string };
type Triggered = Alert & { current_price?: number | null; triggered_at: string; forecast?: { low: number; median: number; high: number; confidence_level: number } };

export default function Alerts() {
  const qc = useQueryClient();
  const sessionReady = useSessionReady();
  const [err, setErr] = useState("");
  const [triggered, setTriggered] = useState<Triggered[]>([]);
  const q = useQuery({ queryKey: ["alerts"], queryFn: () => api<{ items: Alert[] }>("/api/v1/alerts"), enabled: sessionReady });
  const add = useMutation({ mutationFn: (body: unknown) => api("/api/v1/alerts", { method: "POST", body: JSON.stringify(body) }), onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts"] }) });
  const evaluate = useMutation({ mutationFn: () => api<{ triggered: Triggered[] }>("/api/v1/alerts/evaluate", { method: "POST" }), onSuccess: (data) => { setTriggered(data.triggered || []); qc.invalidateQueries({ queryKey: ["alerts"] }); } });

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setErr("");
    const f = new FormData(event.currentTarget);
    try {
      await add.mutateAsync({
        symbol: f.get("symbol"), condition: f.get("condition"), threshold: Number(f.get("threshold")),
        training_window: f.get("training_window") || "1mo", confidence_level: 0.8, email_enabled: f.get("email_enabled") === "on",
      });
      event.currentTarget.reset();
    } catch (reason) { setErr((reason as Error).message); }
  }

  return <TerminalShell><AuthGate>
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3"><div><h1 className="text-2xl font-semibold">Price &amp; Forecast Alerts</h1><p className="text-sm text-slate-500">Spot thresholds plus range-aware triggers. Forecast triggers always include the full interval.</p></div><Button variant="ghost" onClick={() => evaluate.mutate()} disabled={evaluate.isPending}>{evaluate.isPending ? "Evaluating…" : "Evaluate now"}</Button></div>
    <Card><CardHeader><CardTitle>Create alert</CardTitle></CardHeader><CardContent><form onSubmit={submit} className="grid gap-2 lg:grid-cols-[1fr_210px_180px_140px_auto]"><Input name="symbol" placeholder="TCS" required/><Select name="condition"><option value="ABOVE">Spot ABOVE</option><option value="BELOW">Spot BELOW</option><option value="FORECAST_HIGH_ABOVE">Forecast high ABOVE</option><option value="FORECAST_LOW_BELOW">Forecast low BELOW</option></Select><Input name="threshold" type="number" step="any" placeholder="Price" required/><Select name="training_window" defaultValue="1mo"><option value="1w">1 week</option><option value="1mo">1 month</option><option value="3mo">3 months</option><option value="1y">1 year</option><option value="5y">5 years</option></Select><Button>Create</Button><label className="flex min-h-11 items-center gap-2 text-xs text-slate-400 lg:col-span-5"><input type="checkbox" name="email_enabled" className="size-4 accent-[var(--accent-primary)]"/>Email me when this rule triggers. Delivery requires SMTP configuration.</label></form>{err&&<p className="mt-2 text-xs text-loss">{err}</p>}</CardContent></Card>
    {triggered.length > 0 && <Card className="mt-3"><CardHeader><CardTitle>Triggered now</CardTitle></CardHeader><CardContent className="space-y-2">{triggered.map((item) => <div key={item.id} className="rounded border border-amber-400/20 bg-amber-400/5 p-3 text-xs"><div className="font-semibold text-amber-200">{item.symbol} · {item.condition} {inr(item.threshold)}</div>{item.forecast ? <div className="mt-1 tabular text-slate-300">{Math.round(item.forecast.confidence_level*100)}% interval: {inr(item.forecast.low)} – {inr(item.forecast.high)} · median {inr(item.forecast.median)}</div> : <div className="mt-1 tabular text-slate-300">Current {inr(item.current_price)}</div>}</div>)}</CardContent></Card>}
    <Card className="mt-3"><CardHeader><CardTitle>Rules</CardTitle></CardHeader><CardContent className="overflow-x-auto p-0"><table className="w-full text-sm"><thead className="text-left text-[10px] uppercase text-slate-600"><tr><th className="px-4 py-3">Symbol</th><th>Condition</th><th>Threshold</th><th>Window</th><th>Delivery</th><th>Status</th><th>Last trigger</th></tr></thead><tbody>{q.data?.items?.map(x=><tr key={x.id} className="border-t border-slate-800"><td className="px-4 py-3 font-semibold">{x.symbol}</td><td>{x.condition.replaceAll("_", " ")}</td><td>{inr(x.threshold)}</td><td>{x.training_window}</td><td className="text-xs text-slate-500">{x.email_enabled?"EMAIL":"IN APP"}</td><td className={x.is_active?"text-gain":"text-slate-600"}>{x.is_active?"ACTIVE":"PAUSED"}</td><td className="text-xs text-slate-500">{x.last_triggered_at||"—"}</td></tr>)}</tbody></table></CardContent></Card>
  </AuthGate></TerminalShell>;
}
