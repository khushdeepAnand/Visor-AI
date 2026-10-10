"use client";

/**
 * Single source of truth for the browser's view of the session.
 *
 * Every user-owned route reads this provider instead of issuing its own
 * `/auth/me` request, so login, registration, OAuth callbacks and logout all
 * update the whole application at once. The provider deliberately separates
 * "still checking" from "not signed in" and from "the session check failed",
 * because collapsing them is what used to show a signed-in user the
 * "Sign in required" panel.
 */

import { createContext, useCallback, useContext, useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "@/lib/api";

export type SessionUser = {
  id?: number;
  name: string;
  email?: string;
  role?: string;
  created_at?: string;
  auth_provider?: string;
  connected_providers?: string[];
  mfa_enabled?: boolean;
  research_acknowledgment_required?: boolean;
};

export type SessionStatus = "loading" | "authenticated" | "unauthenticated" | "error";

type AuthValue = {
  user: SessionUser | null;
  status: SessionStatus;
  /** True only for a definitive 401 from the server. */
  isSignedOut: boolean;
  isAdmin: boolean;
  error: ApiError | null;
  /** Re-read the session and let every consumer re-render. */
  refresh: () => Promise<void>;
  /** End the session server-side, then drop all cached user-owned data. */
  signOut: () => Promise<void>;
};

export const SESSION_QUERY_KEY = ["me"] as const;

/** Query keys holding data that belongs to the signed-in user. */
const USER_SCOPED_KEYS = ["portfolio", "watchlist", "market-workspace-watchlist", "alerts", "paper", "predictions", "journal", "audit", "replay", "sessions", "operations-models", "operations-users", "passkeys", "mfa-status", "compliance-operations", "queue-operations"];

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: SESSION_QUERY_KEY,
    queryFn: () => api<{ user: SessionUser | null }>("/api/v1/auth/me"),
    retry: (attempt, error) => {
      // A 401 is a final answer, not a transient failure. Retrying it would
      // delay the sign-in prompt; retrying a network or 5xx error is useful.
      if (error instanceof ApiError && error.status === 401) return false;
      return attempt < 2;
    },
    staleTime: 30_000,
    refetchOnWindowFocus: true,
  });

  const refresh = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: SESSION_QUERY_KEY });
    await queryClient.refetchQueries({ queryKey: SESSION_QUERY_KEY });
  }, [queryClient]);

  const signOut = useCallback(async () => {
    try {
      await api("/api/v1/auth/logout", { method: "POST" });
    } finally {
      // Clearing runs even when the request is rejected, so the browser never
      // keeps another account's portfolio, watchlist or orders on screen.
      await queryClient.cancelQueries();
      // Undefined is a no-op in setQueryData. Removing an observed query also
      // leaves its previous result alive until a re-fetch: both could redirect
      // the login page back to a signed-in workspace immediately after logout.
      queryClient.setQueryData(SESSION_QUERY_KEY, { user: null });
      for (const key of USER_SCOPED_KEYS) {
        queryClient.removeQueries({ queryKey: [key] });
      }
    }
  }, [queryClient]);

  const value = useMemo<AuthValue>(() => {
    const error = query.error instanceof ApiError ? query.error : null;
    const isSignedOut = error?.status === 401;
    let status: SessionStatus = "loading";
    if (query.data?.user) status = "authenticated";
    else if (isSignedOut) status = "unauthenticated";
    else if (query.error) status = "error";
    else if (!query.isPending) status = "unauthenticated";
    const user = query.data?.user ?? null;
    return {
      user,
      status,
      isSignedOut,
      isAdmin: String(user?.role || "user").toLowerCase() === "admin",
      error,
      refresh,
      signOut,
    };
  }, [query.data, query.error, query.isPending, refresh, signOut]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider.");
  return value;
}

/**
 * True once the session is confirmed. Protected queries pass this as their
 * `enabled` flag so a signed-out browser never issues a request that can only
 * return 401 — which is what used to make the paper-trading page poll a
 * protected endpoint every five seconds while signed out.
 */
export function useSessionReady(): boolean {
  return useAuth().status === "authenticated";
}

/** Build a login URL that returns the user to the page they asked for. */
export function loginHref(pathname: string | null | undefined): string {
  if (!pathname || pathname === "/login" || pathname === "/register") return "/login";
  return `/login?next=${encodeURIComponent(pathname)}`;
}

/** Only allow same-site relative paths back from `?next=`. */
export function safeNextPath(raw: string | null | undefined): string {
  if (!raw) return "/";
  if (!raw.startsWith("/") || raw.startsWith("//")) return "/";
  if (raw.startsWith("/login") || raw.startsWith("/register")) return "/";
  return raw;
}
