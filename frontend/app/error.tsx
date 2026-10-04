"use client";

import Link from "next/link";
import { useEffect } from "react";

export default function RouteError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => { console.error(error); }, [error]);
  return <main className="grid min-h-screen place-items-center bg-terminal-950 p-5 terminal-grid"><section role="alert" className="max-w-lg rounded-2xl border border-loss/25 bg-terminal-900 p-7 text-center shadow-panel"><div className="text-xs font-semibold uppercase tracking-[.2em] text-loss">Workspace isolated</div><h1 className="display-font mt-3 text-2xl font-semibold text-white">This view could not be rendered.</h1><p className="mt-3 text-sm leading-6 text-slate-400">Your other research views and saved data remain available. Retry this route or inspect provider status.</p>{error.digest && <p className="mt-2 font-mono text-[10px] text-slate-600">Reference {error.digest}</p>}<div className="mt-5 flex justify-center gap-2"><button onClick={reset} className="min-h-11 rounded-lg bg-accent px-4 text-sm font-semibold text-terminal-950">Retry view</button><Link href="/system" className="inline-flex min-h-11 items-center rounded-lg border border-slate-700 px-4 text-sm text-slate-300">System status</Link></div></section></main>;
}
