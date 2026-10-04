"use client";

import { createContext, useCallback, useContext, useEffect, useReducer, useRef, useState, type CSSProperties, type Dispatch, type ReactNode, type SetStateAction } from "react";
import { useQuery } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";
import { api, type MarketDataContext } from "@/lib/api";
import type { Timeframe } from "@/components/TimeframeBar";
import { useAuth } from "@/lib/auth";

export type MarketMode = "OFFLINE_DEMO" | "FALLBACK_ALLOWED" | "LIVE_ONLY";
export type MarketSurface = "quote" | "history" | "forecast" | "indicators" | "derivatives_chain" | "derivatives_margin" | "derivatives_futures" | "paper_order" | "stream";

type ProviderInfo = { provider: string; configured: boolean; credential_mode?: string };
type HealthResponse = {
  market_data?: {
    provider_mode?: MarketMode;
    providers?: ProviderInfo[];
  };
};
type ProviderHealth = { provider: string; configured?: boolean; available?: boolean; healthy?: boolean; demoted?: boolean; failure_streak?: number; last_success_at?: number | string | null; data_age_seconds?: number | null };
type ProviderHealthResponse = { provider_mode?: MarketMode; active_provider?: string | null; providers?: ProviderHealth[]; readiness_probe?: { ready?: boolean } };

type SurfaceState = {
  contexts: Partial<Record<MarketSurface, MarketDataContext>>;
  errors: Partial<Record<MarketSurface, string>>;
};

type SurfaceAction =
  | { type: "context"; surface: MarketSurface; context?: MarketDataContext }
  | { type: "error"; surface: MarketSurface; error?: string }
  | { type: "clear" };

export type MarketSourceStatus = {
  tone: "idle" | "live" | "fallback" | "demo" | "stale" | "unavailable" | "mixed";
  label: string;
  detail?: string;
  provider?: string;
  mixed: boolean;
};

type MarketContextValue = {
  selectedSymbol: string;
  setSelectedSymbol: Dispatch<SetStateAction<string>>;
  timeframe: Timeframe;
  setTimeframe: Dispatch<SetStateAction<Timeframe>>;
  mode?: MarketMode;
  configuredProviders: ProviderInfo[];
  surfaceContexts: SurfaceState["contexts"];
  surfaceErrors: SurfaceState["errors"];
  sourceStatus: MarketSourceStatus;
  dispatchSurface: Dispatch<SurfaceAction>;
};

const MarketContext = createContext<MarketContextValue | null>(null);

function surfaceReducer(state: SurfaceState, action: SurfaceAction): SurfaceState {
  if (action.type === "clear") return { contexts: {}, errors: {} };
  if (action.type === "context") {
    const contexts = { ...state.contexts };
    const errors = { ...state.errors };
    if (action.context) contexts[action.surface] = action.context;
    else delete contexts[action.surface];
    delete errors[action.surface];
    return { contexts, errors };
  }
  const errors = { ...state.errors };
  if (action.error) errors[action.surface] = action.error;
  else delete errors[action.surface];
  return { contexts: state.contexts, errors };
}

function isDemoProvider(provider: string) {
  const normalized = provider.toLowerCase();
  return normalized === "demo" || normalized.includes("demo_india") || normalized.includes("synthetic");
}

