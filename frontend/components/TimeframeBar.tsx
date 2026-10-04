"use client";

import { useEffect, useState } from "react";
import { motionPreference, transitionCss } from "@/lib/motionTokens";

export const TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1D", "1W"] as const;
export type Timeframe = (typeof TIMEFRAMES)[number];

export function TimeframeBar({ value, onChange }: { value: Timeframe; onChange: (value: Timeframe) => void }) {
  // Motion values come from MOTION_TOKENS, never from ad-hoc utility classes.
  const [preference, setPreference] = useState<"full" | "reduced">("full");
  useEffect(() => setPreference(motionPreference()), []);
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target instanceof Element && target.matches("input, textarea, select, [contenteditable='true']")) return;
      if (!event.altKey) return;
      const index = Number(event.key) - 1;
      if (index >= 0 && index < TIMEFRAMES.length) {
        event.preventDefault();
        onChange(TIMEFRAMES[index]);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onChange]);

  return (
    <div className="flex flex-wrap items-center gap-1" aria-label="Chart timeframe controls">
      {TIMEFRAMES.map((frame, index) => (
        <button
          key={frame}
          type="button"
          aria-pressed={value === frame}
          title={`${frame} · Alt+${index + 1}`}
          onClick={() => onChange(frame)}
          style={{ transition: transitionCss("interaction", preference) }}
          className={`interactive-surface min-h-11 rounded-md px-2.5 py-1 text-xs font-semibold ${
            value === frame ? "bg-accent/15 text-accent" : "text-slate-500 hover:bg-slate-800 hover:text-slate-200"
          }`}
        >
          {frame}
        </button>
      ))}
      <span className="ml-1 hidden text-[9px] uppercase tracking-wider text-slate-700 sm:inline">Alt+1…7</span>
    </div>
  );
}
