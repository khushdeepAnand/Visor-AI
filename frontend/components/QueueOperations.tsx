"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, formatApiError } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

type QueueSnapshot = { health: { enabled: boolean; broker_connected: boolean; dead_letter_count: number }; dead_letters: { id: string; task: string; failed_at: string; attempts: number }[]; schedules: { name: string; task: string; interval_seconds: number }[]; schedule_note: string };

export function QueueOperations() {
  const client = useQueryClient();
  const [password, setPassword] = useState("");
  const [reason, setReason] = useState("");
  const snapshot = useQuery({ queryKey: ["queue-operations"], queryFn: () => api<QueueSnapshot>("/api/v1/admin/queue"), retry: false, refetchInterval: 30_000 });
  const run = useMutation({ mutationFn: async ({ kind, id }: { kind: "trigger" | "replay"; id: string }) => {
    const grant = await api<{ step_up_token: string }>("/api/v1/admin/step-up", { method: "POST", body: JSON.stringify({ password, action: "admin_change", target: `queue:${kind}:${id}` }) });
    setPassword("");
    if (kind === "trigger") return api(`/api/v1/admin/queue/trigger/${id}`, { method: "POST", headers: { "X-Step-Up-Token": grant.step_up_token }, body: JSON.stringify({ reason }) });
    return api(`/api/v1/admin/queue/replay/${id}`, { method: "POST", headers: { "X-Step-Up-Token": grant.step_up_token }, body: JSON.stringify({ reason }) });
  }, onSuccess: () => client.invalidateQueries({ queryKey: ["queue-operations"] }) });
  const disabled = !password || reason.trim().length < 12 || run.isPending;
  return <Card className="mt-5"><CardHeader><CardTitle>Task queue & scheduled jobs</CardTitle></CardHeader><CardContent className="space-y-4">
    <p>{snapshot.data?.health.enabled ? "Durable queue enabled" : "Local inline execution"} · Broker {snapshot.data?.health.broker_connected ? "connected" : "unavailable"}</p>
    <div className="grid gap-3 sm:grid-cols-2"><label>Confirm admin password<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} className="mt-1 block min-h-11 w-full rounded border border-slate-700 bg-terminal-950 px-3" /></label><label>Job action justification<input value={reason} onChange={e => setReason(e.target.value)} maxLength={160} className="mt-1 block min-h-11 w-full rounded border border-slate-700 bg-terminal-950 px-3" /></label></div>
    <div className="flex flex-wrap gap-3">{["retention", "instruments", "backups", "decay"].map(name => <Button key={name} disabled={disabled} onClick={() => run.mutate({ kind: "trigger", id: name })}>Run {name}</Button>)}</div>
    <h3 className="font-medium">Dead letters</h3>
    {!snapshot.data?.dead_letters.length && <p className="text-sm text-slate-400">No entries reported. Check broker health before treating this as an empty queue.</p>}
    <ul className="space-y-2">{snapshot.data?.dead_letters.map(entry => <li key={entry.id} className="flex flex-wrap items-center justify-between gap-3 rounded border border-slate-700 p-3"><span>{entry.task} · {entry.failed_at} · attempts {entry.attempts}</span><Button disabled={disabled} onClick={() => run.mutate({ kind: "replay", id: entry.id })}>Retry job</Button></li>)}</ul>
    <details><summary>Configured schedules</summary><ul className="mt-2">{snapshot.data?.schedules.map(job => <li key={job.name}>{job.name}: every {job.interval_seconds} seconds</li>)}</ul><p className="mt-2 text-sm text-slate-400">{snapshot.data?.schedule_note}</p></details>
    {(snapshot.error || run.error) && <p role="alert">{formatApiError(snapshot.error || run.error)}</p>}{run.isSuccess && <p role="status">Job accepted and action audited.</p>}
  </CardContent></Card>;
}
