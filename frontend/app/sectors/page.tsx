"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CircleSlash, Info, Layers3, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { TerminalShell } from "@/components/TerminalShell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api, formatApiError } from "@/lib/api";
import { pct } from "@/lib/utils";

type SectorEntry = { symbol: string; change_pct_5d: number | null };
type SectorBlock = {
  sector: string;
  symbol_count: number;
  measured_count: number;
  avg_change_pct_5d: number | null;
  symbols: SectorEntry[];
};

type SectorRotation = {
  generated_at?: string;
  partial_coverage: boolean;
  sector_count: number;
  sectors: SectorBlock[];
  coverage: {
    catalogue: { total: number; sector_populated: number; sector_populated_pct: number; distinct_sectors: number };
    runtime_eq_total: number | null;
    basis: string;
  };
  disclosures: string[];
};

function isFeatureDisabled(error: unknown): boolean {
  return typeof error === "object" && error !== null && "code" in error && (error as { code?: string }).code === "feature_disabled";
}

export default function SectorsPage() {
  const [expanded, setExpanded] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ["sector-rotation"],
    queryFn: () => api<SectorRotation>("/api/v1/sector-rotation"),
    staleTime: 60_000,
  });
  const rows = useMemo(() => {
    const list = [...(query.data?.sectors ?? [])];
    return list.sort((a, b) => (b.avg_change_pct_5d ?? -Infinity) - (a.avg_change_pct_5d ?? -Infinity));
  }, [query.data]);

  if (isFeatureDisabled(query.error)) {
    return (
      <TerminalShell>
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-3 py-4 sm:px-4">
          <header>
            <h1 className="display-font text-lg font-semibold text-slate-100">Sector rotation</h1>
          </header>
          <Card>
            <CardContent className="space-y-2 py-4">
              <p role="status" className="text-sm text-slate-300">
                Sector rotation is currently turned off by an operator feature flag.
              </p>
              <p className="text-xs text-slate-500">
                An administrator can enable the <code>sector_rotation</code> flag under Admin → Operations.
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
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 px-3 py-4 sm:px-4">
        <header className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h1 className="display-font text-lg font-semibold text-slate-100">Sector rotation</h1>
            <p className="text-xs text-slate-500">
              Realized 5-session returns grouped by sector, over the symbols the stored catalogue actually assigns.
            </p>
          </div>
          {data?.partial_coverage && (
            <span className="rounded bg-amber-900/50 px-2 py-1 text-[10px] uppercase tracking-wide text-amber-300">
              Partial coverage
            </span>
          )}
        </header>

        {query.isError && (
          <Card>
            <CardContent className="py-4">
              <p role="alert" className="text-sm text-loss">{formatApiError(query.error)}</p>
            </CardContent>
          </Card>
        )}

        {data?.partial_coverage !== false && (
          <Card className="border-amber-900/60">
            <CardContent className="flex items-start gap-3 py-4">
              <TriangleAlert aria-hidden="true" size={16} className="mt-0.5 shrink-0 text-amber-300" />
              <div className="text-xs leading-relaxed text-slate-300">
                <p className="font-medium uppercase tracking-wide text-amber-300">Partial coverage</p>
                <p className="mt-1">
                  {data
                    ? `Sector metadata exists for ${data.coverage.catalogue.sector_populated} of ${data.coverage.catalogue.total} catalogue symbols (${data.coverage.catalogue.sector_populated_pct}%); the runtime broker instrument master carries no sector field.`
                    : "Sector metadata coverage is being checked."}{" "}
                  This view shows only what the stored catalogue can group, so it is not a full-universe rotation.
                </p>
              </div>
            </CardContent>
          </Card>
        )}

        {data?.coverage && (
          <div className="grid gap-2 sm:grid-cols-3">
            <Card>
              <CardHeader className="pb-0"><CardTitle className="text-[10px] uppercase tracking-[.18em] text-slate-500">Catalogue symbols</CardTitle></CardHeader>
              <CardContent className="py-3 text-sm text-slate-100">{data.coverage.catalogue.total}</CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-0"><CardTitle className="text-[10px] uppercase tracking-[.18em] text-slate-500">With sector</CardTitle></CardHeader>
              <CardContent className="py-3 text-sm text-slate-100">
                {data.coverage.catalogue.sector_populated}{" "}
                <span className="text-[11px] text-slate-500">({data.coverage.catalogue.distinct_sectors} sectors)</span>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-0"><CardTitle className="text-[10px] uppercase tracking-[.18em] text-slate-500">Sectors grouped</CardTitle></CardHeader>
              <CardContent className="py-3 text-sm text-slate-100">{data.sector_count}</CardContent>
            </Card>
          </div>
        )}

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><Layers3 aria-hidden="true" size={14} /> Sector beats</CardTitle>
            <p className="text-xs text-slate-500">Sectors are listed by average realized change over the last five sessions. Not a forecast or ranking to trade on.</p>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            {rows.length === 0 ? (
              <div className="flex flex-col items-center gap-2 py-8 text-center">
                <CircleSlash aria-hidden="true" size={20} className="text-slate-600" />
                <p className="text-sm text-slate-400">No symbols have sector assignments in the catalogue yet.</p>
                <p className="max-w-md text-xs text-slate-500">
                  Backfill <code>symbols.sector</code> (e.g. by extending the instrument master with sector/industry fields) and this
                  view will populate automatically.
                </p>
              </div>
            ) : (
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-800 text-left text-[11px] uppercase tracking-wider text-slate-500">
                    <th className="px-4 py-2">Sector</th>
                    <th className="px-4 py-2 text-right">Symbols</th>
                    <th className="px-4 py-2 text-right">Measured</th>
                    <th className="px-4 py-2 text-right">Avg change, 5 sessions</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((block) => (
                    <SectorRow
                      key={block.sector}
                      block={block}
                      expanded={expanded === block.sector}
                      onToggle={() => setExpanded(expanded === block.sector ? null : block.sector)}
                    />
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>

        {data?.disclosures?.length ? (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-xs uppercase tracking-wider text-slate-500"><Info aria-hidden="true" size={12} /> Disclosures</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-xs text-slate-500">
              {data.disclosures.map((item) => <p key={item}>{item}</p>)}
              {data.coverage?.basis && <p>{data.coverage.basis}</p>}
            </CardContent>
          </Card>
        ) : null}
      </div>
    </TerminalShell>
  );
}

function SectorRow({ block, expanded, onToggle }: { block: SectorBlock; expanded: boolean; onToggle: () => void }) {
  const avg = block.avg_change_pct_5d;
  return (
    <>
      <tr
        className="cursor-pointer border-b border-slate-800/70 hover:bg-terminal-900"
        onClick={onToggle}
        aria-expanded={expanded}
      >
        <td className="px-4 py-2 font-medium text-slate-200">{block.sector}</td>
        <td className="px-4 py-2 text-right text-slate-400">{block.symbol_count}</td>
        <td className="px-4 py-2 text-right text-slate-500">{block.measured_count}</td>
        <td className={`px-4 py-2 text-right ${avg == null ? "text-slate-600" : avg >= 0 ? "text-gain" : "text-loss"}`}>
          {avg == null ? "–" : pct(avg)}
        </td>
      </tr>
      {expanded && (
        <tr className="border-b border-slate-800/70 bg-terminal-900/40">
          <td colSpan={4} className="px-4 py-3">
            <div className="flex flex-wrap gap-1.5">
              {block.symbols.map((entry) => (
                <Link
                  key={entry.symbol}
                  href={`/markets/${encodeURIComponent(entry.symbol)}`}
                  className="inline-flex min-h-8 items-center gap-1 rounded border border-slate-800 px-2 text-[11px] text-slate-300 hover:border-slate-600 hover:text-slate-100"
                >
                  {entry.symbol}
                  <span className={entry.change_pct_5d == null ? "text-slate-600" : entry.change_pct_5d >= 0 ? "text-gain" : "text-loss"}>
                    {entry.change_pct_5d == null ? "n/a" : pct(entry.change_pct_5d)}
                  </span>
                </Link>
              ))}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}