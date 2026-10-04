"use client";

export function MiniTrend({ value, label }: { value?: number | null; label?: string }) {
  if (value == null || !Number.isFinite(Number(value))) {
    return <span className="text-[10px] text-slate-600">trend unavailable</span>;
  }
  const numeric = Number(value);
  const positive = numeric >= 0;
  const width = Math.min(100, Math.max(8, Math.abs(numeric) * 12));
  return (
    <span className="inline-flex min-w-24 items-center gap-2" aria-label={`${label || "Trend"}: ${numeric.toFixed(2)} percent`}>
      <span className={`text-[10px] ${positive ? "signal-up" : "signal-down"}`} aria-hidden="true">{positive ? "▲" : "▼"}</span>
      <span className="h-1.5 w-16 overflow-hidden rounded-full bg-slate-800">
        <span className={`block h-full rounded-full ${positive ? "bg-gain" : "bg-loss"}`} style={{ width: `${width}%` }} />
      </span>
    </span>
  );
}
