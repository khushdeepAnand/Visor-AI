"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Activity, Bell, Bot, BriefcaseBusiness, ChartNoAxesCombined, CircleUserRound, Compass, Filter, FlaskConical, Gauge, Home, Layers3, LogOut, Menu, PieChart, ShieldAlert, ShieldCheck, Sunrise, Swords, Workflow, Wrench, X } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { SymbolSearch } from "./SymbolSearch";
import { TopTicker } from "./TopTicker";
import { DataSourceBadge } from "./DataSourceBadge";
import { ThemeToggle } from "./ThemeToggle";

const primary = [
  ["/", "Home", Home],
  ["/markets/NIFTY%2050", "Explore", Compass],
  ["/paper-trading", "Paper Trade", Swords],
  ["/portfolio", "Portfolio", BriefcaseBusiness],
] as const;

const secondary = [
  ["/brief", "Morning Brief", Sunrise, "Session snapshot for covered symbols"],
  ["/screener", "Screener", Filter, "Filter the universe on stored history"],
  ["/research", "Research", Bot, "Grounded-only question answering over one symbol"],
  ["/sectors", "Sectors", PieChart, "Sector rotation on available coverage"],
  ["/compare", "Compare", Layers3, "Compare symbols and ranges"],
  ["/derivatives", "Derivatives", Activity, "Options and futures scenarios"],
  ["/watchlist", "Watchlist", Layers3, "Saved market instruments"],
  ["/alerts", "Alerts", Bell, "Price and research alerts"],
  ["/risk", "Risk Lab", ShieldAlert, "Portfolio risk scenarios"],
  ["/strategies", "Strategies", Workflow, "No-code rule builder and paper backtests"],
  ["/forward-tests", "Forward Tests", FlaskConical, "Track saved strategies forward on history"],
  ["/track-record", "Track Record", ChartNoAxesCombined, "Automatically settled forecast evidence"],
  ["/system", "System", Gauge, "Provider and service health"],
  ["/account", "Account", CircleUserRound, "Profile and preferences"],
] as const;

