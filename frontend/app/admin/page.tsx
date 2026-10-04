"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Database, RefreshCw, Search, ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { TerminalShell } from "@/components/TerminalShell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { api, formatApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { AdminOperationsPanel } from "@/components/AdminOperationsPanel";

type AdminOverview = {
  version: string;
  database_status: string;
  market: Record<string, unknown>;
  instrument_count: number;
  counts: Record<string, number>;
  errors: Record<string, unknown>;
  administrators: { max_admins: number; configured: string[]; configuration_error: string | null };
  privileges: unknown;
  scheduler_enabled: boolean;
  settings: Record<string, unknown>;
  jobs?: unknown;
  cache?: unknown;
  data_quality?: unknown;
};
type AuditItem = { id: number; actor_email: string; action: string; outcome: string; detail: Record<string, unknown>; created_at: string };
type AuditResp = { items: AuditItem[] };
type ModelsResp = { registry?: unknown; history_support?: unknown; note?: string };
type ErrorsResp = { summary?: unknown; groups?: unknown[] };
type SettingsResp = { settings?: unknown };
type HealthResp = { market_data?: unknown };

function JsonSection({ title, description, value }: { title: string; description: string; value?: unknown }) {
  const available = value !== undefined && value !== null && (!(Array.isArray(value)) || value.length > 0);
  return (
    <Card>
      <CardHeader className="items-start gap-3"><div><CardTitle>{title}</CardTitle><p className="mt-1 text-[11px] leading-4 text-slate-500">{description}</p></div><span className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-1 text-[9px] font-semibold uppercase ${available ? "border-gain/30 text-gain" : "border-slate-700 text-slate-500"}`}>{available ? <CheckCircle2 aria-hidden="true" size={11}/> : <Database aria-hidden="true" size={11}/>} {available ? "Reported" : "Not reported"}</span></CardHeader>
      <CardContent>{available ? <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-5 text-slate-300">{JSON.stringify(value, null, 2)}</pre> : <p className="text-xs leading-5 text-slate-500">Not reported by this deployment. No healthy state is inferred from missing telemetry.</p>}</CardContent>
    </Card>
  );
}

//: Mirrors STEP_UP_MAINTENANCE_ACTIONS in api/main.py. Actions outside this set
//: are audited but do not require password re-confirmation.
const STEP_UP_MAINTENANCE_ACTIONS = new Set(["bootstrap_admins"]);

function selectKeys(record: Record<string, unknown> | undefined, terms: string[]) {
  if (!record) return undefined;
  const entries = Object.entries(record).filter(([key]) => terms.some((term) => key.toLowerCase().includes(term)));
  return entries.length ? Object.fromEntries(entries) : undefined;
}

function AdminWorkspace() {
  const qc = useQueryClient();
  const [auditLimit, setAuditLimit] = useState(50);
  const [auditSearch, setAuditSearch] = useState("");
  const [providerOrder, setProviderOrder] = useState("upstox,yfinance,nse,demo");
  const [stepUpPassword, setStepUpPassword] = useState("");
  const overview = useQuery({ queryKey: ["admin-overview"], queryFn: () => api<AdminOverview>("/api/v1/admin/overview"), retry: 1 });
  const models = useQuery({ queryKey: ["admin-models"], queryFn: () => api<ModelsResp>("/api/v1/admin/models"), retry: 1 });
  const errors = useQuery({ queryKey: ["admin-errors"], queryFn: () => api<ErrorsResp>("/api/v1/admin/errors?limit=50"), retry: 1 });
  const settings = useQuery({ queryKey: ["admin-settings"], queryFn: () => api<SettingsResp>("/api/v1/admin/settings"), retry: 1 });
  const health = useQuery({ queryKey: ["admin-provider-health"], queryFn: () => api<HealthResp>("/api/v1/health"), retry: 1 });
  const audit = useQuery({ queryKey: ["admin-audit", auditLimit], queryFn: () => api<AuditResp>(`/api/v1/admin/audit?limit=${auditLimit}`) });
  // The backend gates these two surfaces behind a single-use step-up token, so
  // the UI must mint one with a password re-confirmation instead of sending the
  // request bare and failing with step_up_required.
  async function mintStepUp(action: string, target: string) {
    if (!stepUpPassword) throw new Error("Re-enter your password to confirm this privileged action.");
    const grant = await api<{ step_up_token: string }>("/api/v1/admin/step-up", {
      method: "POST",
      body: JSON.stringify({ password: stepUpPassword, action, target }),
    });
    return grant.step_up_token;
  }
  const maintenance = useMutation({
    mutationFn: async (action: string) => {
      const headers: Record<string, string> = {};
      if (STEP_UP_MAINTENANCE_ACTIONS.has(action)) {
        headers["X-Step-Up-Token"] = await mintStepUp("admin_change", `maintenance:${action}`);
      }
      return api(`/api/v1/admin/maintenance/${action}`, { method: "POST", headers });
    },
    onSuccess: () => { setStepUpPassword(""); qc.invalidateQueries({ queryKey: ["admin-overview"] }); qc.invalidateQueries({ queryKey: ["admin-errors"] }); qc.invalidateQueries({ queryKey: ["admin-audit"] }); },
  });
  const reorderProviders = useMutation({
    mutationFn: async () => api("/api/v1/admin/diagnostics/providers/order", {
      method: "PUT",
      headers: { "X-Step-Up-Token": await mintStepUp("provider_mode_change", "provider_order") },
      body: JSON.stringify({ order: providerOrder.split(",").map((name) => name.trim()).filter(Boolean) }),
    }),
    onSuccess: () => { setStepUpPassword(""); qc.invalidateQueries({ queryKey: ["admin-provider-health"] }); qc.invalidateQueries({ queryKey: ["admin-audit"] }); },
  });

  const data = overview.data;
  const counts = data?.counts;
  const auditItems = audit.data?.items ?? [];
  const filteredAudit = auditSearch ? auditItems.filter((event) => `${event.actor_email} ${event.action} ${event.outcome}`.toLowerCase().includes(auditSearch.toLowerCase())) : auditItems;
  const jobTelemetry = data?.jobs ?? selectKeys(data?.counts as Record<string, unknown> | undefined, ["job", "queue", "running"]);
  const cacheTelemetry = data?.cache ?? selectKeys(data?.settings, ["cache", "ttl"]);
  const dataQuality = data?.data_quality ?? { market: data?.market, error_summary: errors.data?.summary ?? data?.errors };
  const forecastQuality = models.data ? { registry: models.data.registry, history_support: models.data.history_support, note: models.data.note } : undefined;

  return (
    <TerminalShell>
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div><div className="flex items-center gap-2 text-accent"><ShieldCheck aria-hidden="true" size={17}/><span className="text-[10px] uppercase tracking-[.2em]">Administration</span></div><h1 className="mt-1 text-2xl font-semibold text-white">Admin Workspace</h1><p className="mt-1 text-sm text-slate-500">Operational visibility without user-level portfolio or credential access.</p></div>
        <Button variant="ghost" onClick={() => Promise.all([overview.refetch(), models.refetch(), errors.refetch(), settings.refetch(), health.refetch()])} disabled={overview.isFetching} className="gap-2"><RefreshCw aria-hidden="true" size={14} className={overview.isFetching ? "animate-spin" : ""}/>Refresh all</Button>
      </div>
      {overview.isError && <div role="alert" className="mb-4 rounded-lg border border-loss/30 bg-loss/5 p-3 text-xs text-loss"><AlertTriangle aria-hidden="true" className="mr-2 inline" size={14}/>{formatApiError(overview.error)}</div>}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[["Database", data?.database_status], ["Instruments", data?.instrument_count?.toLocaleString()], ["Users", counts?.users?.toLocaleString()], ["Predictions", counts?.saved_forecasts?.toLocaleString()]].map(([label, value]) => <Card key={label}><CardHeader><CardTitle className="text-xs text-slate-500">{label}</CardTitle></CardHeader><CardContent><div className="tabular text-lg text-white">{value ?? "Unavailable"}</div><div className="mt-1 text-[10px] text-slate-500">Status: {value == null ? "not reported" : "reported"}</div></CardContent></Card>)}
      </div>

      <section className="mt-5" aria-labelledby="operations-title">
        <div className="mb-3"><h2 id="operations-title" className="display-font text-lg font-semibold text-white">Operations and quality</h2><p className="mt-1 text-xs text-slate-500">Sections remain explicit when the active backend does not publish a metric.</p></div>
        <div className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-3">
          <JsonSection title="Providers" description="Configured market-data paths and capability health." value={health.data?.market_data} />
          <JsonSection title="Data quality" description="Market state and sanitized acquisition errors." value={dataQuality} />
          <JsonSection title="Forecast quality" description="Registry evidence and supported history combinations." value={forecastQuality} />
          <JsonSection title="Models" description="Authoritative model inventory, including negative states." value={models.data?.registry} />
          <JsonSection title="Errors" description="Grouped failures without stack traces or secrets." value={errors.data ?? data?.errors} />
          <JsonSection title="Settings" description="Allowlisted operational settings and bounds." value={settings.data?.settings ?? data?.settings} />
          <JsonSection title="Jobs" description="Forecast queue and worker activity when telemetry is available." value={jobTelemetry} />
          <JsonSection title="Cache" description="Cache size, hit/freshness, and TTL data when available." value={cacheTelemetry} />
        </div>
      </section>

      <div className="mt-4 grid gap-3 lg:grid-cols-2">
        <Card><CardHeader><CardTitle>Administrators</CardTitle></CardHeader><CardContent><div className="text-sm text-white">{data?.administrators?.configured?.length ?? 0} / {data?.administrators?.max_admins ?? 3} configured</div>{data?.administrators?.configuration_error && <p role="alert" className="mt-2 text-xs text-warning">Configuration warning: {data.administrators.configuration_error}</p>}<div className="mt-2 text-xs text-slate-500">Scheduler: {data?.scheduler_enabled ? "enabled" : "disabled"}</div></CardContent></Card>
        <Card><CardHeader><CardTitle>Quick maintenance</CardTitle></CardHeader><CardContent className="flex flex-wrap gap-2">{(["refresh_instruments", "refresh_calendar", "clear_error_groups", "bootstrap_admins"] as const).map((action) => <Button key={action} variant="ghost" disabled={maintenance.isPending} onClick={() => maintenance.mutate(action)} className="text-xs">{action.replaceAll("_", " ")}{STEP_UP_MAINTENANCE_ACTIONS.has(action) ? " *" : ""}</Button>)}<label className="mt-1 w-full"><span className="text-[10px] uppercase tracking-wider text-slate-500">Password (* actions and provider priority only)</span><input type="password" autoComplete="current-password" value={stepUpPassword} onChange={(event) => setStepUpPassword(event.target.value)} placeholder="Confirm to authorise" className="mt-1 h-11 w-full rounded-md border border-slate-800 bg-terminal-950 px-3 text-xs text-slate-300"/></label><p className="w-full text-[10px] leading-4 text-slate-500">Starred actions require a single-use step-up token; the password is exchanged for one token per action and never stored.</p>{maintenance.isError && <p role="alert" className="mt-2 w-full text-xs text-loss">{formatApiError(maintenance.error)}</p>}</CardContent></Card>
      </div>
      <Card className="mt-3"><CardHeader><CardTitle>Live provider priority</CardTitle></CardHeader><CardContent><p className="mb-3 text-xs leading-5 text-slate-500">Comma-separated allowlisted adapters. Repeatedly failing providers are demoted in memory without changing this configured order.</p><div className="flex flex-col gap-2 sm:flex-row"><label className="flex-1"><span className="sr-only">Provider order</span><input value={providerOrder} onChange={(event) => setProviderOrder(event.target.value)} className="h-11 w-full rounded-md border border-slate-800 bg-terminal-950 px-3 font-mono text-xs text-slate-300"/></label><Button variant="ghost" disabled={reorderProviders.isPending} onClick={() => reorderProviders.mutate()}>{reorderProviders.isPending ? "Applying…" : "Apply priority"}</Button></div>{reorderProviders.isError && <p role="alert" className="mt-2 text-xs text-loss">{formatApiError(reorderProviders.error)}</p>}</CardContent></Card>

      <AdminOperationsPanel />

      <section className="mt-5" aria-labelledby="audit-title">
        <div className="mb-3 flex flex-wrap items-center gap-3"><h2 id="audit-title" className="display-font text-lg font-semibold text-white">Audit log</h2><label className="relative ml-auto"><span className="sr-only">Filter audit events</span><Search aria-hidden="true" className="absolute left-3 top-3.5 text-slate-500" size={14}/><input value={auditSearch} onChange={(event) => setAuditSearch(event.target.value)} placeholder="Filter events" className="h-11 w-52 rounded-md border border-slate-800 bg-terminal-900 pl-9 pr-3 text-xs text-slate-300 outline-none focus:border-accent/50 sm:w-64"/></label><label><span className="sr-only">Audit event count</span><select value={auditLimit} onChange={(event) => setAuditLimit(Number(event.target.value))} className="h-11 rounded-md border border-slate-800 bg-terminal-900 px-3 text-xs text-slate-300"><option value={25}>25 events</option><option value={50}>50 events</option><option value={100}>100 events</option></select></label></div>
        {audit.isError && <p role="alert" className="mb-2 text-xs text-loss">{formatApiError(audit.error)}</p>}
        <div className="overflow-x-auto rounded-xl border border-slate-800"><table className="w-full min-w-[720px] text-sm"><thead className="text-left text-[10px] uppercase tracking-wider text-slate-500"><tr><th className="px-4 py-3">Time</th><th>Actor</th><th>Action</th><th>Outcome</th><th>Details</th></tr></thead><tbody>{filteredAudit.length === 0 && <tr><td colSpan={5} className="px-4 py-8 text-center text-slate-500">No events reported</td></tr>}{filteredAudit.map((event) => <tr key={event.id} className="border-t border-slate-800/70"><td className="whitespace-nowrap px-4 py-3 text-xs text-slate-500">{new Date(event.created_at).toLocaleString()}</td><td className="text-xs">{event.actor_email}</td><td className="text-xs">{event.action}</td><td><span className={`rounded px-2 py-1 text-[10px] font-medium ${event.outcome === "ok" ? "bg-gain/10 text-gain" : event.outcome === "failed" ? "bg-loss/10 text-loss" : "bg-slate-800 text-slate-400"}`}>{event.outcome === "ok" ? "Success: " : event.outcome === "failed" ? "Failed: " : "Status: "}{event.outcome}</span></td><td className="max-w-48 truncate text-xs text-slate-500">{event.detail ? JSON.stringify(event.detail) : "Unavailable"}</td></tr>)}</tbody></table></div>
      </section>
    </TerminalShell>
  );
}

export default function AdminPage() {
  const { status, isAdmin } = useAuth();
  const router = useRouter();
  useEffect(() => { if (status === "unauthenticated") router.replace("/login?next=/admin"); }, [status, router]);
  if (status === "loading") return <TerminalShell><div className="grid min-h-[60vh] place-items-center text-slate-500" role="status">Checking permissions</div></TerminalShell>;
  if (!isAdmin) return <TerminalShell><div className="grid min-h-[60vh] place-items-center text-center"><div><AlertTriangle aria-hidden="true" className="mx-auto mb-3 text-warning" size={28}/><h1 className="text-lg font-semibold text-white">Access restricted</h1><p className="mt-1 text-sm text-slate-500">This workspace is limited to configured administrators.</p></div></div></TerminalShell>;
  return <AdminWorkspace />;
}
