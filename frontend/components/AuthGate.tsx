"use client";

/**
 * Gate for workspaces that hold user-owned data.
 *
 * Three states are distinguished on purpose: a neutral loading state while the
 * session is still being checked, a sign-in prompt only for a definitive 401,
 * and a retry panel for a session check that failed for any other reason.
 * Children are mounted only once the session is confirmed, so protected
 * requests never fire while signed out.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { loginHref, useAuth } from "@/lib/auth";

export function AuthGate({ children }: { children: React.ReactNode }) {
  const { status, refresh } = useAuth();
  const pathname = usePathname();

  if (status === "loading") {
    return (
      <div className="grid min-h-[50vh] place-items-center" role="status" aria-live="polite">
        <div className="w-full max-w-md space-y-3" aria-label="Checking session">
          <div className="h-5 w-40 animate-pulse rounded bg-slate-800/70" />
          <div className="h-24 animate-pulse rounded-lg bg-slate-800/50" />
          <div className="h-24 animate-pulse rounded-lg bg-slate-800/40" />
          <p className="text-center text-xs text-slate-500">Checking session…</p>
        </div>
      </div>
    );
  }

  if (status === "error") {
    return (
      <div className="grid min-h-[50vh] place-items-center">
        <div className="rounded-lg border border-slate-800 bg-terminal-900 p-6 text-center">
          <h2 className="font-semibold">Session check unavailable</h2>
          <p className="mt-2 max-w-sm text-sm text-slate-500">
            The terminal could not confirm your session. This is not a sign-out; the API may be starting up.
          </p>
          <button
            type="button"
            onClick={() => void refresh()}
            className="mt-4 rounded border border-accent/40 bg-accent/10 px-4 py-2 text-sm text-accent"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (status === "unauthenticated") {
    return (
      <div className="grid min-h-[50vh] place-items-center">
        <div className="rounded-lg border border-slate-800 bg-terminal-900 p-6 text-center">
          <h2 className="font-semibold">Sign in required</h2>
          <p className="mt-2 text-sm text-slate-500">This workspace stores user-owned data.</p>
          <Link
            className="mt-4 inline-block rounded border border-accent/40 bg-accent/10 px-4 py-2 text-sm text-accent"
            href={loginHref(pathname)}
          >
            Open login
          </Link>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
