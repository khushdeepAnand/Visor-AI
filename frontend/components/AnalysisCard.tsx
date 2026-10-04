"use client";

import React from "react";
import { clsx } from "clsx";
import { twMerge } from "tailwind-merge";
import {
  ChartBar,
  AlertTriangle,
  Info,
  Shield,
  TrendingUp,
  TrendingDown,
  Zap,
  Database,
  Target,
  GitBranch,
  ExternalLink,
  Loader2,
} from "lucide-react";

function cn(...inputs: (string | undefined | null | false)[]) {
  return twMerge(clsx(inputs));
}

interface AnalysisCardProps {
  data: {
    symbol: string;
    tier: string;
    evidence_grade: string;
    research_range: {
      low: number;
      median_reference: number;
      high: number;
      confidence_level: number;
      currency: string;
    } | null;
    reference_price: number;
    confidence: { level: string; summary: string };
    volatility_forecast: {
      expected_daily_vol_pct: number;
      expected_range_pct: number;
      model: string;
    } | null;
    market_regime: {
      regime: string;
      trend: string;
      volatility: string;
      probability: number;
    } | null;
    tier_info: {
      tier: string;
      primary_output: string;
      model_stack: string[];
      reason: string;
    } | null;
    fan_chart: Record<string, number> | null;
    scenarios: Array<{ label: string; low: number; high: number; description: string }>;
    observation_zone: { low: number; high: number; label: string } | null;
    risk_zone: { low: number; high: number; label: string } | null;
    zones_unavailable_reason: string | null;
    low_utility: boolean;
    abstained: boolean;
    abstention_reason: string | null;
    decay_card: {
      overall_status: string;
      summary: string;
      live_coverage: number | null;
      backtest_coverage: number | null;
      auto_widened: boolean;
      widen_factor: number;
      signals: Array<{ type: string; severity: string; description: string }>;
    } | null;
    scorecard_summary: {
      tier_coverage: number;
      tier_winkler: number;
      tier_mase: number | null;
    } | null;
    peer_context: {
      peers: string[];
      peer_behavior: string;
    } | null;
    ipo_context: {
      days_since_listing: number | null;
      lockin_dates: string[];
      peer_prior: string;
    } | null;
    generated_at: string;
    disclaimer: string;
  };
}