export function deriveSourceStatus(
  mode: MarketMode | undefined,
  configuredProviders: ProviderInfo[],
  contexts: SurfaceState["contexts"],
  errors: SurfaceState["errors"],
): MarketSourceStatus {
  const values = Object.values(contexts).filter((value): value is MarketDataContext => Boolean(value));
  const providers = [...new Set(values.map((value) => value.provider))];
  const requestedSymbols = [...new Set(values.map((value) => value.requested_symbol))];
  const timeframes = [...new Set(values.map((value) => value.timeframe))];
  const instruments = [...new Set(values.map((value) => `${value.resolved_instrument_key}|${value.exchange}|${value.instrument_type}`))];
  const mixed = providers.length > 1 || requestedSymbols.length > 1 || timeframes.length > 1 || instruments.length > 1;
  const fallbackReasons = [...new Set(values.filter((value) => value.fallback_used && value.fallback_reason).map((value) => value.fallback_reason as string))];
  const hasErrors = Object.keys(errors).length > 0;

  if (mixed) {
    return {
      tone: "mixed",
      label: "Mixed sources",
      detail: providers.length > 1 ? providers.join(" + ") : requestedSymbols.length > 1 ? "Responses requested different symbols" : timeframes.length > 1 ? "Responses use different timeframes" : "Responses resolved to different instruments",
      mixed: true,
    };
  }
  if (mode === "OFFLINE_DEMO" || (providers.length === 1 && isDemoProvider(providers[0]))) {
    return { tone: "demo", label: providers[0] ? `Demo: ${providers[0]}` : "Demo / synthetic data", provider: providers[0], mixed: false };
  }
  if (values.some((value) => value.is_stale) || (mode === "LIVE_ONLY" && hasErrors && values.length > 0)) {
    return { tone: "stale", label: providers[0] ? `Stale: ${providers[0]}` : "Market data stale", provider: providers[0], mixed: false };
  }
  if (mode === "LIVE_ONLY" && hasErrors) {
    return { tone: "unavailable", label: "Live data unavailable", detail: Object.values(errors)[0], mixed: false };
  }
  if (values.some((value) => value.fallback_used)) {
    const detail = fallbackReasons.join("; ") || "Primary provider unavailable";
    return { tone: "fallback", label: `Fallback: ${providers[0] || "provider"}`, detail, provider: providers[0], mixed: false };
  }
  if (providers.length === 1) {
    const context = values[0];
    return { tone: context.is_live ? "live" : "idle", label: `${context.is_live ? "Live" : "Snapshot"}: ${providers[0]}`, provider: providers[0], mixed: false };
  }
  if (configuredProviders.length === 1) {
    return { tone: "idle", label: `Configured: ${configuredProviders[0].provider}`, provider: configuredProviders[0].provider, mixed: false };
  }
  if (configuredProviders.length > 1) {
    return { tone: "idle", label: `${configuredProviders.length} sources configured`, mixed: false };
  }
  return { tone: mode === "LIVE_ONLY" ? "unavailable" : "idle", label: mode === "LIVE_ONLY" ? "Live data unavailable" : "Checking data source", mixed: false };
}

export function MarketProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const [selectedSymbol, setSelectedSymbolState] = useState("NIFTY 50");
  const [timeframe, setTimeframeState] = useState<Timeframe>("1D");
  const [surfaceState, dispatchSurface] = useReducer(surfaceReducer, { contexts: {}, errors: {} });
  const selectedSymbolRef = useRef(selectedSymbol);
  const timeframeRef = useRef(timeframe);
  const workspaceKey = `stockpilot-market-context:${user?.id ?? "guest"}`;
  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(workspaceKey) || "{}") as { timeframe?: Timeframe };
      // A market route's symbol is the authoritative deep-link state. Only restore
      // preferences that cannot conflict with a URL-selected instrument.
      if (saved.timeframe && ["1m", "5m", "15m", "1h", "4h", "1D", "1W"].includes(saved.timeframe)) { timeframeRef.current = saved.timeframe; setTimeframeState(saved.timeframe); }
    } catch { /* Invalid device state falls back to reviewed defaults. */ }
  }, [workspaceKey]);
  useEffect(() => {
    try {
      localStorage.setItem(workspaceKey, JSON.stringify({ symbol: selectedSymbol, timeframe, updatedAt: new Date().toISOString() }));
    } catch { /* Device storage can be unavailable; keep the workspace usable. */ }
  }, [workspaceKey, selectedSymbol, timeframe]);
  const setSelectedSymbol: Dispatch<SetStateAction<string>> = useCallback((value) => {
    const next = typeof value === "function" ? value(selectedSymbolRef.current) : value;
    if (next === selectedSymbolRef.current) return;
    selectedSymbolRef.current = next;
    dispatchSurface({ type: "clear" });
    setSelectedSymbolState(next);
  }, []);
  const setTimeframe: Dispatch<SetStateAction<Timeframe>> = useCallback((value) => {
    const next = typeof value === "function" ? value(timeframeRef.current) : value;
    if (next === timeframeRef.current) return;
    timeframeRef.current = next;
    dispatchSurface({ type: "clear" });
    setTimeframeState(next);
  }, []);
  const health = useQuery({
    queryKey: ["data-source-health"],
    queryFn: () => api<HealthResponse>("/api/v1/health"),
    refetchInterval: 15000,
    retry: 1,
  });
  const providerHealth = useQuery({
    queryKey: ["provider-health"],
    queryFn: () => api<ProviderHealthResponse>("/api/v1/market/providers/health"),
    refetchInterval: 15000,
    retry: 1,
  });
  const mode = health.data?.market_data?.provider_mode;
  const configuredProviders = (health.data?.market_data?.providers || []).filter((provider) => provider.configured);
  const sourceStatus = deriveSourceStatus(mode, configuredProviders, surfaceState.contexts, surfaceState.errors);

  const value: MarketContextValue = {
    selectedSymbol,
    setSelectedSymbol,
    timeframe,
    setTimeframe,
    mode,
    configuredProviders,
    surfaceContexts: surfaceState.contexts,
    surfaceErrors: surfaceState.errors,
    sourceStatus,
    dispatchSurface,
  };
  const demo = mode === "OFFLINE_DEMO";

  return (
    <MarketContext.Provider value={value}>
      <div
        className="min-h-screen"
        style={{ "--market-banner-height": demo ? "28px" : "0px", paddingTop: "var(--market-banner-height)" } as CSSProperties}
      >
        <ProviderHealthPanel query={providerHealth} />
        {demo && (
          <div className="fixed inset-x-0 top-0 z-[70] flex h-7 items-center justify-center border-b border-amber-300/40 bg-amber-400 px-3 text-center text-[11px] font-bold uppercase tracking-[.18em] text-[#111827]" role="status">
            Demo / synthetic data
          </div>
        )}
        {children}
      </div>
    </MarketContext.Provider>
  );
}

