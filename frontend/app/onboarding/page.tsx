"use client";

import { Suspense, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { BarChart3, Database, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { safeNextPath, useAuth } from "@/lib/auth";

const steps = [
  { title: "Research, not execution", icon: ShieldCheck, body: "StockPilot explains market data and supports simulated decisions. It cannot guarantee accuracy or place live broker orders." },
  { title: "Read the corridor", icon: BarChart3, body: "Low, median, and high describe a checked range for one target time. The median is a reference, not a price target." },
  { title: "Know the data mode", icon: Database, body: "Live broker, fallback, stale, and demo data are labelled separately. Demo outcomes never count toward official calibration." },
] as const;

function OnboardingContent() {
  const router = useRouter();
  const params = useSearchParams();
  const { status, refresh } = useAuth();
  const [step, setStep] = useState(0);
  const [coverageAcknowledged, setCoverageAcknowledged] = useState(false);
  const [paperAcknowledged, setPaperAcknowledged] = useState(false);
  const [interests, setInterests] = useState<string[]>(["Banking", "Technology", "Indices"]);
  const [workspaceMode, setWorkspaceMode] = useState<"calm" | "pro">("calm");
  const [submissionError, setSubmissionError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const system = useQuery({ queryKey: ["system-onboarding"], queryFn: () => api<{ research_acknowledgment: { version: string; text: string } }>("/api/v1/system"), enabled: step === 2 });
  useEffect(() => { if (status === "unauthenticated") router.replace("/login?next=/onboarding"); }, [status, router]);
  const current = steps[step];
  const Icon = current.icon;
  const complete = step < 2 || (coverageAcknowledged && paperAcknowledged && Boolean(system.data?.research_acknowledgment?.version));

  async function finish() {
    const version = system.data?.research_acknowledgment?.version;
    if (!version) return;
    setSubmissionError("");
    setSubmitting(true);
    try {
      await api("/api/v1/auth/research-acknowledgment", { method: "POST", body: JSON.stringify({ version, accepted: true }) });
      await refresh();
      localStorage.setItem("stockpilot-onboarding", JSON.stringify({ completed_at: new Date().toISOString(), acknowledgment_version: version, interests, workspaceMode }));
      localStorage.setItem("stockpilot-market-view", workspaceMode);
      router.replace(safeNextPath(params.get("next")));
    } catch (error) {
      setSubmissionError((error as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return <main className="grid min-h-screen place-items-center bg-terminal-950 p-4 terminal-grid">
    <section className="w-full max-w-2xl rounded-2xl border border-slate-800 bg-terminal-900 p-5 shadow-2xl sm:p-8" aria-labelledby="onboarding-title">
      <div className="mb-7 flex gap-2" aria-label={`Step ${step + 1} of 3`}>{steps.map((item, index) => <span key={item.title} className={`h-1.5 flex-1 rounded-full ${index <= step ? "bg-accent" : "bg-slate-800"}`} />)}</div>
      <div className="grid size-12 place-items-center rounded-xl border border-accent/25 bg-accent/10 text-accent"><Icon aria-hidden="true" size={22} /></div>
      <div className="mt-5 text-xs uppercase tracking-[.2em] text-accent">Step {step + 1} of 3</div>
      <h1 id="onboarding-title" className="mt-2 text-2xl font-semibold text-white">{current.title}</h1>
      <p className="mt-3 max-w-xl text-sm leading-6 text-slate-400">{current.body}</p>

      {step === 1 && <div className="mt-5 rounded-xl border border-slate-800 bg-slate-950/40 p-4"><div className="flex items-center justify-between text-xs text-slate-500"><span>Lower scenario</span><span>Median reference</span><span>Upper scenario</span></div><div className="relative mt-3 h-3 rounded-full bg-gradient-to-r from-secondary/50 via-accent to-info/50"><span className="absolute left-1/2 top-1/2 size-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-terminal-950 bg-white" /></div><p className="mt-3 text-xs text-slate-500">Wider corridors communicate more uncertainty. StockPilot does not narrow them to look attractive.</p></div>}
      {step === 2 && <div className="mt-5 space-y-4">
        <fieldset><legend className="text-xs font-semibold uppercase tracking-wide text-slate-400">Choose 3–5 interests</legend><div className="mt-2 flex flex-wrap gap-2">{["Banking", "Technology", "Energy", "Indices", "Healthcare", "Consumer", "Infrastructure", "Automotive"].map((interest) => <button key={interest} type="button" aria-pressed={interests.includes(interest)} onClick={() => setInterests((items) => items.includes(interest) ? items.length > 3 ? items.filter((item) => item !== interest) : items : items.length < 5 ? [...items, interest] : items)} className={`min-h-11 rounded-full border px-4 text-xs ${interests.includes(interest) ? "border-accent/40 bg-accent/10 text-accent" : "border-slate-700 text-slate-400"}`}>{interest}</button>)}</div></fieldset>
        <fieldset><legend className="text-xs font-semibold uppercase tracking-wide text-slate-400">Default workspace</legend><div className="mt-2 grid grid-cols-2 gap-2">{(["calm", "pro"] as const).map((mode) => <button key={mode} type="button" aria-pressed={workspaceMode === mode} onClick={() => setWorkspaceMode(mode)} className={`min-h-12 rounded-lg border px-3 text-left text-xs ${workspaceMode === mode ? "border-secondary/50 bg-secondary/10 text-slate-100" : "border-slate-700 text-slate-500"}`}><b className="block capitalize">{mode}</b><span>{mode === "calm" ? "Guided chart and corridor" : "Movable desktop mosaic"}</span></button>)}</div></fieldset>
        <label className="flex min-h-11 items-start gap-3 text-sm leading-5 text-slate-300"><input className="mt-1 accent-[var(--sp-lime)]" type="checkbox" checked={coverageAcknowledged} onChange={(event) => setCoverageAcknowledged(event.target.checked)} /><span>Forecast coverage is not the probability of making a profit.</span></label>
        <label className="flex min-h-11 items-start gap-3 text-sm leading-5 text-slate-300"><input className="mt-1 accent-[var(--sp-lime)]" type="checkbox" checked={paperAcknowledged} onChange={(event) => setPaperAcknowledged(event.target.checked)} /><span>Paper simulation is not live order execution.</span></label>
        <p className="rounded-lg border border-warning/25 bg-warning/5 p-3 text-xs leading-5 text-slate-400">{system.data?.research_acknowledgment?.text || "Loading the current research-only notice…"}</p>
        {submissionError && <p role="alert" className="text-xs text-loss">{submissionError}</p>}
      </div>}

      <div className="mt-7 flex items-center justify-between gap-3"><Button type="button" variant="ghost" disabled={step === 0 || submitting} onClick={() => setStep((value) => value - 1)}>Back</Button><Button type="button" disabled={!complete || submitting} onClick={() => step === 2 ? finish() : setStep((value) => value + 1)}>{step === 2 ? submitting ? "Recording acknowledgment…" : "Acknowledge and open StockPilot" : "Continue"}</Button></div>
    </section>
  </main>;
}

export default function OnboardingPage() { return <Suspense><OnboardingContent /></Suspense>; }
