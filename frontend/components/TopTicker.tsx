"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useMarket } from "@/components/MarketContext";
import { api, type Quote } from "@/lib/api";
import { pct } from "@/lib/utils";

export function TopTicker() {
  const { selectedSymbol, timeframe, dispatchSurface } = useMarket();
  const previous = useRef<number | undefined>(undefined);
  const [flash, setFlash] = useState("");
  const quote = useQuery({
    queryKey: ["header-ticker", selectedSymbol, timeframe],
    queryFn: () => api<Quote>(`/api/v1/market/quote/${encodeURIComponent(selectedSymbol)}?timeframe=${timeframe}`),
    refetchInterval: 5000,
    retry: 1,
  });

  useEffect(() => {
    if (quote.data?.context) dispatchSurface({ type: "context", surface: "quote", context: quote.data.context });
    else if (quote.error) dispatchSurface({ type: "error", surface: "quote", error: (quote.error as Error).message });
  }, [quote.data, quote.error, dispatchSurface]);

  useEffect(() => {
    const price = quote.data?.price;
    if (price == null) return;
    if (previous.current != null && previous.current !== price) {
      setFlash(price > previous.current ? "price-flash-up" : "price-flash-down");
      const timer = setTimeout(() => setFlash(""), 600);
      previous.current = price;
      return () => clearTimeout(timer);
    }
    previous.current = price;
  }, [quote.data?.price]);

  const value = quote.data;
  const up = (value?.change_pct || 0) > 0;
  const down = (value?.change_pct || 0) < 0;
  const Icon = up ? ArrowUpRight : down ? ArrowDownRight : Minus;
  return (
    <div className={`hidden min-w-0 flex-1 items-center gap-2 overflow-hidden rounded-md px-1.5 py-1 text-xs xl:flex ${flash}`}>
      <span className="shrink-0 font-semibold text-slate-400">{selectedSymbol}</span>
      <span className="shrink-0 rounded border border-slate-800 px-1 text-[9px] text-slate-500">{timeframe}</span>
      {quote.error ? (
        <span className="truncate text-loss">Unavailable</span>
      ) : quote.isLoading ? (
        <span className="skeleton h-4 w-24 rounded" />
      ) : (
        <>
          <span className="tabular text-slate-100">{value?.price?.toLocaleString("en-IN", { maximumFractionDigits: 2 }) ?? "Unavailable"}</span>
          {value?.change_pct != null && <span className={`inline-flex items-center gap-0.5 tabular ${up ? "signal-up" : down ? "signal-down" : "text-slate-500"}`}><Icon size={11} />{pct(value.change_pct)}</span>}
          <span className="truncate text-[9px] uppercase text-slate-600">{value?.context?.provider || value?.source}</span>
        </>
      )}
    </div>
  );
}