export function AnalysisCard({ data }: AnalysisCardProps) {
  const d = data;

  if (!d) return <div className="p-4 text-center text-muted-foreground">No analysis data</div>;

  const tierColors: Record<string, string> = {
    T0: "bg-red-500/10 text-red-500 border-red-500/20",
    T1: "bg-orange-500/10 text-orange-500 border-orange-500/20",
    T2: "bg-yellow-500/10 text-yellow-500 border-yellow-500/20",
    T3: "bg-green-500/10 text-green-500 border-green-500/20",
    T4: "bg-blue-500/10 text-blue-500 border-blue-500/20",
  };

  const confidenceColors: Record<string, string> = {
    high: "bg-green-500/10 text-green-500",
    moderate: "bg-yellow-500/10 text-yellow-500",
    low: "bg-red-500/10 text-red-500",
  };

  const range = d.research_range;
  const width = range ? range.high - range.low : 0;
  const widthPct = range && d.reference_price ? (width / d.reference_price * 100).toFixed(2) : "N/A";

  const tier = d.tier_info?.tier || d.tier || "unknown";

  // Handle abstained case
  if (d.abstained) {
    return (
      <div className="rounded-lg border border-destructive/20 bg-destructive/5 p-4">
        <div className="flex items-center gap-2 mb-2">
          <AlertTriangle className="h-5 w-5 text-destructive" />
          <h3 className="font-semibold text-destructive">No Range Published</h3>
        </div>
        <p className="text-sm text-muted-foreground mb-2">
          {d.abstention_reason || "Insufficient evidence for a calibrated range."}
        </p>
        {d.tier_info && (
          <div className="text-xs text-muted-foreground">
            Tier: {d.tier_info.tier} — {d.tier_info.reason}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-4 rounded-xl border bg-card p-4 shadow-sm">
      {/* Header with Tier Badge */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">{d.symbol}</h2>
          <p className="text-xs text-muted-foreground">
            Generated: {new Date(d.generated_at).toLocaleString()}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className={cn("inline-flex items-center px-2 py-1 rounded-full text-xs font-medium", tierColors[tier] || "")}>
            {d.tier_info?.tier || d.tier}
          </span>
          <span className={cn("inline-flex items-center px-2 py-1 rounded-full text-xs font-medium", confidenceColors[d.confidence?.level] || "")}>
            {d.confidence?.level?.toUpperCase()} Confidence
          </span>
          {d.low_utility && (
            <span className="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-purple-500/10 text-purple-500">
              Low Utility
            </span>
          )}
        </div>
      </div>

      {/* Tier Info */}
      {d.tier_info && (
        <div className="rounded-lg bg-muted/50 p-3 text-sm">
          <div className="font-medium mb-1">Tier: {d.tier_info.tier}</div>
          <div className="text-muted-foreground mb-1">{d.tier_info.reason}</div>
          <div className="text-muted-foreground">Primary Output: {d.tier_info.primary_output}</div>
          <div className="text-muted-foreground">Model Stack: {d.tier_info.model_stack.join(", ")}</div>
        </div>
      )}

      {/* Research Range */}
      {range && (
        <div className="rounded-lg border bg-muted/30 p-4">
          <div className="flex items-center justify-between mb-2">
            <h3 className="font-medium">Research Range ({Math.round(range.confidence_level * 100)}%)</h3>
            <span className="text-xs text-muted-foreground">
              Width: {widthPct}% ({width.toFixed(2)} {range.currency})
            </span>
          </div>
          <div className="grid grid-cols-3 gap-4 text-center">
            <div className="rounded-lg bg-destructive/10 p-3">
              <div className="text-xs text-destructive">Low</div>
              <div className="font-mono font-semibold">{range.low.toFixed(2)}</div>
            </div>
            <div className="rounded-lg bg-primary/10 p-3">
              <div className="text-xs text-primary">Median</div>
              <div className="font-mono font-semibold">{range.median_reference.toFixed(2)}</div>
            </div>
            <div className="rounded-lg bg-green-500/10 p-3">
              <div className="text-xs text-green-500">High</div>
              <div className="font-mono font-semibold">{range.high.toFixed(2)}</div>
            </div>
          </div>
          <div className="mt-2 text-xs text-muted-foreground">
            Reference: {d.reference_price.toFixed(2)} {range.currency} | {d.confidence?.summary}
          </div>
        </div>
      )}

      {/* Fan Chart */}
      {d.fan_chart && Object.keys(d.fan_chart).length > 0 && (
        <div className="rounded-lg border bg-muted/30 p-4">
          <h3 className="font-medium mb-2">Fan Chart (Quantiles)</h3>
          <div className="flex flex-wrap gap-2">
            {Object.entries(d.fan_chart).map(([q, val]) => (
              <span key={q} className="inline-flex items-center px-2 py-1 rounded bg-muted text-xs font-mono">
                q{q}: {val.toFixed(2)}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Volatility Forecast */}
      {d.volatility_forecast && (
        <div className="rounded-lg border bg-muted/30 p-4">
          <div className="flex items-center justify-between mb-2">
            <h3 className="font-medium flex items-center gap-2">
              <Zap className="h-4 w-4" /> Volatility Forecast
            </h3>
            <span className="text-xs text-muted-foreground">Model: {d.volatility_forecast.model}</span>
          </div>
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <div className="text-muted-foreground">Expected Daily Vol</div>
              <div className="font-mono font-semibold">{d.volatility_forecast.expected_daily_vol_pct.toFixed(2)}%</div>
            </div>
            <div>
              <div className="text-muted-foreground">Expected Range (±1σ)</div>
              <div className="font-mono font-semibold">±{d.volatility_forecast.expected_range_pct.toFixed(2)}%</div>
            </div>
          </div>
        </div>
      )}

      {/* Market Regime */}
      {d.market_regime && (
        <div className="rounded-lg border bg-muted/30 p-4">
          <div className="flex items-center justify-between mb-2">
            <h3 className="font-medium flex items-center gap-2">
              <GitBranch className="h-4 w-4" /> Market Regime
            </h3>
            <span className="text-xs text-muted-foreground">Probability: {(d.market_regime.probability * 100).toFixed(0)}%</span>
          </div>
          <div className="flex flex-wrap gap-2 text-xs">
            <span className="inline-flex items-center px-2 py-1 rounded bg-muted">{d.market_regime.regime}</span>
            <span className="inline-flex items-center px-2 py-1 rounded bg-muted">Trend: {d.market_regime.trend}</span>
            <span className="inline-flex items-center px-2 py-1 rounded bg-muted">Vol: {d.market_regime.volatility}</span>
          </div>
        </div>
      )}

      {/* Scenarios */}
      {d.scenarios && d.scenarios.length > 0 && (
        <div className="rounded-lg border bg-muted/30 p-4">
          <h3 className="font-medium mb-2">Scenario Bands</h3>
          <div className="grid grid-cols-3 gap-2">
            {d.scenarios.map((s) => (
              <div key={s.label} className="rounded-lg bg-muted p-2 text-center">
                <div className="text-xs font-medium">{s.label}</div>
                <div className="font-mono text-sm">{s.low.toFixed(2)} – {s.high.toFixed(2)}</div>
                <div className="text-xs text-muted-foreground">{s.description}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Zones */}
      {((d.observation_zone || d.risk_zone) && !d.zones_unavailable_reason) && (
        <div className="rounded-lg border bg-muted/30 p-4">
          <h3 className="font-medium mb-2">Derived Zones</h3>
          <div className="grid grid-cols-2 gap-2">
            {d.observation_zone && (
              <div className="rounded-lg bg-blue-500/10 p-3">
                <div className="text-xs text-blue-500">{d.observation_zone.label}</div>
                <div className="font-mono">{d.observation_zone.low.toFixed(2)} – {d.observation_zone.high.toFixed(2)}</div>
              </div>
            )}
            {d.risk_zone && (
              <div className="rounded-lg bg-red-500/10 p-3">
                <div className="text-xs text-red-500">{d.risk_zone.label}</div>
                <div className="font-mono">{d.risk_zone.low.toFixed(2)} – {d.risk_zone.high.toFixed(2)}</div>
              </div>
            )}
          </div>
        </div>
      )}

      {d.zones_unavailable_reason && (
        <div className="rounded-lg border border-yellow-500/20 bg-yellow-500/5 p-3">
          <div className="flex items-center gap-2 text-sm text-yellow-700">
            <Info className="h-4 w-4" />
            {d.zones_unavailable_reason}
          </div>
        </div>
      )}

      {/* Decay Card */}
      {d.decay_card && (
        <div className={cn("rounded-lg border p-4", d.decay_card.overall_status === "degraded" && "border-destructive/20 bg-destructive/5")}>
          <div className="flex items-center justify-between mb-2">
            <h3 className="font-medium flex items-center gap-2">
              <Shield className="h-4 w-4" /> Live Model Health
            </h3>
            <span className={cn("text-xs font-medium px-2 py-1 rounded",
              d.decay_card.overall_status === "healthy" && "bg-green-500/10 text-green-500",
              d.decay_card.overall_status === "watch" && "bg-yellow-500/10 text-yellow-500",
              d.decay_card.overall_status === "degraded" && "bg-red-500/10 text-red-500"
            )}>
              {d.decay_card.overall_status.toUpperCase()}
            </span>
          </div>
          <p className="text-sm text-muted-foreground mb-2">{d.decay_card.summary}</p>

          <div className="grid grid-cols-2 gap-2 mb-2 text-sm">
            <div>
              <div className="text-muted-foreground">Live Coverage</div>
              <div className="font-mono">{d.decay_card.live_coverage ? (d.decay_card.live_coverage * 100).toFixed(1) + "%" : "N/A"}</div>
            </div>
            <div>
              <div className="text-muted-foreground">Backtest Coverage</div>
              <div className="font-mono">{d.decay_card.backtest_coverage ? (d.decay_card.backtest_coverage * 100).toFixed(1) + "%" : "N/A"}</div>
            </div>
          </div>

          {d.decay_card.auto_widened && (
            <div className="rounded-lg bg-amber-500/10 border border-amber-500/20 p-2 text-sm text-amber-700">
              <div className="flex items-center gap-1">
                <AlertTriangle className="h-3 w-3" />
                Intervals auto-widened by {((d.decay_card.widen_factor - 1) * 100).toFixed(0)}%
              </div>
            </div>
          )}

          {d.decay_card.signals && d.decay_card.signals.length > 0 && (
            <div className="mt-2 space-y-1">
              {d.decay_card.signals.map((s, i) => (
                <div key={i} className="text-xs text-muted-foreground flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-current" />
                  {s.description}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Peer/IPO Context */}
      {(d.peer_context || d.ipo_context) && (
        <div className="rounded-lg border bg-muted/30 p-4">
          <h3 className="font-medium mb-2 flex items-center gap-2">
            <Database className="h-4 w-4" /> Context
          </h3>
          {d.peer_context && (
            <div className="mb-2">
              <div className="text-sm font-medium">Peer Companies: {d.peer_context.peers.join(", ")}</div>
              <div className="text-xs text-muted-foreground">{d.peer_context.peer_behavior}</div>
            </div>
          )}
          {d.ipo_context && (
            <div>
              {d.ipo_context.days_since_listing !== null && (
                <div className="text-sm">Days since listing: {d.ipo_context.days_since_listing}</div>
              )}
              {d.ipo_context.lockin_dates.length > 0 && (
                <div className="text-xs text-muted-foreground">Lock-in expiries: {d.ipo_context.lockin_dates.join(", ")}</div>
              )}
              <div className="text-xs text-muted-foreground">{d.ipo_context.peer_prior}</div>
            </div>
          )}
        </div>
      )}

      {/* Scorecard Summary */}
      {d.scorecard_summary && (
        <div className="rounded-lg border bg-muted/30 p-4">
          <h3 className="font-medium mb-2 flex items-center gap-2">
            <Target className="h-4 w-4" /> Tier Scorecard
          </h3>
          <div className="grid grid-cols-3 gap-4 text-sm">
            <div>
              <div className="text-muted-foreground">Coverage</div>
              <div className="font-mono font-semibold">{(d.scorecard_summary.tier_coverage * 100).toFixed(1)}%</div>
            </div>
            <div>
              <div className="text-muted-foreground">Winkler Score</div>
              <div className="font-mono font-semibold">{d.scorecard_summary.tier_winkler.toFixed(4)}</div>
            </div>
            <div>
              <div className="text-muted-foreground">MASE</div>
              <div className="font-mono font-semibold">{d.scorecard_summary.tier_mase?.toFixed(4) ?? "N/A"}</div>
            </div>
          </div>
        </div>
      )}

      {/* Disclaimer */}
      <div className="rounded-lg bg-muted/30 p-3 text-xs text-muted-foreground">
        {d.disclaimer}
      </div>
    </div>
  );
}

export function AnalysisCardSkeleton() {
  return (
    <div className="space-y-4 rounded-xl border bg-card p-4">
      <div className="h-4 w-1/4 bg-muted animate-pulse rounded" />
      <div className="h-20 bg-muted animate-pulse rounded" />
      <div className="h-16 bg-muted animate-pulse rounded" />
      <div className="h-16 bg-muted animate-pulse rounded" />
      <div className="h-16 bg-muted animate-pulse rounded" />
    </div>
  );
}