"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, formatApiError } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

type Review = { id: string; label: string; status: string; note: string };
type Compliance = { version: string; checklist: Review[]; accounts: { user_id: number; email: string; required: boolean; acknowledged_at: string | null }[] };
const field = "mt-1 block min-h-11 w-full rounded border border-slate-700 bg-terminal-950 px-3";

function ReviewEditor({ item }: { item: Review }) {
  const client = useQueryClient();
  const [status, setStatus] = useState(item.status);
  const [note, setNote] = useState(item.note);
  const save = useMutation({ mutationFn: () => api(`/api/v1/admin/compliance/${item.id}`, { method: "PUT", body: JSON.stringify({ status, note }) }), onSuccess: () => client.invalidateQueries({ queryKey: ["compliance-operations"] }) });
  return <div className="mt-3 grid gap-3 sm:grid-cols-2"><label>Review status<select value={status} onChange={e => setStatus(e.target.value)} className={field}>{["open", "in_progress", "complete", "blocked"].map(value => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}</select></label><label>Evidence reference or justification<input value={note} onChange={e => setNote(e.target.value)} maxLength={500} className={field} /></label><Button disabled={note.trim().length < 12 || save.isPending} onClick={() => save.mutate()}>Save review</Button>{save.isError && <p role="alert">{formatApiError(save.error)}</p>}{save.isSuccess && <p role="status">Review saved and audited.</p>}</div>;
}

export function ComplianceOperations({ editable }: { editable: boolean }) {
  const data = useQuery({ queryKey: ["compliance-operations"], queryFn: () => api<Compliance>("/api/v1/admin/compliance"), retry: false });
  return <Card className="mt-5"><CardHeader><CardTitle>Compliance operations</CardTitle></CardHeader><CardContent>
    <p className="text-sm text-slate-400">Disclosure version: {data.data?.version ?? "Loading…"}. Version changes require users to review and acknowledge again.</p>
    {data.isError && <p role="alert">{formatApiError(data.error)}</p>}
    <ul className="mt-4 space-y-3">{data.data?.checklist.map(item => <li key={item.id} className="rounded border border-slate-700 p-3"><h3 className="font-medium">{item.label}</h3><p className="mt-1 text-sm">Status: {item.status.replaceAll("_", " ")}</p><p className="mt-1 text-sm text-slate-400">{item.note || "No evidence recorded"}</p>{editable && <details className="mt-2"><summary>Edit review</summary><ReviewEditor item={item} /></details>}</li>)}</ul>
    <details className="mt-4"><summary>User disclosure acknowledgments</summary><ul className="mt-3 space-y-2">{data.data?.accounts.map(account => <li key={account.user_id} className="flex flex-wrap justify-between gap-2 border-b border-slate-700 py-2"><span>{account.email}</span><span>{account.required ? "Review required" : `Acknowledged ${account.acknowledged_at}`}</span></li>)}</ul></details>
  </CardContent></Card>;
}
