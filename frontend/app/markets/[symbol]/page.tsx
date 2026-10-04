"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { MarketStatus } from "@/components/MarketStatus";
import { MarketWorkspace } from "@/components/MarketWorkspace";
import { TerminalShell } from "@/components/TerminalShell";
import { API_BASE, api, formatApiError, type Forecast, type Quote } from "@/lib/api";
import { inr, pct } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { useMarket } from "@/components/MarketContext";

export default function MarketPage() {
  const params = useParams<{ symbol: string }>();
  const routeSymbol = decodeURIComponent(params.symbol || "NIFTY 50");
  const { selectedSymbol, setSelectedSymbol, timeframe, dispatchSurface } = useMarket();
  const [forecast, setForecast] = useState<Forecast>();
  const saveResearch = useMutation({
    mutationFn: () => api<{ prediction_id: number }>(`/api/v1/predictions/${encodeURIComponent(selectedSymbol)}/save?training_window=1y&timeframe=${timeframe}&confidence=.8`, { method: "POST" }),
  });
  const quote = useQuery({
    queryKey: ["quote", selectedSymbol, timeframe],
    queryFn: () => api<Quote>(`/api/v1/market/quote/${encodeURIComponent(selectedSymbol)}?timeframe=${timeframe}`),
    refetchInterval: 2500,
    enabled: selectedSymbol === routeSymbol,
  });

  useEffect(() => {
    if (selectedSymbol !== routeSymbol) setSelectedSymbol(routeSymbol);
  }, [routeSymbol, selectedSymbol, setSelectedSymbol]);

  useEffect(() => {
    setForecast(undefined);
  }, [selectedSymbol, timeframe]);

  useEffect(() => {
    if (quote.data?.context) dispatchSurface({ type: "context", surface: "quote", context: quote.data.context });
    else if (quote.error) dispatchSurface({ type: "error", surface: "quote", error: (quote.error as Error).message });
  }, [quote.data, quote.error, dispatchSurface]);

  function downloadForecastPdf() {
    const url = `${API_BASE}/api/v1/reports/forecast/${encodeURIComponent(selectedSymbol)}.pdf?training_window=1y&timeframe=${timeframe}&confidence=.8`;
    window.open(url, "_blank", "noopener,noreferrer");
  }

  if (selectedSymbol !== routeSymbol) {
    return <TerminalShell><div className="skeleton h-24 rounded-xl" /></TerminalShell>;
  }

  const quoteContext = quote.data?.context;
  const exchange = quoteContext?.exchange || "NSE / BSE";
  const instrumentType = quoteContext?.instrument_type;

  return (
    <TerminalShell>
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-3"><h1 className="tabular text-2xl font-semibold text-white">{selectedSymbol}</h1><span className="rounded border border-slate-800 px-2 py-1 text-[10px] text-slate-500">{exchange}{instrumentType ? ` / ${instrumentType}` : ""} · {timeframe}</span></div>
          <div className="mt-2 flex flex-wrap items-baseline gap-3">{quote.error ? <span className="text-sm font-medium text-loss">Quote unavailable</span> : <span className="tabular text-xl">{inr(quote.data?.price)}</span>}{quote.data?.change_pct != null && <span className={`tabular text-sm ${quote.data.change_pct >= 0 ? "signal-up" : "signal-down"}`}>{pct(quote.data.change_pct)}</span>}<span className="text-xs text-slate-600">{quoteContext?.provider || quote.data?.source}</span>{(quoteContext?.is_stale || quote.data?.is_stale) && <span className="rounded bg-amber-500/10 px-2 py-0.5 text-[10px] text-amber-300">STALE CACHE</span>}{quoteContext?.fallback_used && <span className="text-xs text-amber-300">Fallback: {quoteContext.fallback_reason || "primary provider unavailable"}</span>}</div>
        </div>
         <div className="flex flex-wrap items-center gap-2"><Button variant="ghost" disabled={!forecast || saveResearch.isPending || saveResearch.isSuccess} onClick={() => saveResearch.mutate()}>{saveResearch.isSuccess ? "Research saved" : saveResearch.isPending ? "Saving" : "Save research"}</Button><Button variant="ghost" onClick={downloadForecastPdf}>Corridor PDF</Button><MarketStatus /></div>
       </div>
       {saveResearch.isError && <div role="alert" className="mb-3 rounded border border-warning/30 bg-warning/5 p-3 text-xs text-warning">{formatApiError(saveResearch.error)}</div>}
       <MarketWorkspace quote={quote.data} forecast={forecast} onForecast={setForecast} />
    </TerminalShell>
  );
}
