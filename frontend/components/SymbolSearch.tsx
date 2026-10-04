"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowRight, Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useMarket } from "@/components/MarketContext";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";

type Instrument = { symbol: string; name: string; exchange: string; instrument_type: string };
const destinations = [
  ["/", "Home", "Overview and market pulse"], ["/compare", "Compare", "Compare instruments"],
  ["/derivatives", "Derivatives", "Options and futures scenarios"], ["/watchlist", "Watchlist", "Saved instruments"],
  ["/alerts", "Alerts", "Price and corridor alerts"], ["/risk", "Risk Lab", "Portfolio stress tests"],
  ["/track-record", "Track Record", "Settled calibration evidence"], ["/system", "System", "Provider health"],
  ["/account", "Account", "Sessions and identity"],
] as const;

export function SymbolSearch() {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Instrument[]>([]);
  const router = useRouter();
  const input = useRef<HTMLInputElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const { setSelectedSymbol } = useMarket();
  const { isAdmin } = useAuth();
  const routes = isAdmin ? [...destinations, ["/admin", "Admin", "Models and operations"] as const] : destinations;
  const routeMatches = routes.filter(([, label, detail]) => `${label} ${detail}`.toLowerCase().includes(q.toLowerCase())).slice(0, 5);

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing = target?.matches("input, textarea, select, [contenteditable=true]");
      if (((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") || (event.key === "/" && !typing)) {
        event.preventDefault(); setOpen(true); setTimeout(() => input.current?.focus(), 0);
      }
      if (event.key === "Escape" && open) { setOpen(false); trigger.current?.focus(); }
    };
    addEventListener("keydown", handler); return () => removeEventListener("keydown", handler);
  }, [open]);

  useEffect(() => {
    if (!open || q.length < 1) { setItems([]); return; }
    const timer = setTimeout(() => api<{ results: Instrument[] }>(`/api/v1/market/search?q=${encodeURIComponent(q)}&limit=10`).then((value) => setItems(value.results)).catch(() => setItems([])), 140);
    return () => clearTimeout(timer);
  }, [q, open]);

  const navigate = (href: string) => { setOpen(false); setQ(""); router.push(href); };
  const goSymbol = (symbol: string) => { setSelectedSymbol(symbol); navigate(`/markets/${encodeURIComponent(symbol)}`); };
  return <>
    <button ref={trigger} type="button" onClick={() => setOpen(true)} aria-label="Open search and command palette" aria-haspopup="dialog" aria-expanded={open} className="flex size-11 min-w-0 items-center justify-center gap-2 rounded-md border border-slate-800 bg-terminal-950 px-2 text-left text-sm text-slate-500 hover:border-slate-700 sm:w-auto sm:min-w-64 sm:justify-start sm:px-3"><Search aria-hidden="true" size={16}/><span className="hidden flex-1 sm:inline">Search or jump anywhere</span><kbd className="hidden rounded border border-slate-700 px-1.5 py-.5 text-[10px] sm:inline">Ctrl K</kbd></button>
    {open && <div role="presentation" className="fixed inset-0 z-[80] bg-black/75 p-3 backdrop-blur-sm" onMouseDown={() => { setOpen(false); trigger.current?.focus(); }}><section role="dialog" aria-modal="true" aria-label="Search and command palette" onMouseDown={(event) => event.stopPropagation()} className="mx-auto mt-[9vh] w-[min(680px,100%)] overflow-hidden rounded-2xl border border-slate-700 bg-terminal-900 shadow-2xl">
      <div className="flex items-center gap-3 border-b border-slate-800 p-4"><Search aria-hidden="true" size={18} className="text-accent"/><label className="w-full"><span className="sr-only">Symbol, company, or destination</span><input ref={input} autoFocus value={q} onChange={(event) => setQ(event.target.value)} placeholder="Symbol, page, or action..." className="h-11 w-full bg-transparent text-base outline-none placeholder:text-slate-600"/></label><kbd className="rounded border border-slate-700 px-2 py-1 text-[10px] text-slate-500">Esc</kbd></div>
      <div className="max-h-[62vh] overflow-auto p-2">
        {routeMatches.length > 0 && <div><div className="px-3 pb-1 pt-2 text-[9px] font-semibold uppercase tracking-[.18em] text-slate-600">Destinations</div>{routeMatches.map(([href, label, detail]) => <button key={href} onClick={() => navigate(href)} className="flex min-h-12 w-full items-center justify-between rounded-lg px-3 text-left hover:bg-slate-800/60"><span><b className="text-sm text-slate-100">{label}</b><small className="ml-3 text-slate-500">{detail}</small></span><ArrowRight aria-hidden="true" size={14} className="text-slate-600"/></button>)}</div>}
        {items.length > 0 && <div><div className="px-3 pb-1 pt-3 text-[9px] font-semibold uppercase tracking-[.18em] text-slate-600">NSE / BSE instruments</div>{items.map((item) => <button key={`${item.exchange}-${item.symbol}-${item.instrument_type}`} onClick={() => goSymbol(item.symbol)} className="flex min-h-12 w-full min-w-0 items-center justify-between gap-2 rounded-lg px-3 text-left hover:bg-slate-800/60"><span className="min-w-0"><b className="tabular text-slate-100">{item.symbol}</b><small className="ml-3 hidden text-slate-500 sm:inline">{item.name}</small></span><span className="shrink-0 rounded bg-slate-800 px-2 py-1 text-[10px] text-slate-400">{item.exchange} · {item.instrument_type}</span></button>)}</div>}
        {!routeMatches.length && !items.length && <p className="p-5 text-sm text-slate-500">No matching destination or NSE/BSE instrument.</p>}
      </div>
    </section></div>}
  </>;
}
