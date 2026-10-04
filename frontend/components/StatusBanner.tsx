"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

/**
 * Global operator notice strip.
 *
 * Renders the banners published through the admin operations panel
 * (`GET /api/v1/status`, which is unauthenticated). It intentionally renders
 * nothing when there is no active banner, and it stays silent when the status
 * request itself fails: a component whose job is to report degradation must not
 * become another error surface on every page.
 */

type Banner = {
  id: number;
  level: string;
  headline: string;
  body?: string | null;
  starts_at?: string | null;
  ends_at?: string | null;
};

type StatusPayload = { banners?: Banner[]; version?: string };

// Mirrors BANNER_LEVELS in services/forecast_guardrails.py.
const LEVEL_STYLES: Record<string, string> = {
  info: "border-accent/40 bg-accent/10 text-slate-200",
  maintenance: "border-slate-700 bg-terminal-900 text-slate-200",
  degraded: "border-warning/40 bg-warning/10 text-warning",
  outage: "border-loss/40 bg-loss/10 text-loss",
};

export function StatusBanner() {
  const [dismissed, setDismissed] = useState<number[]>([]);
  const status = useQuery({
    queryKey: ["public-status"],
    queryFn: () => api<StatusPayload>("/api/v1/status"),
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: 1,
  });

  const banners = (status.data?.banners ?? []).filter((banner) => !dismissed.includes(banner.id));
  if (banners.length === 0) return null;

  return (
    <div className="sticky top-0 z-50 flex flex-col">
      {banners.map((banner) => {
        const level = String(banner.level ?? "info").toLowerCase();
        const urgent = level === "outage" || level === "degraded";
        return (
          <div
            key={banner.id}
            role={urgent ? "alert" : "status"}
            aria-live={urgent ? "assertive" : "polite"}
            className={`flex items-start gap-3 border-b px-4 py-2 text-xs leading-5 ${LEVEL_STYLES[level] ?? LEVEL_STYLES.info}`}
          >
            <span className="mt-0.5 shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider ring-1 ring-inset ring-white/20">
              {level}
            </span>
            <div className="min-w-0 flex-1">
              <p className="font-medium">{banner.headline}</p>
              {banner.body ? <p className="mt-0.5 opacity-90">{banner.body}</p> : null}
              {banner.ends_at ? (
                <p className="mt-0.5 text-[10px] opacity-70">
                  Scheduled until {new Date(banner.ends_at).toLocaleString()}
                </p>
              ) : null}
            </div>
            <button
              type="button"
              onClick={() => setDismissed((previous) => [...previous, banner.id])
              }
              aria-label={`Dismiss notice: ${banner.headline}`}
              className="shrink-0 rounded px-2 py-0.5 text-[10px] uppercase tracking-wider opacity-80 hover:opacity-100"
            >
              Dismiss
            </button>
          </div>
        );
      })}
    </div>
  );
}

export default StatusBanner;
