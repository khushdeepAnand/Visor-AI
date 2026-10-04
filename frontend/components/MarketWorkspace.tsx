"use client";

import { FormEvent, useEffect, useState, type KeyboardEvent, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Mosaic, MosaicWindow, type MosaicNode } from "react-mosaic-component";
import { TerminalChart } from "@/components/TerminalChart";
import { ForecastCard } from "@/components/ForecastCard";
import { IndicatorsPanel } from "@/components/IndicatorsPanel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { api, fetchModelQuality, type Forecast, type Quote } from "@/lib/api";
import { useAuth, useSessionReady } from "@/lib/auth";
import { inr } from "@/lib/utils";
import { MarketSourceLabel, MixedSourceNotice, useMarket } from "@/components/MarketContext";
import { WidgetErrorBoundary } from "@/components/WidgetErrorBoundary";

export type MarketPanelId = "chart" | "forecast" | "indicators" | "depth" | "paper" | "watchlist";

type WatchlistItem = { id: number; symbol: string; price?: number; change_pct?: number };
type WorkspaceResponse = { workspace: string; layout: MosaicNode<MarketPanelId> | Record<string, never> };

const defaultLayout: MosaicNode<MarketPanelId> = {
  type: "split",
  direction: "row",
  splitPercentages: [70, 30],
  children: [
    "chart",
    {
      type: "split",
      direction: "column",
      splitPercentages: [25, 75],
      children: [
        "forecast",
        {
          type: "split",
          direction: "column",
          splitPercentages: [35, 65],
          children: [
            "indicators",
            {
              type: "split",
              direction: "column",
              splitPercentages: [50, 50],
              children: ["paper", { type: "split", direction: "row", splitPercentages: [50, 50], children: ["depth", "watchlist"] }],
            },
          ],
        },
      ],
    },
  ],
};

const titles: Record<MarketPanelId, string> = {
  chart: "Live chart + research range",
  forecast: "Forecast corridor",
  indicators: "Technical indicators",
  depth: "Quote / market depth",
  paper: "Paper simulation",
  watchlist: "Watchlist",
};

function DepthPanel({ quote }: { quote?: Quote }) {
  const { surfaceErrors } = useMarket();
  const bids = quote?.depth?.bids || [];
  const asks = quote?.depth?.asks || [];
  return (
    <div className="h-full overflow-auto bg-terminal-950 text-xs">
      <MarketSourceLabel context={quote?.context} />
      <div className="p-3">
      <div className="mb-3 grid grid-cols-2 gap-2">
        <div className="rounded border border-slate-800 p-2"><div className="text-[9px] uppercase text-slate-600">LTP</div><div className="tabular mt-1 text-base text-white">{inr(quote?.price)}</div></div>
        <div className="rounded border border-slate-800 p-2"><div className="text-[9px] uppercase text-slate-600">Source</div><div className="mt-1 truncate text-slate-300">{quote?.source || "—"}</div></div>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div><div className="mb-1 text-[9px] uppercase tracking-wider text-gain">Bids</div>{bids.slice(0, 5).map(([price, qty], index) => <div key={`${price}-${index}`} className="flex justify-between border-t border-slate-900 py-1"><span className="tabular text-gain">{price.toFixed(2)}</span><span className="tabular text-slate-500">{qty}</span></div>)}</div>
        <div><div className="mb-1 text-[9px] uppercase tracking-wider text-loss">Asks</div>{asks.slice(0, 5).map(([price, qty], index) => <div key={`${price}-${index}`} className="flex justify-between border-t border-slate-900 py-1"><span className="tabular text-loss">{price.toFixed(2)}</span><span className="tabular text-slate-500">{qty}</span></div>)}</div>
      </div>
      {!bids.length && !asks.length && <p className="mt-3 leading-5 text-slate-600">Depth is shown when the active broker/provider exposes it. The app does not invent order-book levels.</p>}
      {surfaceErrors.quote && <p className="mt-3 text-loss">Quote unavailable — {surfaceErrors.quote}</p>}
      </div>
    </div>
  );
}

