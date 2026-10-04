"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowDownRight, ArrowUpRight, Newspaper, RefreshCw } from "lucide-react";
import { TerminalShell } from "@/components/TerminalShell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { api, formatApiError } from "@/lib/api";
import { inr, pct } from "@/lib/utils";

/**
 * Morning Brief (v9 Part E1).
 *
 * Render-only surface over `GET /api/v1/brief`. All aggregation, staleness
 * marking, and exclusion accounting already happen server-side in
 * `services/morning_brief.py`; this page must not recompute or reorder any of
 * it, and must show excluded symbols rather than silently dropping them.
 */

type Snapshot = {
  symbol: string;
  as_of?: string;
  sessions_available?: number;
  last_close?: number | null;
  change_pct_1d?: number | null;
  change_pct_5d?: number | null;
  change_pct_20d?: number | null;
  volume?: number | null;
  volume_vs_20d_average?: number | null;
  realized_range_pct_14d?: number | null;
  distance_from_20d_high_pct?: number | null;
  distance_from_20d_low_pct?: number | null;
  freshness?: { as_of?: string; approx_sessions_behind?: number | null; stale?: boolean };
};

type Headline = {
  symbol: string;
  title?: string | null;
  url?: string | null;
  publisher?: string | null;
  published_at?: string | null;
  sentiment?: number | null;
  is_stale?: boolean;
};

type BriefResponse = {
  generated_at: string;
  session?: Record<string, unknown> & { state?: string; label?: string; note?: string };
  coverage: {
    requested: number;
    considered: number;
    included: number;
    excluded: Array<{ symbol: string; reason: string; sessions_available?: number; sessions_required?: number }>;
    max_symbols: number;
    truncated: boolean;
  };
  movers: {
    basis: string;
    gainers: Snapshot[];
    losers: Snapshot[];
    volume_leaders: Snapshot[];
    widest_realized_range: Snapshot[];
  };
  symbols: Snapshot[];
  headlines: {
    state: string;
    items: Headline[];
    failures?: Array<{ symbol: string; reason: string }>;
    headline_count?: number;
    mean_sentiment?: number | null;
    sentiment_basis?: string;
    note?: string;
  };
  evidence: { basis: string; min_sessions_required: number; stale_symbols?: string[]; is_forecast: boolean; is_recommendation: boolean };
  disclosures: string[];
};

