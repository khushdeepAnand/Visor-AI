"use client";
import { Suspense, FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { safeNextPath, useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Eye, EyeOff } from "lucide-react";
import { OAuthButtons } from "@/components/OAuthButtons";

function LoginContent() {
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [ready, setReady] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const router = useRouter();
  const params = useSearchParams();
  const next = safeNextPath(params.get("next"));
  const oauthError = params.get("oauth_error");
  const [mfaRequired, setMfaRequired] = useState(params.get("mfa") === "required");
  const supportId = params.get("support_id");
  const oauthMessage: Record<string, string> = {
    oauth_cancelled: "Google sign-in was cancelled. No account changes were made.",
    oauth_state_invalid: "The sign-in request expired or could not be verified. Start again from this page.",
    oauth_provider_unavailable: "Google sign-in is temporarily unavailable. Use password sign-in or retry later.",
    oauth_identity_invalid: "Google did not return a verified identity that StockPilot can accept.",
    oauth_link_refused: "That Google identity could not be safely linked to this account.",
    oauth_id_token_expired: "The Google sign-in session expired. Please sign in again.",
    oauth_id_token_missing: "Google did not return an identity token for this sign-in.",
    oauth_nonce_invalid: "The Google sign-in request could not be verified. Please try again.",
    oauth_id_token_invalid: "The Google identity could not be validated. Please sign in again.",
    oauth_email_unverified: "Your Google email address is not verified by Google.",
    oauth_profile_incomplete: "The Google account did not return a usable email address.",
    oauth_code_missing: "Google did not return an authorization code. Please try again.",
    google_unavailable: "Google sign-in has not been configured for this environment.",
  };
  const { status, refresh } = useAuth();
  useEffect(() => setReady(true), []);

  // Reverse guard: an already-signed-in visitor is sent on instead of being
  // shown a login form that would immediately be redundant.
  useEffect(() => {
    if (status === "authenticated") router.replace(next);
  }, [status, next, router]);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError("");
    setPending(true);
    const f = new FormData(e.currentTarget);
    try {
      if (mfaRequired) {
        const result = await api<{ next?: string }>("/api/v1/auth/mfa/verify", {
          method: "POST",
          body: JSON.stringify({ code: f.get("code") }),
        });
        await refresh();
        router.replace(safeNextPath(result.next || next));
        return;
      }
      const result = await api<{ mfa_required?: boolean }>("/api/v1/auth/login", {
        method: "POST",
        body: JSON.stringify({ email: f.get("email"), password: f.get("password"), next }),
      });
      if (result.mfa_required) {
        setMfaRequired(true);
        return;
      }
      // The session cache is refreshed before navigating, so the destination
      // workspace renders signed-in on its first paint.
      await refresh();
      router.replace(next);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="grid min-h-screen place-items-center p-4 terminal-grid">
      <form method="post" onSubmit={submit} className="w-full max-w-md rounded-xl border border-slate-800 bg-terminal-900 p-6 shadow-2xl">
        <div className="mb-6">
          <div className="text-xs uppercase tracking-[.2em] text-accent">StockPilot AI</div>
          <h1 className="mt-2 text-2xl font-semibold">{mfaRequired ? "Verify your sign-in" : "Sign in to your workspace"}</h1>
          <p className="mt-2 text-sm leading-5 text-slate-500">{mfaRequired ? "Enter the current code from your authenticator, or one unused recovery code." : "Market research is public. Saved portfolios, alerts, and paper simulations require an account."}</p>
        </div>
        {!mfaRequired && <><OAuthButtons nextPath={next} /><div className="my-5 flex items-center gap-3 text-[10px] uppercase tracking-wider text-slate-600"><span className="h-px flex-1 bg-slate-800"/>or use password<span className="h-px flex-1 bg-slate-800"/></div></>}
        <div className="space-y-3">
          {mfaRequired ? <>
            <label htmlFor="login-code" className="block text-xs text-slate-300">Authenticator or recovery code</label>
            <Input id="login-code" name="code" inputMode="text" autoComplete="one-time-code" placeholder="123456 or XXXX-XXXX" aria-describedby={error ? "login-error" : undefined} autoFocus required />
          </> : <>
            <label htmlFor="login-email" className="block text-xs text-slate-300">Email address</label>
            <Input id="login-email" name="email" type="email" placeholder="email@example.com" autoComplete="email" aria-describedby={error ? "login-error" : undefined} required />
            <label htmlFor="login-password" className="block text-xs text-slate-300">Password</label>
            <div className="relative"><Input id="login-password" name="password" type={showPassword ? "text" : "password"} placeholder="Your password" autoComplete="current-password" aria-describedby={error ? "login-error" : undefined} required /><button type="button" onClick={() => setShowPassword((value) => !value)} aria-label={showPassword ? "Hide password" : "Show password"} className="absolute inset-y-0 right-0 grid w-11 place-items-center text-slate-500 hover:text-slate-200">{showPassword ? <EyeOff aria-hidden="true" size={16}/> : <Eye aria-hidden="true" size={16}/>}</button></div>
          </>}
          {(oauthError || error) && <p id="login-error" role="alert" className="rounded border border-loss/30 bg-loss/5 p-3 text-xs leading-5 text-loss">{error || oauthMessage[oauthError || ""] || "External sign-in could not be completed."}{supportId ? ` Support ID: ${supportId}.` : ""}</p>}
          <Button className="w-full" type="submit" disabled={!ready || pending}>{pending ? "Verifying…" : mfaRequired ? "Verify and sign in" : "Sign in"}</Button>
        </div>
        {!mfaRequired && <p className="mt-4 text-xs text-slate-500">New account? <Link className="text-accent" href={`/register?next=${encodeURIComponent(next)}`}>Register</Link></p>}
      </form>
    </main>
  );
}

export default function Login() {
  return (
    <Suspense>
      <LoginContent />
    </Suspense>
  );
}