function QuickOrder() {
  const { selectedSymbol, timeframe, sourceStatus, surfaceContexts } = useMarket();
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const sessionReady = useSessionReady();
  const mutation = useMutation({ mutationFn: (body: unknown) => api<{ status: string; fill_price: number; simulation_notice: string }>("/api/v1/paper/orders", { method: "POST", body: JSON.stringify(body) }) });
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setMessage(""); setError("");
    const form = new FormData(event.currentTarget);
    try {
      const result = await mutation.mutateAsync({ symbol: selectedSymbol, side: form.get("side"), quantity: Number(form.get("quantity")), order_type: form.get("order_type"), instrument_type: surfaceContexts.quote?.instrument_type || "EQUITY", reasoning_notes: `Quick order from market workspace (${timeframe}, ${sourceStatus.provider || "source pending"})` });
      setMessage(`${result.status}${result.fill_price ? ` @ ${inr(result.fill_price)}` : ""}`);
    } catch (reason) { setError((reason as Error).message); }
  }
  if (!sessionReady) return <div className="h-full bg-terminal-950 p-3 text-xs leading-5 text-slate-500">Sign in to use the intentional paper-simulation ticket. Research ranges never prefill an action.</div>;
  return <div className="h-full overflow-auto bg-terminal-950"><MarketSourceLabel context={surfaceContexts.quote} /><div className="p-3"><form className="space-y-2" onSubmit={submit}><div className="flex items-center justify-between gap-2"><div className="tabular text-sm font-semibold text-white">{selectedSymbol}</div><span className="rounded border border-slate-800 px-1.5 py-0.5 text-[9px] text-slate-500">{timeframe}</span></div><div className="text-[10px] text-slate-600">{sourceStatus.label}</div><label className="block text-[11px] text-slate-400">Simulation side<Select name="side" defaultValue="" required className="mt-1 w-full"><option value="" disabled>Choose a side</option><option>BUY</option><option>SELL</option></Select></label><label className="block text-[11px] text-slate-400">Order type<Select name="order_type" className="mt-1 w-full"><option>MARKET</option><option>LIMIT</option></Select></label><label className="block text-[11px] text-slate-400">Hypothetical quantity<Input name="quantity" type="number" min="1" step="1" placeholder="Enter quantity" required className="mt-1"/></label><Button className="w-full" disabled={mutation.isPending}>Simulate order</Button></form>{message && <p className="mt-2 text-xs text-gain">{message}</p>}{error && <p role="alert" className="mt-2 text-xs text-loss">{error}</p>}<p className="mt-3 text-[10px] leading-4 text-slate-600">Simulation only. No value from forecasting selects a side, quantity, or order price.</p></div></div>;
}

function WatchlistPanel() {
  const queryClient = useQueryClient();
  const sessionReady = useSessionReady();
  const query = useQuery({ queryKey: ["market-workspace-watchlist"], queryFn: () => api<{ items: WatchlistItem[] }>("/api/v1/watchlist"), retry: false, enabled: sessionReady });
  const remove = useMutation({ mutationFn: (id: number) => api(`/api/v1/watchlist/${id}`, { method: "DELETE" }), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["market-workspace-watchlist"] }) });
  if (!sessionReady) return <div className="h-full bg-terminal-950 p-3 text-xs text-slate-600">Sign in to sync your watchlist.</div>;
  if (query.error) return <div className="h-full bg-terminal-950 p-3 text-xs text-slate-600">Watchlist is unavailable right now.</div>;
  return <div className="h-full overflow-auto bg-terminal-950 p-2 text-xs">{query.data?.items?.map((item) => <div key={item.id} className="flex min-h-11 items-center justify-between border-b border-slate-900 px-1 py-2"><span className="tabular text-slate-200">{item.symbol}</span><button className="min-h-11 px-2 text-[10px] text-slate-600 hover:text-loss" onClick={() => remove.mutate(item.id)}>remove</button></div>)}{!query.data?.items?.length && <div className="p-2 text-slate-600">Your saved symbols will appear here.</div>}</div>;
}

