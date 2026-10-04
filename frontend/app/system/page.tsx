"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleCheck, CircleX, ExternalLink, RefreshCw } from "lucide-react";
import { TerminalShell } from "@/components/TerminalShell";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";

type Provider = {
  provider: string;
  configured: boolean;
  status: string;
  quote?: string;
  history?: string;
  classification?: string;
  quote_latency_ms?: number;
  history_latency_ms?: number;
};

type ProviderMetric = {
  requests: number;
  successes: number;
  failures: number;
  fallbacks: number;
  last_latency_ms?: number | null;
  seconds_since_success?: number | null;
  data_age_seconds?: number | null;
};

type ProviderMetrics = {
  requests: number;
  successes: number;
  failures: number;
  fallback_loads: number;
  fallback_rate: number;
  stale_cache_hits: number;
  providers: Record<string, ProviderMetric>;
};

type SystemStatus = {
  version: string;
  database_status: string;
  instrument_count: number;
  market: { exchange?: string; regular_session?: string; is_open?: boolean; reason?: string };
  providers: { status: string; mode: string; order: string[]; providers: Provider[]; metrics?: ProviderMetrics };
  range_forecasting: { training_windows: string[]; timeframes: string[] };
};

export default function SystemPage() {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["system"],
    queryFn: () => api<SystemStatus>("/api/v1/system"),
    refetchInterval: 10_000,
  });
  const testProviders = useMutation({
    mutationFn: () => api("/api/v1/system/providers/test", { method: "POST" }),
    onSuccess: () => window.setTimeout(() => queryClient.invalidateQueries({ queryKey: ["system"] }), 800),
  });
  const data = query.data;

  return <TerminalShell>
    <div className="mb-4">
      <div className="text-xs uppercase tracking-[.22em] text-accent">Operational context</div>
      <h1 className="mt-1 text-2xl font-semibold">System status</h1>
      <p className="mt-1 text-sm text-slate-500">Provider readiness, data mode, market state, and supported forecast inputs.</p>
    </div>

    <div className="grid gap-3 xl:grid-cols-[1.25fr_.75fr]">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Market providers</CardTitle>
            <p className="mt-1 text-xs text-slate-500">Mode: {data?.providers?.mode || "checking"} · Order: {data?.providers?.order?.join(" → ") || "checking"}</p>
          </div>
          <Button type="button" variant="ghost" onClick={() => testProviders.mutate()} disabled={testProviders.isPending}>
            <RefreshCw aria-hidden="true" size={14} className={testProviders.isPending ? "animate-spin" : ""} />
            Test connections
          </Button>
        </CardHeader>
        <CardContent className="space-y-2">
          {testProviders.error && <div role="alert" className="rounded border border-warning/30 bg-warning/5 p-3 text-xs text-warning">{(testProviders.error as Error).message} Sign in to run connection tests.</div>}
          {!data?.providers?.providers?.length && <div className="rounded border border-slate-800 p-4 text-sm text-slate-500">Provider checks are starting. This page refreshes automatically.</div>}
          {data?.providers?.providers?.map((provider) => {
            const ready = provider.status === "ready";
            const metric = data.providers.metrics?.providers?.[provider.provider];
            return <div key={provider.provider} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-800 bg-slate-950/30 p-3">
              <div className="flex items-start gap-3">
                {ready ? <CircleCheck aria-hidden="true" size={17} className="mt-0.5 text-gain" /> : <CircleX aria-hidden="true" size={17} className="mt-0.5 text-warning" />}
                <div>
                  <div className="font-semibold capitalize text-slate-200">{provider.provider}</div>
                  <div className="mt-1 text-xs text-slate-500">Quote {provider.quote || "not run"} · History {provider.history || "not run"}</div>
                  {metric && <div className="mt-1 text-[11px] text-slate-600">{metric.successes}/{metric.requests} requests succeeded · {metric.failures} failures · {metric.fallbacks} fallback selections</div>}
                  {provider.classification && <div className="mt-1 text-xs text-warning">{provider.classification}. Check credentials or reauthorize.</div>}
                </div>
              </div>
              <div className="text-right">
                <span className={`rounded-full border px-2 py-1 text-[10px] font-semibold uppercase tracking-wide ${ready ? "border-gain/30 bg-gain/10 text-gain" : "border-slate-700 bg-slate-900 text-slate-400"}`}>{provider.status.replaceAll("_", " ")}</span>
                {(metric?.last_latency_ms != null || provider.quote_latency_ms || provider.history_latency_ms) && <div className="mt-2 text-[10px] text-slate-600">Latency {metric?.last_latency_ms ?? provider.quote_latency_ms ?? "-"} ms</div>}
                {metric?.data_age_seconds != null && <div className="mt-1 text-[10px] text-slate-600">Data age {formatAge(metric.data_age_seconds)}</div>}
              </div>
            </div>;
          })}
          {data?.providers?.metrics && <div className="grid grid-cols-2 gap-2 rounded-lg border border-slate-800 bg-slate-950/30 p-3 text-xs text-slate-500 sm:grid-cols-4">
            <Metric label="Requests" value={String(data.providers.metrics.requests)} />
            <Metric label="Failures" value={String(data.providers.metrics.failures)} />
            <Metric label="Fallback rate" value={`${(data.providers.metrics.fallback_rate * 100).toFixed(1)}%`} />
            <Metric label="Stale cache hits" value={String(data.providers.metrics.stale_cache_hits)} />
          </div>}
          <div className="rounded-lg border border-accent/20 bg-accent/5 p-3 text-xs leading-5 text-slate-400">
            Broker secrets are read only from the backend environment and are never displayed or stored in this browser. For Upstox setup and reauthorization, use the official
            {" "}<a className="inline-flex items-center gap-1 text-accent underline underline-offset-4" href="https://upstox.com/developer/api-documentation/getting-started/" target="_blank" rel="noreferrer">developer guide <ExternalLink aria-hidden="true" size={12} /></a>.
          </div>
        </CardContent>
      </Card>

      <div className="grid content-start gap-3">
        <Card><CardHeader><CardTitle>Core health</CardTitle></CardHeader><CardContent className="space-y-3 text-sm">
          <Status label="Database" value={data?.database_status || "checking"} good={data?.database_status === "operational"} />
          <Status label="Market" value={data?.market?.is_open ? "Open" : data?.market?.reason || "checking"} good={Boolean(data?.market?.is_open)} />
          <Status label="Provider probe" value={data?.providers?.status || "checking"} good={data?.providers?.status === "complete"} />
          <Status label="Instrument universe" value={data ? `${data.instrument_count} India-listed instruments` : "checking"} good={Boolean(data?.instrument_count)} />
        </CardContent></Card>
        <Card><CardHeader><CardTitle>Forecast support</CardTitle></CardHeader><CardContent className="text-xs leading-6 text-slate-400">
          <div>Windows: {data?.range_forecasting?.training_windows?.join(", ") || "checking"}</div>
          <div>Timeframes: {data?.range_forecasting?.timeframes?.join(", ") || "checking"}</div>
          <div className="mt-2 text-slate-600">Version {data?.version || "-"}. Unsupported combinations remain disabled rather than substituted.</div>
        </CardContent></Card>
      </div>
    </div>
  </TerminalShell>;
}

function Status({ label, value, good }: { label: string; value: string; good: boolean }) {
  return <div className="flex items-center justify-between gap-4 border-b border-slate-800 pb-3 last:border-0 last:pb-0"><span className="text-slate-500">{label}</span><span className={`text-right font-medium ${good ? "text-gain" : "text-slate-300"}`}>{value}</span></div>;
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div><div className="text-[10px] uppercase tracking-wide text-slate-600">{label}</div><div className="mt-1 font-semibold text-slate-300">{value}</div></div>;
}

function formatAge(seconds: number) {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${Math.round(seconds / 3600)}h`;
}
