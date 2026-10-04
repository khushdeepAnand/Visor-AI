"use client";

import { AlertTriangle, Info, Shield } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { MarginEstimate, PayoffLivePnl, PayoffMargin } from "@/lib/api";

interface MarginEstimateCardProps {
  margin?: MarginEstimate | PayoffMargin | {
    state: "available" | "unavailable";
    reason?: string;
    span_margin?: number;
    exposure_margin?: number;
    total_margin?: number;
    approximate_margin?: boolean;
    note?: string;
  };
  breakevens?: number[];
  live_pnl?: PayoffLivePnl | {
    state: "available" | "unavailable";
    pnl?: number;
    basis?: string;
    missing_legs?: number[];
  };
  disclosure: string;
  children?: React.ReactNode;
}

export function MarginEstimateCard({
  margin,
  breakevens,
  live_pnl,
  disclosure,
  children,
}: MarginEstimateCardProps) {
  // Check if it's a PayoffMargin (has state property)
  const isPayoffMargin = margin && "state" in margin;
  const marginAvailable = isPayoffMargin && margin.state === "available";
  const marginData = margin && !isPayoffMargin ? margin as MarginEstimate | undefined : undefined;
  const livePnlAvailable = live_pnl && live_pnl.state === "available";

  return (
    <Card className="border-warning/30 bg-warning/5">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <Shield className="text-warning" size={14} />
          Margin estimate (not a broker requirement)
          <span className="ml-auto rounded bg-slate-800 px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-slate-400">
            Non-Official
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="rounded-lg border border-warning/25 bg-warning/5 p-3 text-xs leading-5 text-warning">
          <strong className="text-warning">Important Disclosure:</strong> {disclosure}
        </div>
        <p className="text-xs text-slate-400">Official margin not available — broker risk files are not held.</p>

        {marginAvailable && isPayoffMargin && (
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="rounded-lg border border-slate-800 bg-terminal-900 p-3">
              <div className="text-[10px] uppercase tracking-wider text-slate-500">SPAN Margin</div>
              <div className="mt-1 tabular text-lg text-white">
                {margin.span_margin != null ? `₹${margin.span_margin.toLocaleString()}` : "—"}
              </div>
            </div>
            <div className="rounded-lg border border-slate-800 bg-terminal-900 p-3">
              <div className="text-[10px] uppercase tracking-wider text-slate-500">Exposure Margin</div>
              <div className="mt-1 tabular text-lg text-white">
                {margin.exposure_margin != null ? `₹${margin.exposure_margin.toLocaleString()}` : "—"}
              </div>
            </div>
            <div className="rounded-lg border border-slate-800 bg-terminal-900 p-3">
              <div className="text-[10px] uppercase tracking-wider text-slate-500">Total Margin</div>
              <div className="mt-1 tabular text-lg font-semibold text-accent">
                {margin.total_margin != null ? `₹${margin.total_margin.toLocaleString()}` : "—"}
              </div>
            </div>
          </div>
        )}

        {marginData && (
          <div className="space-y-4">
            <p className="text-xs text-slate-400">Basis: {marginData.basis} over {marginData.scenarios_evaluated} scenarios</p>
            <div className="rounded-lg border border-slate-800 bg-terminal-900 p-3">
              <div className="text-[10px] uppercase tracking-wider text-slate-500">Total Margin</div>
              <div className="mt-1 tabular text-lg font-semibold text-accent">
                {marginData.total_margin != null ? `₹${marginData.total_margin.toLocaleString()}` : "—"}
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-lg border border-slate-800 bg-terminal-900 p-3">
                <div className="text-[10px] uppercase tracking-wider text-slate-500">Basis</div>
                <div className="mt-1 tabular text-lg text-white">{marginData.basis}</div>
              </div>
              <div className="rounded-lg border border-slate-800 bg-terminal-900 p-3">
                <div className="text-[10px] uppercase tracking-wider text-slate-500">Scenarios Evaluated</div>
                <div className="mt-1 tabular text-lg text-white">{marginData.scenarios_evaluated}</div>
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
              <div className="rounded-lg border border-slate-800 bg-terminal-900 p-3">
                <div className="text-[10px] uppercase tracking-wider text-slate-500">Short premium floor</div>
                <div className="mt-1 tabular text-lg text-white">
                  {marginData.short_premium_floor != null ? `₹${marginData.short_premium_floor.toLocaleString()}` : "—"}
                </div>
              </div>
              <div className="rounded-lg border border-slate-800 bg-terminal-900 p-3">
                <div className="text-[10px] uppercase tracking-wider text-slate-500">Standalone sum</div>
                <div className="mt-1 tabular text-lg text-white">
                  {marginData.sum_standalone != null ? `₹${marginData.sum_standalone.toLocaleString()}` : "—"}
                </div>
              </div>
              <div className="rounded-lg border border-slate-800 bg-terminal-900 p-3">
                <div className="text-[10px] uppercase tracking-wider text-slate-500">Strategy offset</div>
                <div className="mt-1 tabular text-lg text-white">
                  {marginData.strategy_offset != null ? `₹${marginData.strategy_offset.toLocaleString()}` : "—"}
                </div>
              </div>
            </div>
            {marginData.worst_scenario && (
              <div className="rounded-lg border border-warning/25 bg-warning/5 p-3 text-xs leading-5 text-warning">
                <div className="text-[10px] uppercase tracking-wider text-slate-500">Worst scenario</div>
                <div className="mt-1">Underlying {marginData.worst_scenario.underlying_move_pct}%, volatility {marginData.worst_scenario.volatility_change_pct >= 0 ? "+" : ""}{marginData.worst_scenario.volatility_change_pct}% → loss ₹{marginData.worst_scenario.loss?.toLocaleString()}</div>
              </div>
            )}
            {marginData.per_leg && marginData.per_leg.length > 0 && (
              <div className="rounded-lg border border-slate-800 bg-terminal-900 p-3">
                <div className="text-[10px] uppercase tracking-wider text-slate-500">Per-Leg Standalone Margin</div>
                <div className="mt-2 overflow-x-auto">
                  <table className="w-full min-w-[400px] text-left text-xs">
                    <thead className="border-b border-slate-800 text-[10px] uppercase tracking-wider text-slate-500">
                      <tr>
                        <th className="px-3 py-1">Leg</th>
                        <th className="px-3 py-1">Side</th>
                        <th className="px-3 py-1">Type</th>
                        <th className="px-3 py-1">Standalone</th>
                        <th className="px-3 py-1">Derivation</th>
                      </tr>
                    </thead>
                    <tbody>
                      {marginData.per_leg.map((leg, i) => (
                        <tr key={i} className="border-b border-slate-900/80">
                          <td className="px-3 py-1 text-slate-300">{leg.label ?? `Leg ${leg.index}`}</td>
                          <td className="px-3 py-1 text-slate-300">{leg.side}</td>
                          <td className="px-3 py-1 text-slate-300">{leg.type}</td>
                          <td className="px-3 py-1 tabular text-white">₹{leg.standalone_margin?.toLocaleString()}</td>
                          <td className="px-3 py-1 text-slate-500">{leg.derivation}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
            <div className="rounded-lg border border-warning/25 bg-warning/5 p-3 text-xs leading-5 text-warning">
              <strong className="text-warning">Disclosure:</strong> {marginData.disclosures?.join(" ") ?? "Scan-based estimate, not a broker requirement."}
            </div>
          </div>
        )}

        {!marginAvailable && isPayoffMargin && margin && (
          <div className="rounded-lg border border-slate-800 bg-terminal-900 p-3 text-sm text-slate-400">
            <div className="flex items-center gap-2 text-warning">
              <AlertTriangle size={14} />
              <span>No margin estimate: {margin.reason || "Unknown reason"}</span>
            </div>
            {margin.note && <p className="mt-2 text-xs text-slate-500">{margin.note}</p>}
          </div>
        )}

        {breakevens && breakevens.length > 0 && (
          <div>
            <div className="text-[10px] uppercase tracking-wider text-slate-500">Breakeven Points</div>
            <div className="mt-2 flex flex-wrap gap-2">
              {breakevens.map((be, i) => (
                <span key={i} className="rounded bg-slate-800 px-2 py-1 text-xs font-mono text-slate-300">
                  ₹{be.toFixed(2)}
                </span>
              ))}
            </div>
          </div>
        )}

        {livePnlAvailable && live_pnl && live_pnl.pnl != null && (
          <div className="rounded-lg border border-slate-800 bg-terminal-900 p-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Info className="text-info" size={14} />
                <span className="text-xs text-slate-400">Live Mark-to-Market P&L</span>
                {live_pnl.basis && (
                  <span className="text-[9px] text-slate-500">({live_pnl.basis})</span>
                )}
              </div>
              <div className={`tabular text-lg font-semibold ${live_pnl.pnl >= 0 ? "text-gain" : "text-loss"}`}>
                {live_pnl.pnl >= 0 ? "+" : ""}₹{live_pnl.pnl.toFixed(2)}
              </div>
            </div>
          </div>
        )}

        {!livePnlAvailable && live_pnl && (
          <div className="rounded-lg border border-slate-800 bg-terminal-900 p-3 text-sm text-slate-400">
            <div className="flex items-center gap-2 text-warning">
              <AlertTriangle size={14} />
              <span>Live P&L unavailable: {live_pnl.basis || "Live marks not supplied"}</span>
            </div>
            {live_pnl.missing_legs && live_pnl.missing_legs.length > 0 && (
              <p className="mt-1 text-[10px] text-slate-500">
                Missing marks for legs: {live_pnl.missing_legs.join(", ")}
              </p>
            )}
          </div>
        )}

        {children}
      </CardContent>
    </Card>
  );
}
