"use client";

import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Info, Sparkles, TriangleAlert, TrendingDown, TrendingUp, Waves } from "lucide-react";
import Link from "next/link";
import { TerminalShell } from "@/components/TerminalShell";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { api, formatApiError } from "@/lib/api";
import { pct } from "@/lib/utils";

type SentimentPoint = {
  snapshot_at: string;
  avg_score: number | null;
  label: string;
  headline_count: number;
  scored_count: number;
  source: string;
};

type SentimentTrend = {
  symbol: string;
  window_days: number;
  snapshot_count: number;
  series: SentimentPoint[];
  is_stale: boolean;
};

function isFeatureDisabled(error: unknown): boolean {
  return typeof error === "object" && error !== null && "code" in error && (error as { code?: string }).code === "feature_disabled";
}

function scoreBadgeClass(score: number | null): string {
  if (score == null) return "text-slate-600";
  if (score >= 0.12) return "text-gain";
  if (score <= -0.12) return "text-loss";
  return "text-slate-300";
}

export default function SentimentPage() {
  const [symbol, setSymbol] = useState("RELIANCE");
  const query = useQuery({
    queryKey: ["sentiment-trend", symbol],
    queryFn: () => api<SentimentTrend>(`/api/v1/sentiment/${encodeURIComponent(symbol)}?days=30`),
    staleTime: 60_000,
  });
  const queryClient = useQueryClient();

  const capture = useMutation({
    mutationFn: () => api<SentimentTrend>(`/api/v1/sentiment/${encodeURIComponent(symbol)}/capture`, { method: "POST" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sentiment-trend"] });
    },
  });

  const series = useMemo(() => [...(query.data?.series ?? [])].reverse(), [query.data]);

  if (isFeatureDisabled(query.error)) {
    return (
      <TerminalShell>
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-3 py-4 sm:px-4">
          <header>
            <h1 className="display-font text-lg font-semibold text-slate-100">Sentiment trend</h1>
          </header>
          <Card>
            <CardContent className="space-y-2 py-4">
              <p role="status" className="text-sm text-slate-300">
                Sentiment trend is currently turned off by an operator feature flag.
              </p>
              <p className="text-xs text-slate-500">
                An administrator can enable the <code>sentiment_trend</code> flag under Admin · Operations.
              </p>
              {query.isError && <p className="text-xs text-loss">{formatApiError(query.error)}</p>}
            </CardContent>
          </Card>
        </div>
      </TerminalShell>
    );
  }

  const data = query.data;

  return (
    <TerminalShell>
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-3 py-4 sm:px-4">
        <header className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h1 className="display-font text-lg font-semibold text-slate-100">Sentiment trend</h1>
            <p className="text-xs text-slate-500">
              Per-session headline sentiment snapshots scored on a [-1, +1] scale from the news catalogue.
            </p>
          </div>
          {data?.is_stale && (
            <span
              role="status"
              className="inline-flex items-center gap-1.5 rounded border border-amber-900/60 bg-amber-950/40 px-2 py-1 text-[10px] uppercase tracking-wide text-amber-300"
            >
              <TriangleAlert aria-hidden="true" size={12} /> Last snapshot is stale
            </span>
          )}
        </header>

        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            capture.mutate();
          }}
        >
          <label className="flex min-h-11 items-center gap-1 text-slate-400">
            <Waves aria-hidden="true" size={13} className="text-slate-500" />
            <span className="sr-only">Symbol</span>
            <input
              value={symbol}
              onChange={(event) => setSymbol(event.target.value.toUpperCase())}
              className="w-32 rounded-md border border-slate-700 bg-terminal-950 px-2 py-1 text-[11px] text-slate-200"
            />
          </label>
          <button
            type="submit"
            disabled={capture.isPending || !symbol.trim()}
            className="inline-flex min-h-11 items-center rounded px-3 py-1 text-[10px] uppercase tracking-wide text-slate-200"
          >
            <Sparkles aria-hidden="true" size={12} className="mr-1" /> Capture now
          </button>
          {capture.isError && (
            <p role="alert" className="text-xs text-loss">{formatApiError(capture.error)}</p>
          )}
        </form>

        {query.isError && (
          <Card>
            <CardContent className="py-4">
              <p role="alert" className="text-sm text-loss">{formatApiError(query.error)}</p>
            </CardContent>
          </Card>
        )}

        <Card>
          <CardHeader className="pb-0">
            <CardTitle className="flex items-center gap-2 text-xs uppercase tracking-wider text-slate-500">
              <TrendingUp aria-hidden="true" size={13} /> Snapshot pipeline
            </CardTitle>
          </CardHeader>
          <CardContent className="py-3 text-xs text-slate-400">
            {data?.snapshot_count == null ? (
              <>Loading pipeline status…</>
            ) : (
              <p>
                {data.snapshot_count} snapshot{data.snapshot_count === 1 ? "" : "s"} over the last {data.window_days} session
                window (headlines scored against the operator-tuned sentiment model per capture).
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-0">
            <CardTitle className="flex items-center gap-2 text-xs uppercase tracking-wider text-slate-500">
              <Waves aria-hidden="true" size={13} /> Trend series — {symbol}
            </CardTitle>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            {series.length === 0 ? (
              <div className="flex flex-col items-center gap-2 py-8 text-center">
                <TrendingDown aria-hidden="true" size={20} className="text-slate-600" />
                <p className="text-sm text-slate-300">No sentiment snapshots for {symbol} yet.</p>
                <p className="max-w-md text-xs text-slate-500">
                  Newest-to-oldest: a running average of headline scores is folded into a single per-session snapshot when
                  the capture job runs. Run the scheduled job or use <em>Capture now</em> above to prime a trend.
                </p>
              </div>
            ) : (
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-800 text-left text-[11px] uppercase tracking-wider text-slate-500">
                    <th className="px-4 py-2">Snapshot</th>
                    <th className="px-4 py-2">Score</th>
                    <th className="px-4 py-2">Side</th>
                    <th className="px-4 py-2 text-right">Headlines</th>
                    <th className="px-4 py-2 text-right">Scored</th>
                    <th className="px-4 py-2">Source</th>
                  </tr>
                </thead>
                <tbody>
                  {series.map((point) => (
                    <tr key={point.snapshot_at} className="border-b border-slate-800/70">
                      <td className="px-4 py-2 font-mono text-xs text-slate-400">
                        {new Date(point.snapshot_at).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
                      </td>
                      <td className={`px-4 py-2 text-right font-mono text-xs ${scoreBadgeClass(point.avg_score)}`}>
                        {point.avg_score == null ? "n/a" : point.avg_score.toFixed(2)}
                      </td>
                      <td className={`px-4 py-2 text-xs uppercase ${scoreBadgeClass(point.avg_score)}`}>
                        {point.avg_score == null ? "n/a" : point.label}
                      </td>
                      <td className="px-4 py-2 text-right text-slate-400">{point.headline_count}</td>
                      <td className="px-4 py-2 text-right text-slate-500">{point.scored_count}</td>
                      <td className="px-4 py-2 text-xs text-slate-500">{point.source || "n/a"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-0">
            <CardTitle className="flex items-center gap-2 text-xs uppercase tracking-wider text-slate-500">
              <Info aria-hidden="true" size={13} /> Why capture screenshots
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 py-3 text-xs leading-relaxed text-slate-500">
            <p>
              Trend rows are immutable capture snapshots: each one records the summed headline score at a point in time,
              so the series reflects what was actually scored then — not a recomputed figure.
            </p>
            <p>
              A symbol with fewer than two snapshots has no meaningful slope. Use the scheduled capture job (or <em>Capture
              now</em>) to build up a comparable daily series before relying on the direction.
            </p>
          </CardContent>
          <CardFooter>
            <Link href="/news" className="text-[11px] uppercase tracking-wide text-accent hover:text-accent/80">
              View the headline feed →
            </Link>
          </CardFooter>
        </Card>
      </div>
    </TerminalShell>
  );
}