export function MarketWorkspace({ quote, forecast, onForecast }: { quote?: Quote; forecast?: Forecast; onForecast: (value: Forecast | undefined) => void }) {
  const { selectedSymbol, timeframe, surfaceErrors } = useMarket();
  const storageKey = `stockpilot-market-layout:${selectedSymbol}`;
  const [layout, setLayout] = useState<MosaicNode<MarketPanelId> | null>(defaultLayout);
  const [mobilePanel, setMobilePanel] = useState<MarketPanelId>("indicators");
  const [viewMode, setViewMode] = useState<"calm" | "pro">("calm");
  const [proAvailable, setProAvailable] = useState(false);
  const [retryNonce, setRetryNonce] = useState(0);
  const sessionReady = useSessionReady();
  const { isAdmin } = useAuth();
  const modelQuality = useQuery({
    queryKey: ["model-quality", selectedSymbol],
    queryFn: () => fetchModelQuality(selectedSymbol),
    enabled: isAdmin && sessionReady,
    refetchInterval: 5 * 60_000,
    retry: false,
  });
  const retrainedAt = modelQuality.data?.last_refreshed?.[selectedSymbol]?.last_refreshed ?? null;

  useEffect(() => {
    const cached = window.localStorage.getItem(storageKey);
    if (cached) {
      try { setLayout(JSON.parse(cached) as MosaicNode<MarketPanelId>); } catch { /* ignore invalid local layout */ }
    }
    if (!sessionReady) return;
    api<WorkspaceResponse>(`/api/v1/workspaces/${encodeURIComponent(`market-${selectedSymbol}`)}`)
      .then((response) => {
        if (response.layout && Object.keys(response.layout).length) setLayout(response.layout as MosaicNode<MarketPanelId>);
      })
      .catch(() => undefined);
  }, [storageKey, selectedSymbol, sessionReady]);

  useEffect(() => {
    setViewMode(window.localStorage.getItem("stockpilot-market-view") === "pro" ? "pro" : "calm");
    const query = window.matchMedia?.("(min-width: 1024px)");
    if (!query) return;
    const update = () => setProAvailable(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  const persist = (next: MosaicNode<MarketPanelId> | null) => {
    setLayout(next);
    if (next) window.localStorage.setItem(storageKey, JSON.stringify(next));
    if (sessionReady) api(`/api/v1/workspaces/${encodeURIComponent(`market-${selectedSymbol}`)}`, { method: "PUT", body: JSON.stringify({ layout: next || {} }) }).catch(() => undefined);
  };

  const panels: Record<MarketPanelId, ReactNode> = {
    chart: <TerminalChart key={retryNonce} onForecast={onForecast} />,
    forecast: <div className="h-full overflow-auto bg-terminal-950 p-2"><ForecastCard data={forecast} loading={!forecast && !surfaceErrors.forecast} error={surfaceErrors.forecast ? `Forecast unavailable: ${surfaceErrors.forecast}` : undefined} onRetry={() => setRetryNonce((value) => value + 1)} retrainedAt={retrainedAt} /></div>,
    indicators: <IndicatorsPanel symbol={selectedSymbol} timeframe={timeframe} />,
    depth: <DepthPanel quote={quote} />,
    paper: <QuickOrder />,
    watchlist: <WatchlistPanel />,
  };

  const detailIds: MarketPanelId[] = ["indicators", "depth", "paper", "watchlist"];
  const effectivePro = viewMode === "pro" && proAvailable;
  const setMode = (mode: "calm" | "pro") => {
    setViewMode(mode);
    window.localStorage.setItem("stockpilot-market-view", mode);
  };
  const moveDetailFocus = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (!(["ArrowLeft", "ArrowRight", "Home", "End"] as string[]).includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? 0 : event.key === "End" ? detailIds.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + detailIds.length) % detailIds.length;
    setMobilePanel(detailIds[next]);
    document.getElementById(`market-tab-${detailIds[next]}`)?.focus();
  };

  const detailTabs = (
    <>
      <div className="mb-2 mt-3 flex gap-1 overflow-x-auto rounded-lg border border-slate-800 bg-terminal-900/80 p-1" role="tablist" aria-label="Market details">
        {detailIds.map((id, index) => <button id={`market-tab-${id}`} key={id} role="tab" aria-controls={`market-panel-${id}`} tabIndex={mobilePanel===id?0:-1} aria-selected={mobilePanel===id} onKeyDown={(event) => moveDetailFocus(event, index)} onClick={()=>setMobilePanel(id)} className={`interactive-surface min-h-11 shrink-0 rounded-md px-3 py-2 text-[10px] font-semibold uppercase tracking-wide ${mobilePanel===id?"bg-accent/12 text-accent":"text-slate-500"}`}>{titles[id]}</button>)}
      </div>
      <div id={`market-panel-${mobilePanel}`} role="tabpanel" aria-labelledby={`market-tab-${mobilePanel}`} className="min-h-[360px] overflow-hidden rounded-xl border border-slate-800 bg-terminal-900 shadow-panel sm:min-h-[420px]"><WidgetErrorBoundary key={mobilePanel} title={`${titles[mobilePanel]} unavailable`}>{panels[mobilePanel]}</WidgetErrorBoundary></div>
    </>
  );

  return (
    <>
      <MixedSourceNotice />
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-800 bg-terminal-900/70 px-3 py-2">
        <div><div className="text-xs font-semibold text-slate-200">Workspace view</div><p className="text-[11px] text-slate-500">Calm keeps the chart and corridor in sequence. Pro restores the movable mosaic.</p></div>
        <div className="grid min-h-11 grid-cols-2 rounded-lg border border-slate-700 bg-terminal-950 p-1" aria-label="Workspace view mode">
          <button type="button" aria-pressed={viewMode === "calm"} onClick={() => setMode("calm")} className={`min-w-20 rounded-md px-3 text-xs font-semibold ${viewMode === "calm" ? "bg-accent/15 text-accent" : "text-slate-500"}`}>Calm</button>
          <button type="button" aria-pressed={effectivePro} disabled={!proAvailable} title={proAvailable ? "Open the movable panel mosaic" : "Pro mosaic is available from 1024px"} onClick={() => setMode("pro")} className={`min-w-20 rounded-md px-3 text-xs font-semibold disabled:cursor-not-allowed disabled:opacity-45 ${effectivePro ? "bg-secondary/15 text-secondary" : "text-slate-500"}`}>Pro mosaic</button>
        </div>
      </div>

      {!effectivePro ? <div>
        <div className="grid gap-3 xl:grid-cols-[minmax(0,1.35fr)_minmax(360px,.65fr)] xl:items-start">
          <div data-testid="market-chart-panel" className="min-h-[560px] overflow-hidden rounded-xl border border-slate-800 bg-terminal-900 shadow-panel"><WidgetErrorBoundary title="Chart unavailable">{panels.chart}</WidgetErrorBoundary></div>
          <div data-testid="forecast-corridor-panel" className="overflow-hidden rounded-xl border border-slate-800 bg-terminal-900 shadow-panel"><WidgetErrorBoundary title="Corridor unavailable">{panels.forecast}</WidgetErrorBoundary></div>
        </div>
        {detailTabs}
      </div> : <div className="stockpilot-mosaic h-[760px] min-h-[600px] overflow-hidden rounded-xl border border-slate-800 bg-terminal-900 shadow-panel">
        <Mosaic<MarketPanelId>
          value={layout}
          onChange={setLayout}
          onRelease={persist}
          renderTile={(id, path) => (
            <MosaicWindow<MarketPanelId> path={path} title={titles[id]}>
              <WidgetErrorBoundary key={id} title={`${titles[id]} unavailable`}>{panels[id]}</WidgetErrorBoundary>
            </MosaicWindow>
          )}
        />
      </div>}
    </>
  );
}
