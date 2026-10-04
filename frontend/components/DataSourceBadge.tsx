"use client";

import { CircleAlert, Radio, RadioTower, ShieldAlert } from "lucide-react";
import { useMarket } from "@/components/MarketContext";

export function DataSourceBadge() {
  const { sourceStatus } = useMarket();
  const { label, tone, detail } = sourceStatus;
  const Icon = tone === "live" ? RadioTower : tone === "demo" || tone === "unavailable" || tone === "mixed" ? ShieldAlert : tone === "stale" ? CircleAlert : Radio;
  const toneClass =
    tone === "live"
      ? "border-secondary/30 bg-secondary/10 text-secondary"
      : tone === "fallback" || tone === "stale"
        ? "border-amber-500/30 bg-amber-500/10 text-amber-300"
        : tone === "demo" || tone === "unavailable"
          ? "border-rose-500/30 bg-rose-500/10 text-rose-300"
          : tone === "mixed"
            ? "border-orange-400/40 bg-orange-400/10 text-orange-200"
            : "border-slate-800 bg-slate-900/50 text-slate-500";

  return (
    <div
      title={detail || "Actual or configured market-data source"}
      aria-label={[label, detail].filter(Boolean).join(": ")}
      className={`flex min-w-0 shrink items-center gap-1.5 rounded-md border px-2 py-1 text-[10px] uppercase tracking-wide ${toneClass}`}
    >
      <Icon size={12} className={`shrink-0 ${tone === "live" ? "animate-pulse" : ""}`} aria-hidden="true" />
      <span className="max-w-32 truncate capitalize">{label.replace(/_/g, " ")}</span>
      {detail && <span className="hidden max-w-48 truncate normal-case tracking-normal xl:inline">· {detail}</span>}
    </div>
  );
}
