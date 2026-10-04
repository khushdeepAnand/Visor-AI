"use client";

import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AuthGate } from "@/components/AuthGate";
import { Stat } from "@/components/Stat";
import { TerminalShell } from "@/components/TerminalShell";
import { VirtualOrderTable, type PaperOrderRow } from "@/components/VirtualOrderTable";
import { HistoricalReplay } from "@/components/HistoricalReplay";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { api } from "@/lib/api";
import { useSessionReady } from "@/lib/auth";
import { inr } from "@/lib/utils";
import { MixedSourceNotice, useMarket } from "@/components/MarketContext";
import type { MarketDataContext } from "@/lib/api";

type Position = { symbol: string; quantity: number; average_price: number; mark_price: number; unrealized_pnl: number; market_value: number; lot_size?: number };
type Badge = { key: string; title: string; earned_at?: string };
type Account = { initial_balance: number; cash_balance: number; market_value: number; equity: number; leaderboard_opt_in: boolean; positions: Position[]; orders: PaperOrderRow[]; badges: Badge[] };
type Journal = { id: number; order_id?: number; event_type: string; notes?: string; created_at: string };
type Challenge = { key: string; title: string; scenario: string; choices: string[] };

type OrderResult = { status: string; fill_price: number; simulation_notice: string; margin_required?: number; context?: MarketDataContext };

