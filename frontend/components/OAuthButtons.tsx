"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { safeNextPath } from "@/lib/auth";

type OAuthStatus = {
  google: { configured: boolean };
};

function GoogleMark() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="size-4">
      <path fill="#4285F4" d="M21.6 12.23c0-.71-.06-1.4-.18-2.06H12v3.9h5.38a4.6 4.6 0 0 1-2 3.02v2.53h3.24c1.9-1.75 2.98-4.33 2.98-7.39Z" />
      <path fill="#34A853" d="M12 22c2.7 0 4.98-.9 6.63-2.38l-3.24-2.53c-.9.6-2.05.96-3.39.96-2.61 0-4.83-1.76-5.62-4.13H3.03v2.61A10 10 0 0 0 12 22Z" />
      <path fill="#FBBC05" d="M6.38 13.92A6.01 6.01 0 0 1 6.06 12c0-.67.12-1.32.32-1.92V7.47H3.03A10 10 0 0 0 2 12c0 1.61.39 3.14 1.03 4.53l3.35-2.61Z" />
      <path fill="#EA4335" d="M12 5.95c1.47 0 2.79.51 3.83 1.5l2.87-2.88A9.63 9.63 0 0 0 12 2a10 10 0 0 0-8.97 5.47l3.35 2.61C7.17 7.71 9.39 5.95 12 5.95Z" />
    </svg>
  );
}

export function OAuthButtons({ nextPath }: { nextPath?: string }) {
  const next = safeNextPath(nextPath);
  const status = useQuery({
    queryKey: ["oauth-status"],
    queryFn: () => api<OAuthStatus>("/api/v1/auth/oauth/status"),
    staleTime: 60_000,
    retry: false,
  });
  const googleReady = Boolean(status.data?.google?.configured);

  return (
    <div className="space-y-2">
      <a
        href={googleReady ? `/api/v1/auth/google/start?next=${encodeURIComponent(next)}` : undefined}
        aria-disabled={!googleReady}
        className={`flex min-h-11 w-full items-center justify-center gap-2 rounded-lg border px-4 text-sm font-medium ${googleReady ? "border-slate-300 bg-white text-slate-900 hover:bg-slate-100" : "cursor-not-allowed border-slate-800 bg-slate-900 text-slate-600"}`}
      >
        <GoogleMark /> Continue with Google{status.isSuccess && !googleReady ? " · not configured" : ""}
      </a>
      {status.isError && <p className="text-center text-[11px] text-warning">External sign-in status is unavailable. Password sign-in still works.</p>}
    </div>
  );
}
