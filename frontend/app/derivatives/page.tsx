"use client";
import { FormEvent, useEffect, useRef, useState } from "react";
import { Activity, Calculator, Layers3, RefreshCw, TrendingUp, DollarSign } from "lucide-react";
import { PayoffChart, type PayoffResponse } from "@/components/PayoffChart";
import { TerminalShell } from "@/components/TerminalShell";
import { OptionChainTable, type OptionChainRow } from "@/components/OptionChainTable";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { api, formatApiError, type MarketDataContext, type PayoffMargin, type MarginEstimate } from "@/lib/api";
import { inr, pct } from "@/lib/utils";
import { useMarket } from "@/components/MarketContext";
import { MarginEstimateCard } from "@/components/MarginEstimateCard";

type Tab = "chain" | "scenario" | "futures" | "term" | "payoff";
type PayoffLeg = { type: string; side: string; strike: string; premium: string; quantity: string; lot_size: string };
const BLANK_LEG: PayoffLeg = { type: "call", side: "buy", strike: "", premium: "", quantity: "1", lot_size: "50" };
type ChainResp = { underlying: string; expiry: string; source: string; is_live: boolean; is_stale: boolean; fetched_at?: string; message?: string; rows: OptionChainRow[]; context?: MarketDataContext };
type ScenarioResp = { model_label: string; exercise_type: string; spot: number; strike: number; days_to_expiry: number; volatility: number; risk_free_rate: number; scenarios: { label: string; reference_price: number; greeks: { delta: number; gamma: number; theta_per_day: number; vega_per_vol_point: number }; note: string }[]; disclaimer: string; context?: MarketDataContext };
type FuturesResp = { spot_price: number; futures_price: number; days_to_expiry: number; basis: number; basis_pct: number; annualized_basis_pct: number; theoretical_fair_value: number; fair_value_mispricing_pct: number; open_interest: number; change_in_open_interest_pct: number; futures_price_change_pct: number; classification: string; interpretation: string; data_status: string };
type TermContractForm = { label: string; days_to_expiry: string; futures_price: string; open_interest: string };
type TermStructureResp = {
  spot_price: number;
  curve_state: "contango" | "backwardation" | "flat" | "mixed";
  near_to_far_slope_pct: number;
  data_status: string;
  contracts: Array<{ label: string; days_to_expiry: number; futures_price: number; open_interest: number | null; basis: number; basis_pct: number; theoretical_fair_value: number; fair_value_difference_pct: number; open_interest_share_pct: number | null }>;
  rollovers: Array<{ from_label: string; to_label: string; day_gap: number; long_roll_cost_points: number; long_roll_cost_pct: number; annualized_roll_yield_pct: number | null; state: string }>;
  warnings: Array<{ code: string; message: string }>;
};
const DEFAULT_TERM_CONTRACTS: TermContractForm[] = [
  { label: "Near month", days_to_expiry: "30", futures_price: "", open_interest: "" },
  { label: "Next month", days_to_expiry: "60", futures_price: "", open_interest: "" },
  { label: "Far month", days_to_expiry: "90", futures_price: "", open_interest: "" },
];

function TermStructureChart({ contracts }: { contracts: TermStructureResp["contracts"] }) {
  const prices = contracts.map(contract => Number(contract.futures_price));
  const low = Math.min(...prices);
  const high = Math.max(...prices);
  const spread = Math.max(high - low, 1);
  const points = contracts.map((contract, index) => {
    const x = contracts.length === 1 ? 50 : 8 + index * (84 / (contracts.length - 1));
    const y = 82 - ((Number(contract.futures_price) - low) / spread) * 64;
    return { contract, x, y };
  });
  return <svg viewBox="0 0 100 100" role="img" aria-label="Futures prices by contract month" className="h-56 w-full overflow-visible rounded-lg border border-slate-800 bg-terminal-950 p-3">
    <line x1="8" y1="82" x2="92" y2="82" stroke="currentColor" className="text-slate-800" />
    <polyline points={points.map(point => `${point.x},${point.y}`).join(" ")} fill="none" stroke="currentColor" strokeWidth="1.5" className="text-accent" />
    {points.map(({ contract, x, y }) => <g key={contract.label}>
      <circle cx={x} cy={y} r="2.25" fill="currentColor" className="text-accent" />
      <text x={x} y={Math.max(8, y - 5)} textAnchor="middle" className="fill-slate-300 text-[4px]">{Number(contract.futures_price).toFixed(2)}</text>
      <text x={x} y="91" textAnchor="middle" className="fill-slate-500 text-[3.6px]">{contract.label}</text>
    </g>)}
  </svg>;
}

