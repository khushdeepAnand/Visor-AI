"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { ChartNoAxesCombined } from "lucide-react";
import { TerminalShell } from "@/components/TerminalShell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { api, fetchModelQuality, formatApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { inr } from "@/lib/utils";
import { NextDayScorecard, type NextDayEvidence } from "@/components/PublicScorecard";

type HistoryItem = { id: number; symbol: string; forecast_low?: number; forecast_median?: number; forecast_high?: number; confidence_level?: number; timeframe?: string; model_version?: string; forecast_status?: string; outcome_status?: string; actual_price?: number; coverage_hit?: number; origin_timestamp?: string; target_timestamp?: string; horizon?: string; horizon_sessions?: number };
type History = { items: HistoryItem[] };
type Calibration = { overall: { coverage_numerator?: number; calibration_denominator?: number; empirical_coverage?: number; average_width?: number; status?: string }; track_record?: Record<string, number>; by_window_timeframe?: Array<Record<string, unknown>>; by_horizon?: Array<{ horizon: string; coverage_numerator?: number; calibration_denominator?: number; empirical_coverage?: number; status?: string }> };

export default function TrackRecordPage() {
  const router = useRouter();
  const { status, isAdmin } = useAuth();
  const [timeframe, setTimeframe] = useState("all");
  const [horizon, setHorizon] = useState("all");
  useEffect(() => { if (status === "unauthenticated") router.replace("/login?next=/track-record"); }, [status, router]);
  const history = useQuery({ queryKey: ["prediction-history"], queryFn: () => api<History>("/api/v1/predictions/history?limit=250"), enabled: status === "authenticated" });
  const calibration = useQuery({ queryKey: ["prediction-calibration"], queryFn: () => api<Calibration>("/api/v1/predictions/calibration?limit=1000"), enabled: status === "authenticated" });
  const scorecard = useQuery({ queryKey: ["public-next-day-scorecard"], queryFn: () => api<{ next_day: NextDayEvidence }>("/api/v1/scorecard"), enabled: status === "authenticated" });
  const modelQuality = useQuery({ queryKey: ["model-quality"], queryFn: () => fetchModelQuality(), enabled: status === "authenticated" && isAdmin, retry: false });
  const items = (history.data?.items || [])
    .filter((item) => timeframe === "all" || item.timeframe === timeframe)
    .filter((item) => horizon === "all" || String(item.horizon_sessions ?? item.horizon ?? "1") === horizon);
  const overall = calibration.data?.overall;
  const numerator = overall?.coverage_numerator || 0;
  const denominator = overall?.calibration_denominator || 0;

  return <TerminalShell>
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3"><div><div className="flex items-center gap-2 text-accent"><ChartNoAxesCombined aria-hidden="true" size={17} /><span className="text-xs uppercase tracking-[.2em]">Evidence ledger</span></div><h1 className="mt-1 text-2xl font-semibold">Track Record</h1><p className="mt-1 text-sm text-slate-500">Only authoritative automatic outcomes count toward calibration. Demo, manual, stale, and unverifiable rows are excluded.</p></div><div className="flex flex-wrap gap-3"><label className="text-xs text-slate-500">Timeframe<Select className="ml-2 w-32" value={timeframe} onChange={(event) => setTimeframe(event.target.value)}><option value="all">All</option><option value="1D">1 day</option><option value="1h">1 hour</option><option value="15m">15 min</option><option value="5m">5 min</option><option value="1m">1 min</option></Select></label><label className="text-xs text-slate-500">Horizon<Select className="ml-2 w-32" value={horizon} onChange={(event) => setHorizon(event.target.value)}><option value="all">All</option><option value="1">1 session</option><option value="3">3 sessions</option><option value="5">5 sessions</option><option value="10">10 sessions</option></Select></label></div></div>
    {(history.error || calibration.error) && <div role="alert" className="mb-4 rounded border border-loss/30 bg-loss/5 p-3 text-xs text-loss">{formatApiError(history.error || calibration.error)}</div>}
    <div className="mb-3">{scorecard.data?.next_day && <NextDayScorecard evidence={scorecard.data.next_day} />}{scorecard.isPending && <p role="status" className="text-xs text-slate-500">Loading next-day evidence…</p>}{scorecard.error && <p role="alert" className="text-xs text-loss">Next-day evidence unavailable: {formatApiError(scorecard.error)}</p>}</div>
    <div className="grid gap-3 md:grid-cols-3"><Card className="md:col-span-2"><CardHeader><CardTitle>Comparable settled ranges</CardTitle></CardHeader><CardContent><div className="text-2xl font-semibold text-white">Price landed inside {numerator} of {denominator} comparable ranges.</div><p className="mt-2 text-xs leading-5 text-slate-500">{denominator ? `Observed coverage: ${((overall?.empirical_coverage || 0) * 100).toFixed(1)}%. This describes historical interval coverage, not profit probability.` : "Insufficient automatically settled evidence. No accuracy percentage is shown."}</p></CardContent></Card><Card><CardHeader><CardTitle>Official denominator</CardTitle></CardHeader><CardContent><div className="tabular text-2xl text-accent">{denominator}</div><p className="mt-2 text-xs text-slate-500">Filtered official outcomes only</p></CardContent></Card></div>
    {(calibration.data?.by_horizon || []).length > 0 && <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">{(calibration.data?.by_horizon || []).map((group) => <Card key={group.horizon}><CardContent><div className="text-xs uppercase tracking-wide text-slate-500">{group.horizon} session horizon</div><div className="tabular mt-1 text-xl font-semibold text-white">{group.coverage_numerator ?? 0} of {group.calibration_denominator ?? 0}</div><p className="mt-1 text-[11px] leading-4 text-slate-500">{group.calibration_denominator ? `Observed ${((group.empirical_coverage || 0) * 100).toFixed(1)}% coverage for this horizon.` : "No settled evidence for this horizon yet."}</p></CardContent></Card>)}</div>}
    {isAdmin && modelQuality.data && (() => {
    const quality = modelQuality.data;
    const refreshed = Object.entries(quality.last_refreshed || {});
    return (
      <Card className="mt-3">
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2">Model health
            {quality.dashboard.drift_active_groups > 0 ? (
              <span className="rounded-full border border-warning/35 bg-warning/5 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-warning">{quality.dashboard.drift_active_groups} drift-active group{quality.dashboard.drift_active_groups === 1 ? "" : "s"}</span>
            ) : (
              <span className="rounded-full border border-gain/35 bg-gain/5 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-gain">No active drift</span>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-[11px] leading-4 text-slate-500">{quality.disclaimer} Last evaluated {new Date(quality.dashboard.last_evaluated).toLocaleString()} · {quality.dashboard.group_count} groups across all users' authoritative settlements.</p>
          {quality.dashboard.groups.length > 0 && (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full min-w-[760px] text-left text-xs">
                <thead className="uppercase tracking-wide text-slate-600"><tr><th className="py-2">Symbol</th><th>Frame</th><th>Horizon</th><th>Settled</th><th>Coverage</th><th>Nominal</th><th>MASE</th><th>State</th><th>Drift reasons</th></tr></thead>
                <tbody>{quality.dashboard.groups.map((group) => <tr key={`${group.symbol}-${group.timeframe}-${group.horizon}`} className="border-t border-slate-800"><td className="py-2 font-semibold text-white">{group.symbol}</td><td className="tabular text-slate-400">{group.timeframe}</td><td className="tabular text-slate-400">{group.horizon}</td><td className="tabular text-slate-300">{group.settled_samples}</td><td className="tabular">{group.rolling_coverage != null ? `${(group.rolling_coverage * 100).toFixed(1)}%` : "–"}</td><td className="tabular text-slate-500">{group.nominal_coverage != null ? `${(group.nominal_coverage * 100).toFixed(1)}%` : "–"}</td><td className="tabular">{group.mase != null ? group.mase.toFixed(2) : "–"}</td><td><span className={group.state === "drift" ? "text-warning" : group.state === "stable" ? "text-gain" : "text-slate-500"}>{group.state}</span></td><td className="max-w-[220px] text-[10px] leading-4 text-slate-500">{group.drift_reasons?.length ? group.drift_reasons.join(" · ") : "–"}</td></tr>)}</tbody>
              </table>
            </div>
          )}
          {refreshed.length > 0 && (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full min-w-[480px] text-left text-xs">
                <thead className="uppercase tracking-wide text-slate-600"><tr><th className="py-2">Symbol</th><th>Last model refresh</th><th>Trigger</th></tr></thead>
                <tbody>{refreshed.map(([symbol, info]) => <tr key={symbol} className="border-t border-slate-800"><td className="py-2 font-semibold text-white">{symbol}</td><td className="tabular text-slate-300">{new Date(info.last_refreshed).toLocaleString()}</td><td className={info.trigger === "drift_auto_retrain" ? "text-warning" : "text-slate-400"}>{info.trigger}</td></tr>)}</tbody>
              </table>
            </div>
          )}
          {!quality.dashboard.groups.length && !refreshed.length && <p className="mt-3 text-[11px] text-slate-500">No settled evidence or refresh artifacts yet.</p>}
        </CardContent>
      </Card>
    );
  })()}
    <Card className="mt-3"><CardHeader><CardTitle>Research trail</CardTitle></CardHeader><CardContent><div className="overflow-x-auto"><table className="w-full min-w-[820px] text-left text-xs"><thead className="uppercase tracking-wide text-slate-600"><tr><th className="py-3">Symbol</th><th>Horizon</th><th>Status</th><th>Range</th><th>Actual</th><th>Outcome</th><th>Target time</th></tr></thead><tbody>{items.length === 0 && <tr><td colSpan={7} className="border-t border-slate-800 py-10 text-center text-slate-500">No saved research for this filter.</td></tr>}{items.map((item) => <tr key={item.id} className="border-t border-slate-800"><td className="py-3 font-semibold text-white">{item.symbol}</td><td className="tabular text-slate-400">{(() => { const h = item.horizon_sessions ?? (item.horizon ? parseInt(item.horizon, 10) : 1); return `${h} session${h === 1 ? "" : "s"}`; })()}</td><td>{item.forecast_status || "not reported"}</td><td className="tabular">{item.forecast_low != null && item.forecast_high != null ? `${inr(item.forecast_low)} to ${inr(item.forecast_high)}` : "No corridor"}</td><td className="tabular">{item.actual_price != null ? inr(item.actual_price) : "Pending"}</td><td>{item.outcome_status === "settled" ? item.coverage_hit ? "Inside range" : "Outside range" : item.outcome_status || "pending"}</td><td>{item.target_timestamp ? new Date(item.target_timestamp).toLocaleString() : "Not reported"}</td></tr>)}</tbody></table></div></CardContent></Card>
  </TerminalShell>;
}
