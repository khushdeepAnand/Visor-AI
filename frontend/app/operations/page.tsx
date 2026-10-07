"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { TerminalShell } from "@/components/TerminalShell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import { api, formatApiError } from "@/lib/api";
import { ComplianceOperations } from "@/components/ComplianceOperations";
import { QueueOperations } from "@/components/QueueOperations";

type History = { action: string; actor: string; reason: string; recorded_at: string; manifest: { receipt: { candidate_id: string; evaluated_at: string; artifact_hash: string }; gate_summary: unknown } };
type Registry = { status: { active: boolean; candidate_id: string | null; inactive_reason: string | null }; history: History[]; controls: Record<string, unknown>[] };
type Account = { id: number; name: string; email: string; created_at: string; account_status: string; mfa_enabled: boolean; anomalous_login: boolean };

export default function OperationsPage() {
  const { user, status } = useAuth();
  const role = user?.role;
  const modelsAllowed = status === "authenticated" && (role === "admin" || role === "model-ops");
  const accountsAllowed = status === "authenticated" && (role === "admin" || role === "support-ops");
  const client = useQueryClient();
  const [reason, setReason] = useState("");
  const [search, setSearch] = useState("");
  const [mfa, setMfa] = useState("");
  const [age, setAge] = useState("");
  const [anomalous, setAnomalous] = useState("");
  const [selected, setSelected] = useState<number[]>([]);
  const [accountAction, setAccountAction] = useState("force_logout");
  const [accountReason, setAccountReason] = useState("");
  const [password, setPassword] = useState("");
  const registry = useQuery({ queryKey: ["operations-models", user?.id], queryFn: () => api<Registry>("/api/v1/admin/model-operations"), enabled: modelsAllowed, retry: false });
  const accounts = useQuery({ queryKey: ["operations-users", user?.id, search, mfa, age, anomalous], queryFn: () => api<{ items: Account[] }>(`/api/v1/admin/users?search=${encodeURIComponent(search)}${mfa ? `&mfa=${mfa}` : ""}${age ? `&min_age_days=${age}` : ""}${anomalous ? `&anomalous=${anomalous}` : ""}`), enabled: accountsAllowed, retry: false });
  const bulk = useMutation({ mutationFn: async () => {
    const ids = [...selected].sort((a, b) => a - b);
    const grant = await api<{ step_up_token: string }>("/api/v1/admin/step-up", { method: "POST", body: JSON.stringify({ password, action: "admin_change", target: `accounts:${accountAction}:${ids.join(",")}` }) });
    setPassword("");
    return api("/api/v1/admin/accounts/bulk", { method: "POST", headers: { "X-Step-Up-Token": grant.step_up_token }, body: JSON.stringify({ account_ids: ids, action: accountAction, reason: accountReason }) });
  }, onSuccess: () => { setSelected([]); setAccountReason(""); client.invalidateQueries({ queryKey: ["operations-users"] }); } });
  const action = useMutation({
    mutationFn: ({ path, body, method }: { path: string; body: object; method: string }) => api(path, { method, body: JSON.stringify({ ...body, reason }) }),
    onSuccess: () => { setReason(""); client.invalidateQueries({ queryKey: ["operations-models"] }); },
  });
  return <TerminalShell>
    <h1 className="text-2xl font-semibold">Operational workspace</h1>
    <p className="mt-2 text-sm text-slate-400">Role: {role ?? "signed out"}. Every model change requires a justification.</p>
    {!modelsAllowed && !accountsAllowed && <p role="status" className="mt-6">Sign in with a configured operational account to use this workspace.</p>}
    {modelsAllowed && <Card className="mt-5"><CardHeader><CardTitle>Model operations</CardTitle></CardHeader><CardContent>
      {registry.isError && <p role="alert">{formatApiError(registry.error)}</p>}
      {registry.isPending && <p role="status">Loading verified registry…</p>}
      {registry.data && <>
        <p>Current candidate: {registry.data.status.candidate_id ?? "None"}. {registry.data.status.active ? "Active gated receipt" : registry.data.status.inactive_reason}</p>
        <label className="mt-4 block">Justification<input value={reason} onChange={e => setReason(e.target.value)} minLength={12} maxLength={500} className="mt-1 min-h-11 w-full rounded border border-slate-700 bg-terminal-950 px-3" /></label>
        <section aria-label="Tier forecasting controls" className="mt-4 grid gap-3 sm:grid-cols-5">{["T0", "T1", "T2", "T3", "T4"].map(tier => {
          const paused = registry.data.controls.some(c => c.tier === tier && c.paused === true);
          return <Button key={tier} disabled={reason.trim().length < 12 || action.isPending} aria-pressed={paused} onClick={() => action.mutate({ path: "/api/v1/admin/model-operations/pause", method: "PUT", body: { tier, paused: !paused } })}>{paused ? "Resume" : "Pause"} {tier}</Button>;
        })}</section>
        <h2 className="mt-5 font-semibold">Verified promotion history</h2>
        <ul className="mt-3 space-y-3">{registry.data.history.slice().reverse().map((event, index) => <li key={`${event.recorded_at}-${index}`} className="rounded border border-slate-700 p-3">
          <p>{event.action}: {event.manifest.receipt.candidate_id}</p><p className="mt-1 text-sm text-slate-400">{event.reason} · Evidence {event.manifest.receipt.evaluated_at}</p>
          <details className="mt-2"><summary>Gate scorecard</summary><pre className="overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(event.manifest.gate_summary, null, 2)}</pre></details>
          {event.action === "promote" && <Button className="mt-2" disabled={reason.trim().length < 12 || action.isPending} onClick={() => action.mutate({ path: "/api/v1/admin/model-operations/rollback", method: "POST", body: { candidate_id: event.manifest.receipt.candidate_id } })}>Rollback to {event.manifest.receipt.candidate_id}</Button>}
        </li>)}</ul>
        <details className="mt-4"><summary>Live-decay controls</summary><pre className="overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(registry.data.controls, null, 2)}</pre></details>
      </>}
      {action.isError && <p role="alert" className="mt-3 text-loss">{formatApiError(action.error)}</p>}
      {action.isSuccess && <p role="status" className="mt-3">Model control saved and audited.</p>}
    </CardContent></Card>}
    {accountsAllowed && <Card className="mt-5"><CardHeader><CardTitle>Account directory</CardTitle></CardHeader><CardContent>
      <div className="flex flex-wrap gap-3"><label>Search accounts<input value={search} onChange={e => setSearch(e.target.value)} className="mt-1 block min-h-11 rounded border border-slate-700 bg-terminal-950 px-3" /></label><label>MFA status<select value={mfa} onChange={e => setMfa(e.target.value)} className="mt-1 block min-h-11 rounded border border-slate-700 bg-terminal-950 px-3"><option value="">All</option><option value="true">Enabled</option><option value="false">Disabled</option></select></label></div>
      {accounts.isError && <p role="alert">{formatApiError(accounts.error)}</p>}
      <div className="mt-3 flex flex-wrap gap-3"><label>Minimum account age (days)<input type="number" min={0} max={36500} value={age} onChange={e => setAge(e.target.value)} className="mt-1 block min-h-11 rounded border border-slate-700 bg-terminal-950 px-3" /></label><label>Unacknowledged login anomaly<select value={anomalous} onChange={e => setAnomalous(e.target.value)} className="mt-1 block min-h-11 rounded border border-slate-700 bg-terminal-950 px-3"><option value="">All</option><option value="true">Flagged</option><option value="false">Clear</option></select></label></div>
      <div className="mt-4 overflow-x-auto"><table className="w-full text-left text-sm"><caption className="sr-only">Operational account metadata</caption><thead><tr>{["Account", "Created", "Status", "MFA", "Login anomaly"].map(label => <th scope="col" key={label} className="p-3">{label}</th>)}</tr></thead><tbody>{accounts.data?.items.map(account => <tr key={account.id} className="border-t border-slate-700"><td className="p-3">{role === "admin" && <input type="checkbox" aria-label={`Select ${account.email}`} checked={selected.includes(account.id)} onChange={e => setSelected(ids => e.target.checked ? [...ids, account.id] : ids.filter(id => id !== account.id))} className="mr-2 size-5" />}{account.name}<br />{account.email}</td><td className="p-3">{account.created_at}</td><td className="p-3">{account.account_status}</td><td className="p-3">{account.mfa_enabled ? "Enabled" : "Disabled"}</td><td className="p-3">{account.anomalous_login ? "Flagged" : "Clear"}</td></tr>)}</tbody></table></div>
      {role === "admin" && <section aria-label="Bulk account controls" className="mt-4 grid gap-3 sm:grid-cols-2"><label>Account action<select value={accountAction} onChange={e => setAccountAction(e.target.value)} className="mt-1 block min-h-11 w-full rounded border border-slate-700 bg-terminal-950 px-3">{["force_logout", "force_mfa_reset", "suspend", "reinstate", "reset_lockout"].map(value => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}</select></label><label>Confirm admin password<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} className="mt-1 block min-h-11 w-full rounded border border-slate-700 bg-terminal-950 px-3" /></label><label>Account action justification<input value={accountReason} onChange={e => setAccountReason(e.target.value)} maxLength={160} className="mt-1 block min-h-11 w-full rounded border border-slate-700 bg-terminal-950 px-3" /></label><Button disabled={!selected.length || selected.length > 100 || !password || accountReason.trim().length < 12 || bulk.isPending} onClick={() => bulk.mutate()}>Apply to {selected.length} selected account(s)</Button>{bulk.isError && <p role="alert">{formatApiError(bulk.error)}</p>}{bulk.isSuccess && <p role="status">Accounts updated and audited.</p>}</section>}
    </CardContent></Card>}
    {accountsAllowed && <ComplianceOperations editable={role === "admin"} />}
    {role === "admin" && <QueueOperations />}
  </TerminalShell>;
}
