"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, formatApiError } from "@/lib/api";
import { registerPasskey } from "@/lib/passkeys";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

export function PasskeyPanel() {
  const client = useQueryClient();
  const credentials = useQuery({ queryKey: ["passkeys"], queryFn: () => api<{ available: boolean; credentials: { id: number; label: string; created_at: string }[] }>("/api/v1/auth/webauthn/credentials"), retry: false });
  const refresh = () => { client.invalidateQueries({ queryKey: ["passkeys"] }); client.invalidateQueries({ queryKey: ["mfa-status"] }); };
  const add = useMutation({ mutationFn: registerPasskey, onSuccess: refresh });
  const remove = useMutation({ mutationFn: (id: number) => api(`/api/v1/auth/webauthn/credentials/${id}`, { method: "DELETE" }), onSuccess: refresh });
  return <Card><CardHeader><CardTitle>Passkeys</CardTitle></CardHeader><CardContent className="space-y-3">
    <p className="text-sm text-slate-400">Use a device passkey as your second factor after password sign-in. Keep a second device or authenticator available for recovery.</p>
    <Button onClick={() => add.mutate()} disabled={!credentials.data?.available || add.isPending}>{add.isPending ? "Waiting for your device…" : "Add passkey"}</Button>
    {credentials.data?.credentials.map(key => <div key={key.id} className="flex flex-wrap items-center justify-between gap-3 rounded border border-slate-700 p-3"><span>{key.label || "Passkey"} · {new Date(key.created_at).toLocaleDateString()}</span><Button variant="ghost" aria-label={`Remove ${key.label || "passkey"}`} disabled={remove.isPending} onClick={() => remove.mutate(key.id)}>Remove</Button></div>)}
    {add.isSuccess && <p role="status">Passkey added</p>}
    {(credentials.error || add.error || remove.error) && <p role="alert">{formatApiError(credentials.error || add.error || remove.error)}</p>}
  </CardContent></Card>;
}