export default function Derivatives() {
  const { selectedSymbol: underlying, setSelectedSymbol, timeframe, dispatchSurface } = useMarket();
  const [tab, setTab] = useState<Tab>("chain");
  const [payoffLegs, setPayoffLegs] = useState<PayoffLeg[]>([{ ...BLANK_LEG }]);
  const [payoffSpot, setPayoffSpot] = useState("");
  const [payoffData, setPayoffData] = useState<PayoffResponse | null>(null);
  const [payoffErr, setPayoffErr] = useState("");
  const [payoffLoading, setPayoffLoading] = useState(false);

  function updateLeg(index: number, patch: Partial<PayoffLeg>) {
    setPayoffLegs(legs => legs.map((leg, position) => (position === index ? { ...leg, ...patch } : leg)));
  }

  async function loadPayoff(event: FormEvent) {
    event.preventDefault();
    setPayoffLoading(true);
    setPayoffErr("");
    setPayoffData(null);
    try {
      const body = {
        underlying,
        spot: Number(payoffSpot),
        legs: payoffLegs.map(leg => ({
          type: leg.type,
          side: leg.side,
          strike: leg.type === "future" ? undefined : Number(leg.strike),
          premium: Number(leg.premium),
          quantity: Number(leg.quantity),
          lot_size: Number(leg.lot_size),
        })),
      };
      setPayoffData(await api<PayoffResponse>("/api/v1/options/payoff", { method: "POST", body: JSON.stringify(body) }));
    } catch (error) {
      setPayoffErr(formatApiError(error));
      setPayoffData(null);
    } finally {
      setPayoffLoading(false);
    }
  }
  const [expiry, setExpiry] = useState("");
  const [expiries, setExpiries] = useState<string[]>([]);
  const [chain, setChain] = useState<ChainResp>();
  const [chainErr, setChainErr] = useState("");
  const [loading, setLoading] = useState(false);
  const expiriesAbort = useRef<AbortController | null>(null);
  const chainAbort = useRef<AbortController | null>(null);

  const [scenForm, setScenForm] = useState({ spot: "", strike: "", days: "30", vol: "20", rate: "6.5", type: "call", underlying: "index" });
  const [scenData, setScenData] = useState<ScenarioResp>();
  const [scenErr, setScenErr] = useState("");
  const [scenLoading, setScenLoading] = useState(false);

  const [futData, setFutData] = useState<FuturesResp>();
  const [futErr, setFutErr] = useState("");
  const [futLoading, setFutLoading] = useState(false);
  const [futForm, setFutForm] = useState({ spot: "", futures: "", days: "30", oi: "", previousOi: "", previousFutures: "", rate: "6.5", carry: "0" });
  const [tsData, setTsData] = useState<TermStructureResp>();
  const [tsErr, setTsErr] = useState("");
  const [tsLoading, setTsLoading] = useState(false);
  const [tsForm, setTsForm] = useState({ spot: "", rate: "6.5", carry: "0" });
  const [tsContracts, setTsContracts] = useState<TermContractForm[]>(DEFAULT_TERM_CONTRACTS);

  async function loadExpiries() {
    expiriesAbort.current?.abort();
    const controller = new AbortController();
    expiriesAbort.current = controller;
    setChainErr(""); setChain(undefined); setExpiries([]); setExpiry("");
    try {
      const r = await api<{ items?: string[]; expiries?: string[] }>(`/api/v1/derivatives/expiries/${encodeURIComponent(underlying)}`, { signal: controller.signal });
      if (controller.signal.aborted) return;
      const items = r.items || r.expiries || [];
      setExpiries(items);
      if (items.length) setExpiry(items[0]);
    } catch (e) {
      if (!controller.signal.aborted) setChainErr(formatApiError(e));
    }
  }

  async function loadChain() {
    if (!expiry) return;
    chainAbort.current?.abort();
    const controller = new AbortController();
    chainAbort.current = controller;
    setLoading(true); setChainErr(""); setChain(undefined);
    try {
      const result = await api<ChainResp>(`/api/v1/derivatives/options/live-chain/${encodeURIComponent(underlying)}?expiry=${encodeURIComponent(expiry)}`, { signal: controller.signal });
      if (controller.signal.aborted) return;
      setChain(result);
      dispatchSurface({ type: "context", surface: "derivatives_chain", context: result.context });
    } catch (e) {
      if (!controller.signal.aborted) {
        setChainErr(formatApiError(e));
        dispatchSurface({ type: "error", surface: "derivatives_chain", error: formatApiError(e) });
      }
    } finally {
      if (chainAbort.current === controller) setLoading(false);
    }
  }

  async function loadScenario(e: FormEvent) {
    e.preventDefault(); setScenErr(""); setScenLoading(true); setScenData(undefined);
    try {
      const body = { spot: Number(scenForm.spot), strike: Number(scenForm.strike), days_to_expiry: Number(scenForm.days), volatility: Number(scenForm.vol) / 100, risk_free_rate: Number(scenForm.rate) / 100, option_type: scenForm.type, underlying_type: scenForm.underlying };
      setScenData(await api<ScenarioResp>("/api/v1/derivatives/options/scenarios", { method: "POST", body: JSON.stringify(body) }));
    } catch (e) { setScenErr(formatApiError(e)); }
    finally { setScenLoading(false); }
  }

  async function loadFutures(event: FormEvent) {
    event.preventDefault();
    setFutErr(""); setFutLoading(true); setFutData(undefined);
    try {
      const body = { spot_price: Number(futForm.spot), futures_price: Number(futForm.futures), days_to_expiry: Number(futForm.days), open_interest: Number(futForm.oi), previous_open_interest: Number(futForm.previousOi), previous_futures_price: Number(futForm.previousFutures), annual_risk_free_rate: Number(futForm.rate) / 100, annual_carry_yield: Number(futForm.carry) / 100 };
      const result = await api<FuturesResp>("/api/v1/derivatives/futures/analyse", { method: "POST", body: JSON.stringify(body) });
      setFutData(result);
    } catch (e) { setFutErr(formatApiError(e)); dispatchSurface({ type: "error", surface: "derivatives_futures", error: formatApiError(e) }); }
    finally { setFutLoading(false); }
  }

  async function loadTermStructure(event: FormEvent) {
    event.preventDefault();
    setTsErr(""); setTsLoading(true); setTsData(undefined);
    try {
      const body = {
        spot_price: Number(tsForm.spot),
        contracts: tsContracts.map(contract => ({
          label: contract.label,
          days_to_expiry: Number(contract.days_to_expiry),
          futures_price: Number(contract.futures_price),
          open_interest: contract.open_interest ? Number(contract.open_interest) : null,
        })),
        annual_risk_free_rate: Number(tsForm.rate) / 100,
        annual_carry_yield: Number(tsForm.carry) / 100,
      };
      const result = await api<TermStructureResp>("/api/v1/derivatives/futures/term-structure", { method: "POST", body: JSON.stringify(body) });
      setTsData(result);
    } catch (e) { setTsErr(formatApiError(e)); dispatchSurface({ type: "error", surface: "derivatives_futures", error: formatApiError(e) }); }
    finally { setTsLoading(false); }
  }

  function updateTermContract(index: number, patch: Partial<TermContractForm>) {
    setTsContracts(contracts => contracts.map((contract, position) => position === index ? { ...contract, ...patch } : contract));
  }

  useEffect(() => {
    void loadExpiries();
    return () => expiriesAbort.current?.abort();
  }, [underlying]);
  useEffect(() => {
    if (expiry) void loadChain();
    else {
      chainAbort.current?.abort();
      setChain(undefined);
      setLoading(false);
    }
    return () => chainAbort.current?.abort();
  }, [underlying, expiry]);

  const tabs: [Tab, string, typeof Activity][] = [["chain", "Option Chain", Activity], ["scenario", "Scenario Lab", Calculator], ["futures", "Futures Basis", TrendingUp], ["term", "Term Structure", Activity], ["payoff", "Payoff", Layers3]];

  return (
    <TerminalShell>
      <div className="mb-5">
        <div className="flex items-center gap-2 text-secondary"><Activity size={17}/><span className="text-[10px] uppercase tracking-[.2em]">Derivatives intelligence</span></div>
        <h1 className="mt-1 text-2xl font-semibold text-white">Derivatives Lab</h1>
        <p className="mt-1 text-sm text-slate-500">Research context: {underlying} · {timeframe}. Futures and options outputs are analytical scenarios, not validated forecasts or trade signals.</p>
      </div>

      {/* Tabs */}
      <div className="mb-4 flex max-w-full gap-1 overflow-x-auto rounded-lg border border-slate-800 bg-terminal-900 p-1 sm:w-fit" role="tablist">
        {tabs.map(([k, label, Icon]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)} className={`flex min-h-11 items-center gap-1.5 rounded-md px-3 py-2 text-xs font-medium transition ${tab === k ? "bg-accent/15 text-accent" : "text-slate-500 hover:text-slate-300"}`}><Icon aria-hidden="true" size={13}/>{label}</button>
        ))}
      </div>

      {/* Chain tab */}
      {tab === "chain" && <section>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <Select value={underlying} onChange={e => setSelectedSymbol(e.target.value)}><option>NIFTY 50</option><option>NIFTY BANK</option>{!["NIFTY 50", "NIFTY BANK"].includes(underlying) && <option>{underlying}</option>}</Select>
          <Select value={expiry} onChange={e => setExpiry(e.target.value)}><option value="">Select expiry</option>{expiries.map(x => <option key={x}>{x}</option>)}</Select>
          <Button variant="ghost" onClick={loadChain} disabled={!expiry || loading} className="gap-2 text-xs h-auto py-1.5 px-2"><RefreshCw size={12} className={loading ? "animate-spin" : ""}/>Refresh</Button>
          {chain && <div className="ml-auto flex flex-wrap gap-3 text-[10px] text-slate-500"><span>Source: {chain.source}</span>{chain.fetched_at && <span>As of: {new Date(chain.fetched_at).toLocaleString()}</span>}<span className={chain.is_stale ? "text-warning" : chain.is_live ? "text-gain" : "text-loss"}>{chain.is_stale ? "Stale snapshot" : chain.context?.fallback_used ? "Fallback snapshot" : chain.is_live ? "Live snapshot" : "Unavailable"}</span></div>}
        </div>
        {chainErr && <div className="mb-3 rounded border border-loss/30 bg-loss/5 p-3 text-xs text-loss">{chainErr}</div>}
        {chain?.message && <div className="mb-3 rounded border border-warning/30 bg-warning/5 p-3 text-xs text-warning">{chain.message}</div>}
        {loading ? <div className="space-y-2 rounded-xl border border-slate-800 bg-terminal-900/70 p-4"><div className="skeleton h-7 w-56 rounded"/><div className="skeleton h-[480px] w-full rounded-xl"/></div>
          : <OptionChainTable rows={chain?.rows || []} isLive={Boolean(chain?.is_live)}/>}
      </section>}

      {/* Scenario tab */}
      {tab === "scenario" && <div className="grid gap-4 xl:grid-cols-[420px_1fr]">
        <Card className="panel-glow"><CardHeader><CardTitle className="flex items-center gap-2"><Calculator size={15}/> Scenario Ladder</CardTitle></CardHeader><CardContent>
          <form onSubmit={loadScenario} className="space-y-2">
            <label className="block text-xs text-slate-400">Spot price</label>
            <Input type="number" step="any" value={scenForm.spot} onChange={e => setScenForm(f => ({ ...f, spot: e.target.value }))} placeholder="Spot" required/>
            <label className="block text-xs text-slate-400">Strike price</label>
            <Input type="number" step="any" value={scenForm.strike} onChange={e => setScenForm(f => ({ ...f, strike: e.target.value }))} placeholder="Strike" required/>
            <div className="grid grid-cols-2 gap-2">
              <div><label className="block text-xs text-slate-400">Days to expiry</label><Input type="number" value={scenForm.days} onChange={e => setScenForm(f => ({ ...f, days: e.target.value }))} required/></div>
              <div><label className="block text-xs text-slate-400">Implied volatility %</label><Input type="number" step="any" value={scenForm.vol} onChange={e => setScenForm(f => ({ ...f, vol: e.target.value }))} required/></div>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div><label className="block text-xs text-slate-400">Risk-free rate %</label><Input type="number" step="any" value={scenForm.rate} onChange={e => setScenForm(f => ({ ...f, rate: e.target.value }))}/></div>
              <div><label className="block text-xs text-slate-400">Option type</label><Select value={scenForm.type} onChange={e => setScenForm(f => ({ ...f, type: e.target.value }))}><option value="call">Call</option><option value="put">Put</option></Select></div>
            </div>
            <label className="block text-xs text-slate-400">Exercise</label>
            <Select value={scenForm.underlying} onChange={e => setScenForm(f => ({ ...f, underlying: e.target.value }))}><option value="index">European estimate (index)</option><option value="stock">American estimate (stock)</option></Select>
            <Button className="w-full" disabled={scenLoading}>{scenLoading ? "Computing…" : "Run scenarios"}</Button>
            {scenErr && <p className="text-xs text-loss">{scenErr}</p>}
          </form>
        </CardContent></Card>
        <Card><CardHeader><CardTitle>Analytical Scenario Output</CardTitle></CardHeader><CardContent>
          {scenData ? <div>
            <div className="mb-3 rounded-lg border border-info/25 bg-info/5 p-3 text-xs text-info"><b>Analytical scenario, not a validated forecast.</b> Values change directly with the supplied assumptions.</div>
            <div className="text-xs text-slate-500">{scenData.model_label} · {scenData.exercise_type}</div>
            <div className="mt-4 space-y-3">{scenData.scenarios.map(s => (
              <div key={s.label} className="rounded-lg border border-slate-800 p-3">
                <div className="flex items-center justify-between"><span className="text-sm font-medium text-white">{s.label}</span><span className="tabular text-sm text-accent">{inr(s.reference_price)}</span></div>
                <div className="mt-2 grid grid-cols-4 gap-2 text-[10px] text-slate-500">
                  {(["delta", "gamma", "theta_per_day", "vega_per_vol_point"] as const).map(g => <div key={g}><div className="uppercase">{g.replace(/_/g, " ")}</div><div className="mt-0.5 tabular text-xs text-slate-300">{s.greeks[g]}</div></div>)}
                </div>
                <p className="mt-2 text-xs text-slate-400">{s.note}</p>
              </div>
            ))}</div>
            <p className="mt-4 text-xs text-slate-500">{scenData.disclaimer}</p>
          </div> : <div className="grid min-h-52 place-items-center text-sm text-slate-600">Enter parameters to see a scenario ladder.</div>}
        </CardContent></Card>
      </div>}

      {/* Futures tab */}
      {tab === "futures" && <div className="grid gap-4 xl:grid-cols-[420px_1fr]">
        <Card className="panel-glow"><CardHeader><CardTitle className="flex items-center gap-2"><TrendingUp aria-hidden="true" size={15}/> Futures Basis Scenario</CardTitle></CardHeader><CardContent>
          <p className="mb-3 text-xs leading-5 text-slate-500">Enter a bounded contract snapshot for an analytical carry and open-interest scenario. Inputs are not fetched or validated against an exchange feed.</p>
          <form onSubmit={loadFutures} className="space-y-3">
            <div className="grid grid-cols-2 gap-2"><label className="text-xs text-slate-400">Spot price<Input type="number" min="0.01" step="any" required value={futForm.spot} onChange={e => setFutForm(f => ({...f, spot:e.target.value}))} className="mt-1"/></label><label className="text-xs text-slate-400">Futures price<Input type="number" min="0.01" step="any" required value={futForm.futures} onChange={e => setFutForm(f => ({...f, futures:e.target.value}))} className="mt-1"/></label></div>
            <div className="grid grid-cols-2 gap-2"><label className="text-xs text-slate-400">Previous futures<Input type="number" min="0.01" step="any" required value={futForm.previousFutures} onChange={e => setFutForm(f => ({...f, previousFutures:e.target.value}))} className="mt-1"/></label><label className="text-xs text-slate-400">Days to expiry<Input type="number" min="0" max="366" required value={futForm.days} onChange={e => setFutForm(f => ({...f, days:e.target.value}))} className="mt-1"/></label></div>
            <div className="grid grid-cols-2 gap-2"><label className="text-xs text-slate-400">Open interest<Input type="number" min="0.01" step="any" required value={futForm.oi} onChange={e => setFutForm(f => ({...f, oi:e.target.value}))} className="mt-1"/></label><label className="text-xs text-slate-400">Previous OI<Input type="number" min="0.01" step="any" required value={futForm.previousOi} onChange={e => setFutForm(f => ({...f, previousOi:e.target.value}))} className="mt-1"/></label></div>
            <div className="grid grid-cols-2 gap-2"><label className="text-xs text-slate-400">Risk-free rate %<Input type="number" step="any" value={futForm.rate} onChange={e => setFutForm(f => ({...f, rate:e.target.value}))} className="mt-1"/></label><label className="text-xs text-slate-400">Carry yield %<Input type="number" step="any" value={futForm.carry} onChange={e => setFutForm(f => ({...f, carry:e.target.value}))} className="mt-1"/></label></div>
            <Button type="submit" disabled={futLoading} className="w-full gap-2"><DollarSign aria-hidden="true" size={14}/>{futLoading ? "Calculating" : "Run analytical scenario"}</Button>
          </form>
          {futErr && <p role="alert" className="mt-2 text-xs text-loss">{futErr}</p>}
        </CardContent></Card>
        <Card><CardHeader><CardTitle>Analytical Scenario Output</CardTitle></CardHeader><CardContent>
          {futData ? <div>
            <div className="rounded-lg border border-info/25 bg-info/5 p-3 text-xs leading-5 text-info"><b>Analytical scenario, not a validated forecast.</b> {futData.data_status}</div>
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              {([["Underlying spot", inr(futData.spot_price)], ["Futures price", inr(futData.futures_price)], ["Basis", `${inr(futData.basis)} (${pct(futData.basis_pct)})`], ["Theoretical fair value", inr(futData.theoretical_fair_value)], ["Annualized basis", pct(futData.annualized_basis_pct)], ["Fair-value difference", pct(futData.fair_value_mispricing_pct)], ["Open-interest change", pct(futData.change_in_open_interest_pct)], ["Price change", pct(futData.futures_price_change_pct)]] as const).map(([l, v]) => (
                <div key={l} className="rounded-lg border border-slate-800 p-3"><div className="text-[10px] uppercase text-slate-600">{l}</div><div className="mt-1 tabular text-sm text-white">{v}</div></div>
              ))}
            </div>
            <div className="mt-4 text-sm font-semibold text-slate-200">Classification: {futData.classification}</div>
            <p className="mt-4 text-sm leading-5 text-slate-400">{futData.interpretation}</p>
            <p className="mt-3 text-xs text-slate-500">Scenario reference only. Not a validated forecast or trade recommendation.</p>
          </div> : <div className="grid min-h-52 place-items-center text-center text-sm text-slate-600">Enter contract inputs to calculate an analytical scenario.</div>}
        </CardContent></Card>
      </div>}

      {/* Term structure tab */}
      {tab === "term" && <div className="grid gap-4 xl:grid-cols-[420px_1fr]">
        <Card className="panel-glow"><CardHeader><CardTitle className="flex items-center gap-2"><Layers3 aria-hidden="true" size={15}/> Term Structure</CardTitle></CardHeader><CardContent>
          <p className="mb-3 text-xs leading-5 text-slate-500">Compare near, next, and far quotes. Positive long-roll cost means the deferred contract costs more than the contract being closed.</p>
          <form onSubmit={loadTermStructure} className="space-y-3">
            <label className="block text-xs text-slate-400">Underlying spot<Input type="number" min="0.01" step="any" required value={tsForm.spot} onChange={e => setTsForm(form => ({ ...form, spot: e.target.value }))} className="mt-1"/></label>
            <div className="grid grid-cols-2 gap-2"><label className="text-xs text-slate-400">Annual risk-free rate %<Input type="number" step="any" value={tsForm.rate} onChange={e => setTsForm(f => ({...f, rate:e.target.value}))} className="mt-1"/></label><label className="text-xs text-slate-400">Annual carry yield %<Input type="number" step="any" value={tsForm.carry} onChange={e => setTsForm(f => ({...f, carry:e.target.value}))} className="mt-1"/></label></div>
            {tsContracts.map((contract, index) => <fieldset key={index} className="rounded-lg border border-slate-800 p-3">
              <legend className="px-1 text-[10px] uppercase tracking-wider text-slate-500">Contract {index + 1}</legend>
              <label className="block text-xs text-slate-400">Label<Input required maxLength={40} value={contract.label} onChange={event => updateTermContract(index, { label: event.target.value })} className="mt-1"/></label>
              <div className="mt-2 grid grid-cols-2 gap-2"><label className="text-xs text-slate-400">Days to expiry<Input type="number" min="0" max="730" required value={contract.days_to_expiry} onChange={event => updateTermContract(index, { days_to_expiry: event.target.value })} className="mt-1"/></label><label className="text-xs text-slate-400">Futures price<Input type="number" min="0.01" step="any" required value={contract.futures_price} onChange={event => updateTermContract(index, { futures_price: event.target.value })} className="mt-1"/></label></div>
              <label className="mt-2 block text-xs text-slate-400">Open interest (optional)<Input type="number" min="0" step="any" value={contract.open_interest} onChange={event => updateTermContract(index, { open_interest: event.target.value })} className="mt-1"/></label>
            </fieldset>)}
            <Button type="submit" disabled={tsLoading} className="w-full gap-2"><Layers3 aria-hidden="true" size={14}/>{tsLoading ? "Computing…" : "Compute term structure"}</Button>
          </form>
          {tsErr && <p role="alert" className="mt-2 text-xs text-loss">{tsErr}</p>}
        </CardContent></Card>
        <Card><CardHeader><CardTitle>Term Structure Output</CardTitle></CardHeader><CardContent>
          {tsData ? <div>
            <div className="rounded-lg border border-info/25 bg-info/5 p-3 text-xs leading-5 text-info"><b>Analytical term structure, not a validated forecast.</b> {tsData.data_status}</div>
            <div className="mt-4 flex flex-wrap items-end justify-between gap-3"><div><div className="text-[10px] uppercase tracking-wider text-slate-500">Curve state</div><div className={`mt-1 text-xl font-semibold capitalize ${tsData.curve_state === "contango" ? "text-warning" : tsData.curve_state === "backwardation" ? "text-gain" : "text-slate-200"}`}>{tsData.curve_state}</div></div><div className="text-right"><div className="text-[10px] uppercase tracking-wider text-slate-500">Near to far slope</div><div className="mt-1 tabular text-lg text-white">{pct(tsData.near_to_far_slope_pct)}</div></div></div>
            <div className="mt-4"><TermStructureChart contracts={tsData.contracts}/></div>
            <div className="mt-4 overflow-x-auto"><table className="w-full min-w-[660px] text-left text-xs"><thead className="text-[10px] uppercase text-slate-600"><tr><th className="py-2">Contract</th><th>Quote</th><th>Basis</th><th>Fair value</th><th>Vs fair value</th><th>OI share</th></tr></thead><tbody>{tsData.contracts.map(contract => <tr key={contract.label} className="border-t border-slate-800"><td className="py-2 font-medium text-white">{contract.label}<span className="ml-2 text-[10px] text-slate-600">{contract.days_to_expiry}d</span></td><td className="tabular text-slate-200">{inr(Number(contract.futures_price))}</td><td className="tabular">{pct(contract.basis_pct)}</td><td className="tabular">{inr(contract.theoretical_fair_value)}</td><td className="tabular">{pct(contract.fair_value_difference_pct)}</td><td className="tabular">{contract.open_interest_share_pct == null ? "—" : pct(contract.open_interest_share_pct)}</td></tr>)}</tbody></table></div>
            <div className="mt-4 space-y-2">{tsData.rollovers.map(rollover => <div key={`${rollover.from_label}-${rollover.to_label}`} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-800 p-3 text-xs"><span className="text-slate-300">{rollover.from_label} → {rollover.to_label}<span className="ml-2 capitalize text-slate-500">{rollover.state}</span></span><span className="tabular text-white">Long roll {inr(rollover.long_roll_cost_points)} · {pct(rollover.long_roll_cost_pct)}</span></div>)}</div>
            <div className="mt-4 space-y-1">{tsData.warnings.map(warning => <p key={warning.code} className="text-[11px] leading-4 text-warning">{warning.message}</p>)}</div>
          </div> : <div className="grid min-h-52 place-items-center text-center text-sm text-slate-600">Enter spot and futures prices for multiple expiries.</div>}
        </CardContent></Card>
      </div>}

      {tab === "payoff" && <div className="grid gap-4 xl:grid-cols-[420px_1fr]">
        <Card className="panel-glow"><CardHeader><CardTitle className="flex items-center gap-2"><Layers3 aria-hidden="true" size={15}/> Expiry Payoff Builder</CardTitle></CardHeader><CardContent>
          <p className="mb-3 text-xs leading-5 text-slate-500">Enter the legs and the premiums you actually paid or received. The result is expiry arithmetic on your own inputs, not a forecast or a recommendation, and margin is not shown because this app does not hold broker risk parameters.</p>
          <form onSubmit={loadPayoff} className="space-y-3">
            <label className="block text-xs text-slate-400">Underlying spot<Input type="number" min="0.01" step="any" required aria-label="Underlying spot" value={payoffSpot} onChange={e => setPayoffSpot(e.target.value)} className="mt-1"/></label>
            {payoffLegs.map((leg, i) => (
              <div key={`payoff-leg-${i}`} className="space-y-2 rounded-lg border border-slate-800 p-2">
                <div className="grid grid-cols-2 gap-2">
                  <label className="text-xs text-slate-400">Type<Select aria-label={`Leg ${i + 1} type`} value={leg.type} onChange={e => updateLeg(i, { type: e.target.value })} className="mt-1"><option value="call">Call</option><option value="put">Put</option><option value="future">Future</option></Select></label>
                  <label className="text-xs text-slate-400">Side<Select aria-label={`Leg ${i + 1} side`} value={leg.side} onChange={e => updateLeg(i, { side: e.target.value })} className="mt-1"><option value="buy">Buy</option><option value="sell">Sell</option></Select></label>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <label className="text-xs text-slate-400">Strike<Input type="number" step="any" disabled={leg.type === "future"} aria-label={`Leg ${i + 1} strike`} value={leg.strike} onChange={e => updateLeg(i, { strike: e.target.value })} className="mt-1"/></label>
                  <label className="text-xs text-slate-400">Premium paid<Input type="number" step="any" required aria-label={`Leg ${i + 1} premium`} value={leg.premium} onChange={e => updateLeg(i, { premium: e.target.value })} className="mt-1"/></label>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <label className="text-xs text-slate-400">Lots<Input type="number" min="1" step="1" required aria-label={`Leg ${i + 1} lots`} value={leg.quantity} onChange={e => updateLeg(i, { quantity: e.target.value })} className="mt-1"/></label>
                  <label className="text-xs text-slate-400">Lot size<Input type="number" min="1" step="1" required aria-label={`Leg ${i + 1} lot size`} value={leg.lot_size} onChange={e => updateLeg(i, { lot_size: e.target.value })} className="mt-1"/></label>
                </div>
                {payoffLegs.length > 1 && <Button type="button" variant="ghost" aria-label={`Remove leg ${i + 1}`} onClick={() => setPayoffLegs(legs => legs.filter((_, position) => position !== i))}>Remove leg</Button>}
              </div>
            ))}
            <Button type="button" variant="ghost" onClick={() => setPayoffLegs(legs => [...legs, { ...BLANK_LEG }])}>Add leg</Button>
            <Button type="submit" disabled={payoffLoading} className="w-full gap-2"><Calculator aria-hidden="true" size={14}/>{payoffLoading ? "Calculating" : "Plot payoff"}</Button>
          </form>
          {payoffErr && <p role="alert" className="mt-2 text-xs text-loss">{payoffErr}</p>}
        </CardContent></Card>
        <Card><CardHeader><CardTitle>Payoff at expiry</CardTitle></CardHeader><CardContent>
          {payoffData ? (
            <>
              <PayoffChart data={payoffData}/>
<MarginEstimateCard
                  margin={payoffData.margin as PayoffMargin | MarginEstimate | { state: "available" | "unavailable"; reason?: string; span_margin?: number; exposure_margin?: number; total_margin?: number; approximate_margin?: boolean; note?: string }}
                  breakevens={payoffData.breakevens}
                  live_pnl={payoffData.live_pnl}
                  disclosure="Margin estimates are approximate and non-official; this application does not hold broker SPAN/ELM risk parameters. Live P&L requires real-time option marks which are not available in this simulator."
                />
            </>
          ) : (
            <div className="grid min-h-52 place-items-center text-center text-sm text-slate-600">Enter legs to plot the expiry payoff.</div>
          )}
        </CardContent></Card>
      </div>}
    </TerminalShell>
  );
}
