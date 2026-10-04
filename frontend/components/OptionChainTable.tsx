"use client";

import { useMemo, useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Activity, CircleAlert } from "lucide-react";

type Side = {
  instrument_key?: string | null;
  option_type?: "CE" | "PE";
  ltp?: number | null;
  open_interest?: number | null;
  oi_change?: number | null;
  volume?: number | null;
  iv?: number | null;
  delta?: number | null;
  gamma?: number | null;
  theta?: number | null;
  vega?: number | null;
};
export type OptionChainRow = { strike: number; expiry?: string; underlying_spot?: number | null; ce?: Side | null; pe?: Side | null };

const fmt = (value: number | null | undefined, digits = 2) => value == null || Number.isNaN(Number(value)) ? "unavailable" : Number(value).toLocaleString("en-IN", { maximumFractionDigits: digits });

export function OptionChainTable({ rows, isLive }: { rows: OptionChainRow[]; isLive: boolean }) {
  const parentRef = useRef<HTMLDivElement>(null);
  const data = useMemo(() => rows || [], [rows]);
  const virtualizer = useVirtualizer({ count: data.length, getScrollElement: () => parentRef.current, estimateSize: () => 42, overscan: 12 });

  if (!data.length) return <div className="grid min-h-52 place-items-center rounded-xl border border-dashed border-slate-700/80 bg-terminal-900/50 p-8 text-center"><div><CircleAlert className="mx-auto mb-3 text-info" size={24}/><p className="text-sm font-medium text-slate-200">No option contracts available</p><p className="mt-1 max-w-md text-xs leading-5 text-slate-500">Refresh the India instrument master or choose a currently listed underlying and expiry.</p></div></div>;

  return <div className="overflow-hidden rounded-xl border border-slate-800/80 bg-terminal-900/70 shadow-panel">
    <div className="flex items-center justify-between border-b border-slate-800/80 px-3 py-2 text-[10px] uppercase tracking-[.16em] text-slate-500"><span className="flex items-center gap-2"><Activity size={13}/> Option chain</span><span className={isLive ? "text-gain" : "text-warning"}>{isLive ? "Live broker data" : "Instrument master · market fields unavailable"}</span></div>
    <div className="grid grid-cols-[repeat(5,minmax(70px,1fr))_90px_repeat(5,minmax(70px,1fr))] gap-px border-b border-slate-800 bg-slate-800/70 px-2 py-2 text-center text-[9px] uppercase tracking-wide text-slate-500">
      {['CE OI','CE ΔOI','CE LTP','CE IV','CE Δ','Strike','PE Δ','PE IV','PE LTP','PE ΔOI','PE OI'].map((h)=><span key={h}>{h}</span>)}
    </div>
    <div ref={parentRef} className="h-[520px] overflow-auto" role="table" aria-label="Live option chain">
      <div style={{ height: `${virtualizer.getTotalSize()}px`, position: "relative" }}>
        {virtualizer.getVirtualItems().map((v) => { const row=data[v.index]; return <div key={`${row.strike}-${v.index}`} className="absolute left-0 top-0 grid w-full grid-cols-[repeat(5,minmax(70px,1fr))_90px_repeat(5,minmax(70px,1fr))] items-center gap-px border-b border-slate-900/80 px-2 text-center text-[11px] transition hover:bg-accent/5 focus-within:bg-accent/5" style={{ height: `${v.size}px`, transform: `translateY(${v.start}px)` }}>
          <span className="tabular text-slate-300">{fmt(row.ce?.open_interest,0)}</span><span className="tabular text-slate-500">{fmt(row.ce?.oi_change,0)}</span><span className="tabular text-gain">{fmt(row.ce?.ltp)}</span><span className="tabular text-info">{fmt(row.ce?.iv)}</span><span className="tabular text-slate-400">{fmt(row.ce?.delta,3)}</span><span className="tabular rounded bg-secondary/10 py-1 font-semibold text-secondary">{fmt(row.strike)}</span><span className="tabular text-slate-400">{fmt(row.pe?.delta,3)}</span><span className="tabular text-info">{fmt(row.pe?.iv)}</span><span className="tabular text-loss">{fmt(row.pe?.ltp)}</span><span className="tabular text-slate-500">{fmt(row.pe?.oi_change,0)}</span><span className="tabular text-slate-300">{fmt(row.pe?.open_interest,0)}</span>
        </div> })}
      </div>
    </div>
  </div>;
}
