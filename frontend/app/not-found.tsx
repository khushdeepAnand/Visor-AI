import Link from "next/link";

export default function NotFound() {
  return <main className="grid min-h-screen place-items-center bg-terminal-950 p-5 terminal-grid"><section className="text-center"><div className="font-mono text-xs text-accent">404 / ROUTE_NOT_FOUND</div><h1 className="display-font mt-3 text-3xl font-semibold text-white">That research view does not exist.</h1><p className="mt-2 text-sm text-slate-500">Search for an NSE/BSE instrument or return to the terminal.</p><Link href="/" className="mt-5 inline-flex min-h-11 items-center rounded-lg border border-accent/40 bg-accent/10 px-4 text-sm font-semibold text-accent">Return home</Link></section></main>;
}