/** Secret-free operational disclosure: distinguishes live health, stale telemetry, and fallback. */
function ProviderHealthPanel({ query }: { query: UseQueryResult<ProviderHealthResponse> }) {
  const providers = query.data?.providers ?? [];
  if (query.isLoading) return <div className="border-b border-slate-800 bg-slate-950 px-3 py-1 text-center text-[10px] text-slate-500" role="status">Checking provider health…</div>;
  if (query.isError || !query.data) return <div className="border-b border-amber-500/30 bg-amber-500/10 px-3 py-1 text-center text-[10px] text-amber-200" role="status">Provider health unavailable · using local source labels; no live claim is made.</div>;
  const active = query.data.active_provider;
  return <details className="border-b border-slate-800 bg-slate-950 px-3 py-1 text-[10px] text-slate-400">
    <summary className="mx-auto max-w-6xl cursor-pointer list-none text-center uppercase tracking-wider">Provider health · {query.data.provider_mode ?? "unknown"} · {active ? `active: ${active}` : "fallback / none active"}</summary>
    <div className="mx-auto mt-2 grid max-w-6xl gap-1 pb-1 sm:grid-cols-2 lg:grid-cols-4">
      {providers.map((provider) => {
        const stale = provider.data_age_seconds != null && provider.data_age_seconds > 300;
        const live = provider.provider === active && !stale && !provider.demoted;
        const state = live ? "live" : stale ? "stale" : provider.demoted ? "fallback" : "available";
        return <div key={provider.provider} className="rounded border border-slate-800 px-2 py-1" data-provider-state={state}><span className={state === "live" ? "text-gain" : state === "stale" ? "text-warning" : "text-slate-400"}>{provider.provider}: {state}</span>{provider.failure_streak ? <span className="ml-1 text-slate-600">({provider.failure_streak} failures)</span> : null}</div>;
      })}
    </div>
    <p className="mx-auto max-w-6xl pb-1 text-center text-[9px] text-slate-600">Telemetry is secret-free and may lag by up to 15 seconds. Market panels remain individually labelled for fallback or stale data.</p>
  </details>;
}

export function useMarket() {
  const context = useContext(MarketContext);
  if (!context) throw new Error("useMarket must be used within MarketProvider");
  return context;
}

export function MarketSourceLabel({ context }: { context?: MarketDataContext }) {
  if (!context) return null;
  return (
    <div className={`border-b px-2 py-1.5 text-[9px] uppercase tracking-wide ${context.is_stale ? "border-amber-500/20 bg-amber-500/5 text-amber-300" : context.fallback_used ? "border-amber-500/20 bg-amber-500/5 text-amber-300" : "border-slate-800 bg-slate-950/40 text-slate-500"}`}>
      <span>{context.provider.replace(/_/g, " ")} · {context.timeframe}</span>
      {context.is_stale && <span className="ml-2 font-semibold">Stale</span>}
      {context.fallback_used && <span className="ml-2 normal-case tracking-normal">Fallback: {context.fallback_reason || "primary provider unavailable"}</span>}
    </div>
  );
}

export function MixedSourceNotice() {
  const { sourceStatus } = useMarket();
  if (!sourceStatus.mixed) return null;
  return (
    <div className="mb-2 rounded-lg border border-orange-400/30 bg-orange-400/10 px-3 py-2 text-xs text-orange-200" role="alert">
      <strong>Mixed market-data contexts.</strong> {sourceStatus.detail}. Each panel is isolated and labeled with its own source; do not treat these values as one coherent snapshot.
    </div>
  );
}