export function TerminalShell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const { status, user, isAdmin, signOut } = useAuth();
  const [moreOpen, setMoreOpen] = useState(false);
  const desktopMoreButton = useRef<HTMLButtonElement>(null);
  const mobileMoreButton = useRef<HTMLElement>(null);
  const mobileMoreDetails = useRef<HTMLDetailsElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const dialog = useRef<HTMLElement>(null);
  const isActive = (href: string) => href === "/" ? path === href : path.startsWith(href.split("%20")[0]);
  // Admins get the Setup Doctor alongside the main admin console: it is the
  // first place to look when sign-in or market data fails, so it must be
  // reachable from navigation rather than by typing a URL.
  const moreLinks = isAdmin
    ? [
        ...secondary,
        ["/admin", "Admin", ShieldCheck, "Models, quality and operations"] as const,
        ["/admin/setup", "Setup Doctor", Wrench, "Credential and provider configuration checks"] as const,
        ["/admin/security", "Security Center", ShieldAlert, "Live security posture, from real probes"] as const,
      ]
    : secondary;
  const moreActive = moreLinks.some(([href]) => isActive(href));
  const focusMoreTrigger = () => (window.matchMedia?.("(min-width: 1024px)").matches ? desktopMoreButton : mobileMoreButton).current?.focus();

  useEffect(() => {
    if (status === "authenticated" && user?.research_acknowledgment_required && path !== "/onboarding") {
      router.replace(`/onboarding?next=${encodeURIComponent(path)}`);
    }
  }, [path, router, status, user?.research_acknowledgment_required]);

  useEffect(() => {
    if (!moreOpen) return;
    closeButton.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMoreOpen(false);
        focusMoreTrigger();
      }
      if (event.key === "Tab" && dialog.current) {
        const controls = [...dialog.current.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), input, select, [tabindex]:not([tabindex="-1"])')];
        if (!controls.length) return;
        const first = controls[0];
        const last = controls.at(-1)!;
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [moreOpen]);

  async function logout() {
    try {
      await signOut();
    } finally {
      router.replace("/");
      router.refresh();
    }
  }

  const primaryItems = primary.map(([href, label, Icon]) => (
    <Link key={href} href={href} className={`interactive-surface flex min-h-11 items-center gap-2.5 rounded-lg border px-3 py-2 text-sm ${isActive(href) ? "border-accent/25 bg-accent/10 text-accent" : "border-transparent text-slate-400 hover:bg-slate-800/50 hover:text-slate-100"}`}>
      <Icon aria-hidden="true" size={17} /><span>{label}</span>
    </Link>
  ));

  return (
    <div className="min-h-[calc(100vh-var(--market-banner-height,0px))] bg-terminal-950 terminal-grid">
      <header className="stockpilot-header-glow fixed inset-x-0 top-[var(--market-banner-height,0px)] z-40 flex h-16 items-center gap-2 border-b border-slate-800/90 bg-terminal-950/90 px-3 backdrop-blur-xl md:gap-4 md:px-4">
        <Link href="/" aria-label="StockPilot AI home" className="display-font flex min-h-11 shrink-0 items-center gap-2 font-semibold tracking-tight">
          <span className="grid size-9 place-items-center rounded-lg border border-accent/25 bg-gradient-to-br from-accent/20 to-secondary/15 text-accent shadow-[0_0_24px_rgba(52,211,153,.08)]">SP</span>
          <span className="hidden sm:inline">StockPilot <b className="text-accent">AI</b></span>
          <small className="hidden rounded bg-slate-800 px-1.5 py-.5 text-[9px] text-slate-400 md:inline">v7 RC</small>
        </Link>
        <TopTicker />
        <div className="ml-auto flex min-w-0 items-center gap-2">
          <div className="hidden xl:block"><DataSourceBadge /></div>
          <SymbolSearch />
          <ThemeToggle />
          {status === "authenticated" ? (
            <Link href="/account" aria-label="Open account" className="interactive-surface hidden min-h-11 items-center gap-2 rounded-md border border-slate-800 px-3 text-xs text-slate-300 sm:flex">
              <CircleUserRound aria-hidden="true" size={15} /><span className="max-w-28 truncate">{user?.name || "Account"}</span>
            </Link>
          ) : status === "unauthenticated" ? (
            <Link href="/login" className="flex min-h-11 items-center rounded-md border border-accent/35 bg-accent/10 px-3 text-xs text-accent">Sign in</Link>
          ) : null}
          {status === "authenticated" && <button type="button" onClick={logout} aria-label="Log out" className="interactive-surface grid size-11 shrink-0 place-items-center rounded-md border border-slate-800 text-slate-500 hover:text-slate-100"><LogOut aria-hidden="true" size={16}/></button>}
        </div>
      </header>

      <aside className="fixed bottom-0 left-0 top-[calc(4rem+var(--market-banner-height,0px))] z-30 hidden w-48 border-r border-slate-800/80 bg-terminal-950/88 p-2 backdrop-blur lg:block">
        <nav className="space-y-1" aria-label="Primary navigation">
          {primaryItems}
          <button ref={desktopMoreButton} type="button" aria-haspopup="dialog" aria-controls="more-navigation-dialog" aria-expanded={moreOpen} onPointerDown={() => setMoreOpen(true)} onClick={() => setMoreOpen(true)} className={`interactive-surface flex min-h-11 w-full items-center gap-2.5 rounded-lg border px-3 py-2 text-sm ${moreActive ? "border-accent/25 bg-accent/10 text-accent" : "border-transparent text-slate-400 hover:bg-slate-800/50 hover:text-slate-100"}`}><Menu aria-hidden="true" size={17}/>More</button>
        </nav>
        <div className="absolute bottom-4 left-3 right-3 rounded-lg border border-slate-800 bg-slate-950/60 p-3 text-[10px] leading-4 text-slate-500">Not investment advice.<br/>Research and education only.</div>
      </aside>

      <main className="pb-24 pt-16 lg:pb-0 lg:pl-48"><div key={path} className="reveal-card mx-auto max-w-[1800px] p-3 md:p-4 xl:p-5">{children}<p role="note" className="mt-5 border-t border-slate-800 pt-3 text-[10px] leading-4 text-slate-600">Not investment advice. For research and education only. Forecasts, screeners, and alerts are uncertain analytical outputs, not recommendations. No broker order execution.</p></div></main>

      <nav className="fixed inset-x-0 bottom-0 z-50 grid grid-cols-5 border-t border-slate-800/90 bg-terminal-950/95 px-1 pb-[max(.25rem,env(safe-area-inset-bottom))] pt-1 backdrop-blur-xl lg:hidden" aria-label="Primary navigation">
        {primary.map(([href, label, Icon]) => <Link key={href} href={href} className={`flex min-h-14 min-w-0 flex-col items-center justify-center gap-1 rounded-md px-0.5 text-[10px] ${isActive(href) ? "text-accent" : "text-slate-500"}`}><Icon aria-hidden="true" size={18}/><span className="max-w-full truncate">{label}</span></Link>)}
        <details ref={mobileMoreDetails} open={moreOpen} className="relative min-w-0 lg:hidden">
          <summary ref={mobileMoreButton} role="button" aria-haspopup="dialog" aria-controls="more-navigation-dialog" aria-expanded={moreOpen} onClick={(event) => { event.preventDefault(); setMoreOpen((open) => !open); }} className={`flex min-h-14 list-none flex-col items-center justify-center gap-1 rounded-md text-[10px] ${moreActive ? "text-accent" : "text-slate-500"}`}><Menu aria-hidden="true" size={18}/><span>More</span></summary>
          <section id="more-navigation-dialog" role="dialog" aria-modal="true" aria-labelledby="more-title-mobile" className="fixed inset-x-3 bottom-[calc(4.5rem+env(safe-area-inset-bottom))] z-[70] max-h-[72vh] overflow-auto rounded-2xl border border-slate-700 bg-terminal-900 p-4 shadow-2xl">
            <div className="mb-3 flex items-center justify-between"><div><h2 id="more-title-mobile" className="font-semibold text-white">More destinations</h2><p className="mt-1 text-xs text-slate-500">Research, monitoring and account tools</p></div><button type="button" aria-label="Close navigation" onClick={() => { setMoreOpen(false); mobileMoreButton.current?.focus(); }} className="grid size-11 place-items-center rounded-lg border border-slate-800 text-slate-400"><X aria-hidden="true" size={18}/></button></div>
            <nav className="grid gap-2" aria-label="More navigation">{moreLinks.map(([href, label, Icon, description]) => <Link key={href} href={href} onClick={() => setMoreOpen(false)} className={`flex min-h-16 items-center gap-3 rounded-xl border px-3 py-2 text-sm ${isActive(href) ? "border-accent/30 bg-accent/10 text-accent" : "border-slate-800 text-slate-300 hover:border-slate-700"}`}><Icon aria-hidden="true" size={18}/><span><b className="block font-medium">{label}</b><small className="mt-0.5 block text-[10px] text-slate-500">{description}</small></span></Link>)}</nav>
          </section>
        </details>
      </nav>

      {moreOpen && (
        <div className="fixed inset-0 z-[70] hidden bg-black/75 p-3 backdrop-blur-sm lg:block" role="presentation" onMouseDown={(event) => { if (event.currentTarget === event.target) { setMoreOpen(false); focusMoreTrigger(); } }}>
          <section id="more-navigation-dialog" ref={dialog} role="dialog" aria-modal="true" aria-labelledby="more-title" className="absolute inset-x-3 bottom-[calc(.75rem+env(safe-area-inset-bottom))] max-h-[82vh] overflow-auto rounded-2xl border border-slate-700 bg-terminal-900 p-4 shadow-2xl lg:left-[calc(50%+6rem)] lg:right-auto lg:top-1/2 lg:bottom-auto lg:w-[min(680px,calc(100vw-16rem))] lg:-translate-x-1/2 lg:-translate-y-1/2 lg:p-5">
            <div className="mb-3 flex items-center justify-between"><div><h2 id="more-title" className="font-semibold text-white">More destinations</h2><p className="mt-1 text-xs text-slate-500">Research, monitoring and account tools</p></div><button ref={closeButton} type="button" aria-label="Close navigation" onClick={() => { setMoreOpen(false); focusMoreTrigger(); }} className="grid size-11 place-items-center rounded-lg border border-slate-800 text-slate-400"><X aria-hidden="true" size={18}/></button></div>
            <nav className="grid gap-2 sm:grid-cols-2" aria-label="More navigation">
              {moreLinks.map(([href, label, Icon, description]) => <Link key={href} href={href} onClick={() => setMoreOpen(false)} className={`flex min-h-16 items-center gap-3 rounded-xl border px-3 py-2 text-sm ${isActive(href) ? "border-accent/30 bg-accent/10 text-accent" : "border-slate-800 text-slate-300 hover:border-slate-700"}`}><Icon aria-hidden="true" size={18}/><span><b className="block font-medium">{label}</b><small className="mt-0.5 block text-[10px] text-slate-500">{description}</small></span></Link>)}
            </nav>
          </section>
        </div>
      )}
    </div>
  );
}