function MoverList({ title, rows, metric }: { title: string; rows: Snapshot[]; metric: (row: Snapshot) => string }) {
  if (!rows?.length) {
    return (
      <div>
        <h3 className="text-[10px] uppercase tracking-wider text-slate-500">{title}</h3>
        <p className="mt-1 text-xs text-slate-500">Not enough covered symbols to rank.</p>
      </div>
    );
  }
  return (
    <div>
      <h3 className="text-[10px] uppercase tracking-wider text-slate-500">{title}</h3>
      <ul className="mt-1 space-y-1">
        {rows.map((row) => (
          <li key={`${title}-${row.symbol}`} className="flex items-center justify-between gap-2 text-xs">
            <span className="truncate text-slate-200">{row.symbol}</span>
            <span className={(row.change_pct_1d ?? 0) >= 0 ? "text-gain" : "text-loss"}>{metric(row)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function BriefPage() {
  const [symbolInput, setSymbolInput] = useState("");
  const symbols = useMemo(
    () => symbolInput.split(",").map((item) => item.trim()).filter(Boolean),
    [symbolInput],
  );

  const query = useQuery({
    queryKey: ["morning-brief", symbols.join(",")],
    queryFn: () => {
      const search = symbols.length ? `?symbols=${encodeURIComponent(symbols.join(","))}` : "";
      return api<BriefResponse>(`/api/v1/brief${search}`);
    },
    staleTime: 60_000,
    retry: 1,
  });

  const data = query.data;

  return (
    <TerminalShell>
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 px-3 py-4 sm:px-4">
        <header className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="display-font text-lg font-semibold text-slate-100">Morning Brief</h1>
            <p className="text-xs text-slate-500">
              Realized session data for your watchlist. Not a forecast and not a recommendation.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <input
              aria-label="Override symbols, comma separated"
              placeholder="Override symbols (optional)"
              value={symbolInput}
              onChange={(event) => setSymbolInput(event.target.value)}
              className="h-11 w-56 rounded-md border border-slate-700 bg-terminal-950 px-3 text-sm text-slate-100 outline-none placeholder:text-slate-600 focus:border-accent/70"
            />
            <Button variant="ghost" onClick={() => query.refetch()} aria-label="Refresh brief">
              <RefreshCw aria-hidden="true" size={16} />
            </Button>
          </div>
        </header>

        {query.isLoading && <p role="status" className="text-xs text-slate-400">Loading brief…</p>}
        {query.isError && (
          <Card>
            <CardContent>
              <p role="alert" className="text-xs text-loss">{formatApiError(query.error)}</p>
            </CardContent>
          </Card>
        )}

        {data && (
          <>
            <Card>
              <CardHeader>
                <CardTitle>Session</CardTitle>
                <span className="text-[10px] text-slate-500">
                  Generated {new Date(data.generated_at).toLocaleString()}
                </span>
              </CardHeader>
              <CardContent className="flex flex-wrap gap-4 text-xs text-slate-300">
                <span>State: {String(data.session?.label ?? data.session?.state ?? "unknown")}</span>
                <span>
                  Coverage: {data.coverage.included} of {data.coverage.requested} requested
                </span>
                {data.coverage.truncated && (
                  <span className="text-warning">Truncated to {data.coverage.max_symbols} symbols</span>
                )}
                {data.evidence.stale_symbols?.length ? (
                  <span className="text-warning">Stale: {data.evidence.stale_symbols.join(", ")}</span>
                ) : null}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Movers</CardTitle>
                <span className="text-[10px] text-slate-500">Realized 1-session change</span>
              </CardHeader>
              <CardContent className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <MoverList title="Gainers" rows={data.movers.gainers} metric={(row) => pct(row.change_pct_1d)} />
                <MoverList title="Losers" rows={data.movers.losers} metric={(row) => pct(row.change_pct_1d)} />
                <MoverList
                  title="Volume leaders"
                  rows={data.movers.volume_leaders}
                  metric={(row) => `${(row.volume_vs_20d_average ?? 0).toFixed(2)}x`}
                />
                <MoverList
                  title="Widest 14d range"
                  rows={data.movers.widest_realized_range}
                  metric={(row) => pct(row.realized_range_pct_14d)}
                />
              </CardContent>
              <CardContent className="border-t border-slate-800 pt-3 text-[10px] leading-4 text-slate-500">
                {data.movers.basis}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Covered symbols</CardTitle>
                <span className="text-[10px] text-slate-500">
                  Minimum {data.evidence.min_sessions_required} sessions required
                </span>
              </CardHeader>
              <CardContent className="overflow-x-auto p-0">
                <table className="w-full min-w-[680px] text-left text-xs">
                  <thead className="border-b border-slate-800 text-[10px] uppercase tracking-wider text-slate-500">
                    <tr>
                      <th className="px-4 py-2">Symbol</th>
                      <th className="px-4 py-2">Close</th>
                      <th className="px-4 py-2">1d</th>
                      <th className="px-4 py-2">5d</th>
                      <th className="px-4 py-2">20d</th>
                      <th className="px-4 py-2">Vol vs 20d</th>
                      <th className="px-4 py-2">As of</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.symbols.map((row) => (
                      <tr key={row.symbol} className="border-b border-slate-900/80">
                        <td className="px-4 py-2 text-slate-100">
                          {row.symbol}
                          {row.freshness?.stale && <span className="ml-2 text-[10px] text-warning">stale</span>}
                        </td>
                        <td className="px-4 py-2 text-slate-200">{inr(row.last_close)}</td>
                        <td className={`px-4 py-2 ${(row.change_pct_1d ?? 0) >= 0 ? "text-gain" : "text-loss"}`}>
                          <span className="inline-flex items-center gap-1">
                            {(row.change_pct_1d ?? 0) >= 0 ? <ArrowUpRight size={12} aria-hidden="true" /> : <ArrowDownRight size={12} aria-hidden="true" />}
                            {pct(row.change_pct_1d)}
                          </span>
                        </td>
                        <td className="px-4 py-2 text-slate-300">{pct(row.change_pct_5d)}</td>
                        <td className="px-4 py-2 text-slate-300">{pct(row.change_pct_20d)}</td>
                        <td className="px-4 py-2 text-slate-300">
                          {row.volume_vs_20d_average == null ? "—" : `${row.volume_vs_20d_average.toFixed(2)}x`}
                        </td>
                        <td className="px-4 py-2 text-slate-500">{row.as_of ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </CardContent>
              {data.coverage.excluded.length > 0 && (
                <CardContent className="border-t border-slate-800 text-[10px] leading-4 text-slate-500">
                  Excluded:{" "}
                  {data.coverage.excluded
                    .map((row) => `${row.symbol} (${row.reason}${row.sessions_available != null ? `, ${row.sessions_available} sessions` : ""})`)
                    .join("; ")}
                </CardContent>
              )}
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Newspaper aria-hidden="true" size={14} /> Headlines
                </CardTitle>
                <span className="text-[10px] text-slate-500">State: {data.headlines.state}</span>
              </CardHeader>
              <CardContent className="space-y-2">
                {data.headlines.items.length === 0 && (
                  <p className="text-xs text-slate-500">
                    {data.headlines.note ?? "No attributed headlines were returned. Nothing is inferred from their absence."}
                  </p>
                )}
                {data.headlines.items.map((item, index) => (
                  <div key={`${item.symbol}-${index}`} className="text-xs">
                    <span className="text-slate-500">{item.symbol}</span>{" "}
                    {item.url ? (
                      <a href={item.url} target="_blank" rel="noreferrer" className="text-slate-100 underline decoration-slate-700">
                        {item.title ?? item.url}
                      </a>
                    ) : (
                      <span className="text-slate-100">{item.title ?? "Untitled"}</span>
                    )}
                    <span className="ml-2 text-[10px] text-slate-500">
                      {item.publisher ?? "unattributed"}
                      {item.is_stale ? " · stale" : ""}
                    </span>
                  </div>
                ))}
                {data.headlines.sentiment_basis && (
                  <p className="text-[10px] leading-4 text-slate-500">{data.headlines.sentiment_basis}</p>
                )}
              </CardContent>
            </Card>

            <ul className="space-y-1 text-[10px] leading-4 text-slate-500">
              <li>{data.evidence.basis}</li>
              {data.disclosures.map((line) => <li key={line}>{line}</li>)}
            </ul>
          </>
        )}
      </div>
    </TerminalShell>
  );
}