export default function PaperTrading() {
  const queryClient = useQueryClient();
  const { selectedSymbol, setSelectedSymbol, timeframe, dispatchSurface } = useMarket();
  const [orderType, setOrderType] = useState("MARKET");
  const [instrumentType, setInstrumentType] = useState("EQUITY");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [pendingOrder, setPendingOrder] = useState<Record<string, unknown> | null>(null);
  const sessionReady = useSessionReady();

  const account = useQuery({ queryKey: ["paper"], queryFn: () => api<Account>("/api/v1/paper/account"), refetchInterval: sessionReady ? 5000 : false, enabled: sessionReady });
  const journal = useQuery({ queryKey: ["paper-journal"], queryFn: () => api<{ items: Journal[] }>("/api/v1/paper/journal?limit=50"), enabled: sessionReady });
  const challenges = useQuery({ queryKey: ["paper-challenges"], queryFn: () => api<{ items: Challenge[] }>("/api/v1/paper/challenges"), enabled: sessionReady });
  const order = useMutation({
    mutationFn: (body: unknown) => api<OrderResult>("/api/v1/paper/orders", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["paper"] });
      queryClient.invalidateQueries({ queryKey: ["paper-journal"] });
    },
  });

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(""); setMessage("");
    const form = new FormData(event.currentTarget);
    const body: Record<string, unknown> = {
      symbol: selectedSymbol, timeframe, side: form.get("side"), quantity: Number(form.get("quantity")), order_type: orderType,
      reasoning_notes: form.get("notes"), instrument_type: instrumentType, spread_bps: Number(form.get("spread_bps") || 5), slippage_bps: Number(form.get("slippage_bps") || 2),
      circuit_limit_pct: Number(form.get("circuit_limit_pct") || 20), lot_size: Number(form.get("lot_size") || 1),
    };
    if (orderType === "LIMIT") body.limit_price = Number(form.get("limit"));
    if (["STOP", "TRAILING_STOP", "BRACKET"].includes(orderType)) body.stop_price = Number(form.get("stop"));
    if (orderType === "TRAILING_STOP") body.trail_amount = Number(form.get("trail"));
    if (orderType === "BRACKET") body.target_price = Number(form.get("target"));
    if (instrumentType !== "EQUITY") body.expiry = form.get("expiry");
    if (instrumentType === "OPTION") { body.strike = Number(form.get("strike")); body.option_type = form.get("option_type"); }
    if (!String(body.reasoning_notes || "").trim()) {
      setError("Write a short thesis before reviewing the simulation.");
      return;
    }
    setPendingOrder(body);
  }

  async function confirmOrder() {
    if (!pendingOrder) return;
    setError(""); setMessage("");
    try {
      const result = await order.mutateAsync(pendingOrder);
      if (!result.context || result.context.requested_symbol.toUpperCase() !== selectedSymbol.toUpperCase() || result.context.timeframe !== timeframe) {
        dispatchSurface({ type: "error", surface: "paper_order", error: "Paper-order market context did not match the selected symbol and timeframe." });
        setError("Order recorded, but its market context did not match the selected terminal context. Review it before relying on the fill.");
        return;
      }
      dispatchSurface({ type: "context", surface: "paper_order", context: result.context });
      setMessage(`${result.status}${result.fill_price ? ` @ ${inr(result.fill_price)}` : ""}${result.margin_required ? ` · margin ${inr(result.margin_required)}` : ""} — ${result.simulation_notice}`);
      setPendingOrder(null);
    } catch (reason) { setError((reason as Error).message); }
  }

  const data = account.data;
  return (
    <TerminalShell>
      <AuthGate>
        <div className="mb-4"><h1 className="text-2xl font-semibold">Paper Trading Lab</h1><p className="text-sm text-slate-500">Deliberate equities and F&amp;O simulation with market friction, journaling, and risk-learning exercises.</p></div>
        <MixedSourceNotice />
        <div className="grid gap-3 md:grid-cols-4"><Stat label="Initial" value={inr(data?.initial_balance)} /><Stat label="Cash" value={inr(data?.cash_balance)} /><Stat label="Positions" value={inr(data?.market_value)} /><Stat label="Equity" value={inr(data?.equity)} /></div>
        <div className="mt-3 grid gap-3 xl:grid-cols-[440px_minmax(0,1fr)]">
          <Card>
            <CardHeader><CardTitle>Order Ticket</CardTitle><span className="text-[10px] text-amber-300">SIMULATED · NEVER SENT TO BROKER</span></CardHeader>
            <CardContent>
              <form onSubmit={submit} className="space-y-3">
                <div className="grid grid-cols-2 gap-2"><Input name="symbol" value={selectedSymbol} onChange={(event) => setSelectedSymbol(event.target.value.toUpperCase())} aria-label="Shared market symbol" required /><Select name="side" defaultValue="" aria-label="Paper order side" required><option value="" disabled>Choose side</option><option>BUY</option><option>SELL</option></Select></div>
                <p className="text-[10px] uppercase tracking-wide text-slate-500">Shared context: {selectedSymbol} · {timeframe}</p>
                <div className="grid grid-cols-2 gap-2"><Select value={instrumentType} onChange={(event) => setInstrumentType(event.target.value)}><option>EQUITY</option><option>FUTURE</option><option>OPTION</option></Select><Select value={orderType} onChange={(event) => setOrderType(event.target.value)}><option>MARKET</option><option>LIMIT</option><option>STOP</option><option>TRAILING_STOP</option><option>BRACKET</option></Select></div>
                <div className="grid grid-cols-2 gap-2"><Input name="quantity" type="number" step="any" min="0.0001" placeholder={instrumentType === "EQUITY" ? "Quantity" : "Lots"} required /><Input name="lot_size" type="number" step="1" min="1" defaultValue={1} placeholder="Lot size" title="For F&O use the current exchange lot size." /></div>
                {instrumentType !== "EQUITY" && <Input name="expiry" type="date" required />}
                {instrumentType === "OPTION" && <div className="grid grid-cols-2 gap-2"><Input name="strike" type="number" step="any" min="0.01" placeholder="Strike" required /><Select name="option_type"><option>CE</option><option>PE</option></Select></div>}
                {orderType === "LIMIT" && <Input name="limit" type="number" step="any" min="0.01" placeholder="Limit price" required />}
                {["STOP", "TRAILING_STOP", "BRACKET"].includes(orderType) && <Input name="stop" type="number" step="any" min="0.01" placeholder="Stop trigger" required />}
                {orderType === "BRACKET" && <Input name="target" type="number" step="any" min="0.01" placeholder="Profit target" required />}
                {orderType === "TRAILING_STOP" && <Input name="trail" type="number" step="any" min="0.01" placeholder="Trail amount" required />}
                <div className="grid grid-cols-3 gap-2"><Input name="spread_bps" type="number" min="0" max="500" step="0.1" defaultValue={5} title="Simulated spread in basis points" /><Input name="slippage_bps" type="number" min="0" max="500" step="0.1" defaultValue={2} title="Simulated slippage in basis points" /><Select name="circuit_limit_pct" defaultValue="20"><option value="2">2% circuit</option><option value="5">5% circuit</option><option value="10">10% circuit</option><option value="20">20% circuit</option></Select></div>
                <Input name="notes" aria-label="Required paper trade thesis" placeholder="Required thesis / journal note" required minLength={5} />
                <Button className="w-full" disabled={order.isPending}>Review paper order</Button>
                {pendingOrder && <div className="rounded-xl border border-warning/30 bg-warning/5 p-3" role="dialog" aria-label="Paper order decision pause"><div className="text-xs font-semibold text-warning">Decision pause</div><dl className="mt-2 grid grid-cols-2 gap-2 text-[11px] text-slate-400"><div><dt>Action</dt><dd className="text-slate-200">{String(pendingOrder.side)} {String(pendingOrder.symbol)}</dd></div><div><dt>Exposure units</dt><dd className="text-slate-200">{Number(pendingOrder.quantity) * Number(pendingOrder.lot_size)}</dd></div><div><dt>Friction</dt><dd className="text-slate-200">{String(pendingOrder.spread_bps)} bps spread + {String(pendingOrder.slippage_bps)} bps slippage</dd></div><div><dt>Forecast evidence</dt><dd className="text-slate-200">Not linked; no range selected this order</dd></div></dl><p className="mt-2 text-[11px] text-slate-500">Thesis: {String(pendingOrder.reasoning_notes)}</p><div className="mt-3 flex gap-2"><Button type="button" onClick={confirmOrder} disabled={order.isPending}>{order.isPending ? "Simulating…" : "Confirm simulation"}</Button><Button type="button" variant="ghost" onClick={() => setPendingOrder(null)} disabled={order.isPending}>Go back</Button></div></div>}
                {message && <p className="text-xs leading-5 text-gain">{message}</p>}{error && <p className="text-xs leading-5 text-loss">{error}</p>}
              </form>
            </CardContent>
          </Card>
          <div className="min-w-0 space-y-3">
            <Card><CardHeader><CardTitle>Open Positions</CardTitle></CardHeader><CardContent className="overflow-x-auto p-0"><table className="w-full text-sm"><thead className="text-left text-[10px] uppercase text-slate-600"><tr><th className="px-4 py-3">Contract</th><th>Qty</th><th>Lot</th><th>Avg</th><th>Mark</th><th>P&amp;L</th></tr></thead><tbody>{data?.positions?.map((position) => <tr key={position.symbol} className="border-t border-slate-800"><td className="px-4 py-3 font-semibold">{position.symbol}</td><td>{position.quantity}</td><td>{position.lot_size || 1}</td><td>{inr(position.average_price)}</td><td>{inr(position.mark_price)}</td><td className={position.unrealized_pnl >= 0 ? "signal-up" : "signal-down"}>{inr(position.unrealized_pnl)}</td></tr>)}</tbody></table></CardContent></Card>
            <Card><CardHeader><CardTitle>Virtualized Order History</CardTitle><span className="text-[10px] text-slate-600">TanStack Table + Virtual</span></CardHeader><CardContent className="overflow-x-auto p-0"><div className="min-w-[700px]"><VirtualOrderTable orders={data?.orders || []} /></div></CardContent></Card>
          </div>
        </div>
        <div className="mt-4"><div className="mb-2"><h2 className="display-font text-xl font-semibold text-white">Historical Replay Lab</h2><p className="text-xs text-slate-500">Commit to a decision before the sourced next-session outcome is revealed.</p></div><HistoricalReplay /></div>
        <div className="mt-3 grid gap-3 xl:grid-cols-2">
          <Card><CardHeader><CardTitle>Immutable Journal</CardTitle></CardHeader><CardContent className="max-h-72 overflow-auto p-0">{journal.data?.items?.map((item) => <div key={item.id} className="border-t border-slate-900 px-4 py-3 text-xs"><div className="flex justify-between gap-3"><span className="font-semibold text-slate-300">{item.event_type}</span><span className="tabular text-[10px] text-slate-600">#{item.order_id || "—"}</span></div>{item.notes && <p className="mt-1 text-slate-500">{item.notes}</p>}<div className="mt-1 text-[9px] text-slate-700">{item.created_at}</div></div>)}{!journal.data?.items?.length && <div className="p-4 text-xs text-slate-600">Order lifecycle events appear here.</div>}</CardContent></Card>
          <Card><CardHeader><CardTitle>Weekly Challenges</CardTitle></CardHeader><CardContent className="space-y-3">{challenges.data?.items?.map((item) => <div key={item.key} className="rounded border border-slate-800 p-3"><div className="text-xs font-semibold text-slate-200">{item.title}</div><p className="mt-1 text-[10px] leading-4 text-slate-500">{item.scenario}</p><div className="mt-2 flex flex-wrap gap-1">{item.choices.map((choice) => <span key={choice} className="rounded bg-slate-900 px-2 py-1 text-[9px] text-slate-400">{choice}</span>)}</div></div>)}</CardContent></Card>
        </div>
      </AuthGate>
    </TerminalShell>
  );
}
