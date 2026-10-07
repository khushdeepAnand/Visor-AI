"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { CircleUserRound, LogOut, ShieldCheck, Trash2 } from "lucide-react";
import { TerminalShell } from "@/components/TerminalShell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/lib/auth";
import { api, formatApiError } from "@/lib/api";
import { PasskeyPanel } from "@/components/PasskeyPanel";

type OAuthStatus = { google: { configured: boolean } };
type Session = { id: number; issued_at: string; expires_at: string; revoked_at?: string | null; device_label: string; current: boolean };
type MfaStatus = { enabled: boolean; totp_enabled?: boolean; recovery_codes_remaining: number };
type MfaSetup = { secret: string; provisioning_uri: string };

export default function AccountPage() {
  const { status: sessionStatus, user, isAdmin, signOut, error: sessionError } = useAuth();
  const router = useRouter();
  const qc = useQueryClient();
  useEffect(() => { if (sessionStatus === "unauthenticated") router.replace("/login?next=/account"); }, [sessionStatus, router]);
  const oauth = useQuery({ queryKey: ["oauth-status"], queryFn: () => api<OAuthStatus>("/api/v1/auth/oauth/status"), staleTime: 60_000, retry: false, enabled: sessionStatus === "authenticated" });
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: () => api<{items: Session[]}>("/api/v1/auth/sessions"), enabled: sessionStatus === "authenticated" });
  const mfa = useQuery({ queryKey: ["mfa-status"], queryFn: () => api<MfaStatus>("/api/v1/auth/mfa"), enabled: sessionStatus === "authenticated" });
  const logout = useMutation({ mutationFn: signOut, onSuccess: () => router.replace("/login") });
  const logoutAll = useMutation({ mutationFn: async () => { await api("/api/v1/auth/logout-all", { method: "POST" }); await signOut(); }, onSuccess: () => router.replace("/login") });
  const revokeOne = useMutation({ mutationFn: (id: number) => api(`/api/v1/auth/sessions/${id}`, { method: "DELETE" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["sessions"] }) });
  const [deleteConfirmation, setDeleteConfirmation] = useState("");
  const [mfaSetup, setMfaSetup] = useState<MfaSetup | null>(null);
  const [mfaCode, setMfaCode] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const setupMfa = useMutation({ mutationFn: () => api<MfaSetup>("/api/v1/auth/mfa/setup", { method: "POST" }), onSuccess: setMfaSetup });
  const enableMfa = useMutation({
    mutationFn: () => api<{ enabled: boolean; recovery_codes: string[] }>("/api/v1/auth/mfa/enable", { method: "POST", body: JSON.stringify({ code: mfaCode }) }),
    onSuccess: async (result) => { setRecoveryCodes(result.recovery_codes); setMfaSetup(null); setMfaCode(""); await mfa.refetch(); await qc.invalidateQueries({ queryKey: ["me"] }); },
  });
  const regenerateCodes = useMutation({
    mutationFn: () => api<{ recovery_codes: string[] }>("/api/v1/auth/mfa/recovery-codes", { method: "POST", body: JSON.stringify({ code: mfaCode }) }),
    onSuccess: async (result) => { setRecoveryCodes(result.recovery_codes); setMfaCode(""); await mfa.refetch(); },
  });
  const disableMfa = useMutation({
    mutationFn: () => api<{ enabled: boolean }>("/api/v1/auth/mfa/disable", { method: "POST", body: JSON.stringify({ code: mfaCode }) }),
    onSuccess: async () => { setRecoveryCodes([]); setMfaCode(""); await mfa.refetch(); await qc.invalidateQueries({ queryKey: ["me"] }); await qc.invalidateQueries({ queryKey: ["sessions"] }); },
  });
  const deleteAccount = useMutation({
    mutationFn: () => api("/api/v1/auth/account", { method: "DELETE", body: JSON.stringify({ confirmation: "DELETE" }) }),
    onSuccess: () => { qc.clear(); router.replace("/"); },
  });

  if (sessionStatus === "loading") return <TerminalShell><div className="grid min-h-[60vh] place-items-center text-slate-500">Loading account…</div></TerminalShell>;
  if (sessionStatus === "error" || sessionStatus === "unauthenticated") return <TerminalShell><div className="grid min-h-[60vh] place-items-center text-center"><div><CircleUserRound className="mx-auto mb-3 text-slate-600" size={28}/><h1 className="text-lg font-semibold text-white">Sign in required</h1><p className="mt-1 text-sm text-slate-500">{sessionError ? formatApiError(sessionError) : "Your session could not be loaded."}</p><Button className="mt-4" onClick={() => router.replace("/login?next=/account")}>Sign in</Button></div></div></TerminalShell>;

  const providers = (user?.connected_providers ?? []).filter((p) => p !== "apple");
  const primary = user?.auth_provider ?? "password";
  const googleOk = Boolean(oauth.data?.google.configured);
  const totpEnabled = mfa.data?.totp_enabled ?? mfa.data?.enabled;

  return (
    <TerminalShell>
      <div className="mb-5">
        <div className="flex items-center gap-2 text-accent"><CircleUserRound size={17}/><span className="text-[10px] uppercase tracking-[.2em]">Account</span></div>
        <h1 className="mt-1 text-2xl font-semibold text-white">Your workspace</h1>
        <p className="mt-1 text-sm text-slate-500">Manage identity, connected providers, and session.</p>
      </div>

      <div className="grid gap-4 md:grid-cols-[1fr_360px]">
        <div className="space-y-4">
          <Card className="panel-glow"><CardHeader><CardTitle>Profile</CardTitle></CardHeader><CardContent className="space-y-3">
            <div className="flex items-center gap-3"><div className="grid size-12 place-items-center rounded-full border border-slate-800 bg-slate-900 text-slate-400"><CircleUserRound size={22}/></div><div><div className="text-lg font-semibold text-white">{user?.name || "Account holder"}</div><div className="text-sm text-slate-500">{user?.email || "Email unavailable"}</div></div></div>
            <div className="flex flex-wrap gap-2 text-xs">
              <span className="rounded border border-slate-800 bg-slate-900 px-2 py-1 text-slate-400">Role: <b className="text-white">{user?.role || "user"}</b></span>
              <span className="rounded border border-slate-800 bg-slate-900 px-2 py-1 text-slate-400">Primary: <b className="text-white">{primary}</b></span>
              {user?.created_at && <span className="rounded border border-slate-800 bg-slate-900 px-2 py-1 text-slate-400">Joined: <b className="text-white">{new Date(user.created_at).toLocaleDateString()}</b></span>}
            </div>
            {isAdmin && <div className="flex items-center gap-2 rounded border border-accent/25 bg-accent/5 px-3 py-2 text-xs text-accent"><ShieldCheck size={14}/>Administrator access is active for this session.</div>}
          </CardContent></Card>

          <Card><CardHeader><CardTitle>Research & paper-only scope</CardTitle></CardHeader><CardContent className="text-sm leading-5 text-slate-400">
            Your account supports saved portfolios, alerts, paper simulations, and calibration history. Broker execution is never enabled from this workspace.
          </CardContent></Card>

          <Card className="panel-glow"><CardHeader><CardTitle>Two-factor authentication</CardTitle></CardHeader><CardContent className="space-y-3">
            <div className="flex items-center justify-between rounded border border-slate-800 bg-slate-900 px-3 py-2 text-xs"><span className="text-slate-400">Authenticator protection</span><span className={totpEnabled ? "text-gain" : "text-slate-500"}>{totpEnabled ? "enabled" : "not enabled"}</span></div>
            {!totpEnabled && !mfaSetup && <Button className="w-full" onClick={() => setupMfa.mutate()} disabled={setupMfa.isPending}>{setupMfa.isPending ? "Preparing…" : "Set up authenticator"}</Button>}
            {mfaSetup && <div className="space-y-3 rounded border border-accent/25 bg-accent/5 p-3">
              <p className="text-xs leading-5 text-slate-300">Add this account to your authenticator using the setup key or provisioning URI. The key is shown only during this setup.</p>
              <div><div className="text-[10px] uppercase tracking-wider text-slate-500">Setup key</div><code className="mt-1 block break-all text-sm text-accent">{mfaSetup.secret}</code></div>
              <details className="text-xs text-slate-500"><summary className="cursor-pointer text-slate-400">Show provisioning URI</summary><code className="mt-2 block break-all">{mfaSetup.provisioning_uri}</code></details>
              <label htmlFor="mfa-enable-code" className="block text-xs text-slate-300">Current 6-digit code</label>
              <Input id="mfa-enable-code" value={mfaCode} onChange={(event) => setMfaCode(event.target.value)} inputMode="numeric" autoComplete="one-time-code" placeholder="123456" />
              {enableMfa.error && <p role="alert" className="text-xs text-loss">{formatApiError(enableMfa.error)}</p>}
              <Button className="w-full" disabled={mfaCode.trim().length < 6 || enableMfa.isPending} onClick={() => enableMfa.mutate()}>{enableMfa.isPending ? "Verifying…" : "Verify and enable"}</Button>
            </div>}
            {recoveryCodes.length > 0 && <div className="rounded border border-warning/30 bg-warning/5 p-3">
              <p className="text-xs font-medium text-warning">Store these one-time recovery codes securely. They will not be shown again.</p>
              <div className="mt-3 grid grid-cols-2 gap-1 font-mono text-xs text-slate-200">{recoveryCodes.map((code) => <span key={code}>{code}</span>)}</div>
              <Button variant="ghost" className="mt-3 w-full" onClick={() => setRecoveryCodes([])}>I have stored these codes</Button>
            </div>}
            {totpEnabled && mfa.data && <div className="space-y-3">
              <p className="text-xs leading-5 text-slate-500">{mfa.data.recovery_codes_remaining} unused recovery code(s) remain. Enter a current authenticator or unused recovery code to rotate codes or disable protection.</p>
              <label htmlFor="mfa-current-code" className="block text-xs text-slate-300">Current second factor</label>
              <Input id="mfa-current-code" value={mfaCode} onChange={(event) => setMfaCode(event.target.value)} autoComplete="one-time-code" placeholder="123456 or XXXX-XXXX" />
              {(regenerateCodes.error || disableMfa.error) && <p role="alert" className="text-xs text-loss">{formatApiError(regenerateCodes.error || disableMfa.error)}</p>}
              <div className="grid gap-2 sm:grid-cols-2"><Button variant="ghost" disabled={mfaCode.trim().length < 6 || regenerateCodes.isPending} onClick={() => regenerateCodes.mutate()}>Rotate recovery codes</Button><Button variant="ghost" className="text-loss border-loss/25" disabled={mfaCode.trim().length < 6 || disableMfa.isPending} onClick={() => disableMfa.mutate()}>Disable 2FA</Button></div>
            </div>}
          </CardContent></Card>
          <PasskeyPanel />
        </div>

        <div className="space-y-4">
          <Card className="panel-glow"><CardHeader><CardTitle>Connected providers</CardTitle></CardHeader><CardContent className="space-y-2 text-sm">
            {providers.length === 0 && <p className="text-slate-500">No external identity providers are linked.</p>}
            {providers.map(p => (
              <div key={p} className="flex items-center justify-between rounded border border-slate-800 bg-slate-900 px-3 py-2.5"><span className="text-slate-300">{p === "google" ? "Google" : p}</span><span className="text-[10px] text-gain">connected</span></div>
            ))}
            <div className="mt-2 text-xs text-slate-500">
              Google: {googleOk ? "available" : "not configured"}
            </div>
          </CardContent></Card>

          <Card className="border-loss/25"><CardHeader><CardTitle className="text-loss">Session</CardTitle></CardHeader><CardContent>
            <p className="mb-3 text-xs leading-5 text-slate-500">{sessions.data?.items?.filter((item) => !item.revoked_at).length || 1} active session(s). Labels are derived from the browser user agent; no IP address or device fingerprint is stored.</p>
            <div className="mb-3 max-h-44 space-y-1 overflow-auto">{sessions.data?.items?.filter((item) => !item.revoked_at).map((item) => <div key={item.id} className="flex items-center justify-between gap-3 rounded border border-slate-800 px-2 py-1.5 text-[10px] text-slate-500"><span><b className="block text-slate-300">{item.device_label}{item.current ? " · current" : ""}</b>Issued {new Date(item.issued_at).toLocaleString()}</span><button type="button" disabled={item.current} onClick={() => revokeOne.mutate(item.id)} className="min-h-8 px-2 text-loss disabled:text-slate-700" aria-label={item.current ? "Current session" : `Revoke ${item.device_label} session`}>{item.current ? "Current" : "Revoke"}</button></div>)}</div>
            <Button variant="ghost" className="w-full gap-2 text-loss border-loss/25 hover:bg-loss/10" disabled={logout.isPending} onClick={() => logout.mutate()}>
              <LogOut size={14}/>{logout.isPending ? "Signing out…" : "Sign out"}
            </Button>
            <Button variant="ghost" className="mt-2 w-full gap-2 text-loss border-loss/25 hover:bg-loss/10" disabled={logoutAll.isPending} onClick={() => logoutAll.mutate()}>
              <ShieldCheck size={14}/>{logoutAll.isPending ? "Revoking…" : "Revoke all active sessions"}
            </Button>
          </CardContent></Card>

          <Card className="border-loss/30"><CardHeader><CardTitle className="text-loss">Delete account and data</CardTitle></CardHeader><CardContent className="space-y-3">
            <p className="text-xs leading-5 text-slate-500">Permanently purges your profile, authentication records, forecasts, portfolio, alerts, saved research, paper activity, and user-linked audit history. This cannot be undone.</p>
            <label htmlFor="delete-confirmation" className="block text-xs text-slate-400">Type <b className="text-slate-200">DELETE</b> to confirm</label>
            <Input id="delete-confirmation" value={deleteConfirmation} onChange={(event) => setDeleteConfirmation(event.target.value)} autoComplete="off" />
            {deleteAccount.error && <p role="alert" className="text-xs text-loss">{formatApiError(deleteAccount.error)}</p>}
            <Button variant="ghost" className="w-full gap-2 text-loss border-loss/30 hover:bg-loss/10" disabled={deleteConfirmation !== "DELETE" || deleteAccount.isPending} onClick={() => deleteAccount.mutate()}>
              <Trash2 size={14}/>{deleteAccount.isPending ? "Deleting account…" : "Permanently delete account"}
            </Button>
          </CardContent></Card>
        </div>
      </div>
    </TerminalShell>
  );
}
