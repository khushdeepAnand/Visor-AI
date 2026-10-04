"use client";

import React, { useEffect, useState } from "react";
import { clsx } from "clsx";
import { twMerge } from "tailwind-merge";
import { ChevronDown, ChevronUp, Download, Filter, RefreshCw, TrendingUp, TrendingDown, AlertTriangle, Shield, Database, Loader2 } from "lucide-react";

function cn(...inputs: (string | undefined | null | false)[]) {
  return twMerge(clsx(inputs));
}

interface ScorecardTier {
  n_forecasts: number;
  coverage: number;
  target_coverage: number;
  winkler_score: number;
  mase: number | null;
  mae: number | null;
  directional_accuracy: number | null;
  conditional_coverage: Record<string, number>;
}

interface ScorecardData {
  generated_at: string;
  disclaimer: string;
  tiers: Record<string, ScorecardTier>;
  overall: {
    total_forecasts: number;
    coverage: number | null;
    winkler_score: number | null;
    mase: number | null;
    mae: number | null;
  };
  conditional_coverage: Record<string, number>;
  promotion_gates: Record<string, { passed: boolean; coverage_within_tolerance: boolean; mase_not_worse_than_naive: boolean }>;
  model_actions: { auto_widened: number; retired: number };
}

export function PublicScorecard() {
  const [data, setData] = useState<ScorecardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tierFilter, setTierFilter] = useState<string>("");

  const fetchScorecard = async () => {
    try {
      setLoading(true);
      setError(null);
      const params = new URLSearchParams();
      if (tierFilter) params.set("tier", tierFilter);
      const res = await fetch(`/api/v1/scorecard?${params.toString()}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      setData(json);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load scorecard");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchScorecard();
  }, [tierFilter]);

  if (loading && !data) {
    return (
      <div className="space-y-4 p-4">
        <div className="h-8 w-1/3 bg-muted animate-pulse rounded" />
        <div className="grid grid-cols-4 gap-4">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-24 bg-muted animate-pulse rounded" />
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-lg border border-destructive/20 bg-destructive/5 p-4">
        <div className="flex items-center gap-2 text-destructive">
          <AlertTriangle className="h-5 w-5" />
          <span className="font-medium">Failed to load scorecard</span>
        </div>
        <p className="text-sm text-muted-foreground mt-1">{error}</p>
        <button
          onClick={fetchScorecard}
          className="mt-3 inline-flex items-center gap-2 px-3 py-1.5 text-sm font-medium rounded-lg bg-primary text-primary-foreground hover:bg-primary/90"
        >
          <RefreshCw className="h-4 w-4" /> Retry
        </button>
      </div>
    );
  }

  if (!data) return null;

  const tiers = ["T0", "T1", "T2", "T3", "T4"];
  const filteredTiers = tierFilter ? [tierFilter] : tiers.filter(t => data.tiers[t]);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Public Model Scorecard</h1>
          <p className="text-sm text-muted-foreground">
            Rolling track record for all tiers • Updated {new Date(data.generated_at).toLocaleString()}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={tierFilter}
            onChange={(e) => setTierFilter(e.target.value)}
            className="px-3 py-1.5 text-sm border rounded-lg bg-background"
          >
            <option value="">All Tiers</option>
            {tiers.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
          <button
            onClick={fetchScorecard}
            disabled={loading}
            className="inline-flex items-center gap-2 px-3 py-1.5 text-sm font-medium rounded-lg border hover:bg-muted"
          >
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
            Refresh
          </button>
        </div>
      </div>

      {/* Disclaimer */}
      <div className="rounded-lg bg-muted/30 p-3 text-xs text-muted-foreground">
        {data.disclaimer}
      </div>

      {/* Overall Summary */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          label="Total Forecasts"
          value={data.overall.total_forecasts.toLocaleString()}
          icon={<Database className="h-5 w-5" />}
        />
        <StatCard
          label="Coverage"
          value={data.overall.coverage !== null ? `${(data.overall.coverage * 100).toFixed(1)}%` : "N/A"}
          target="Target: 80% ± 5%"
          trend={data.overall.coverage !== null && data.overall.coverage >= 0.75 ? "up" : "down"}
          icon={<Shield className="h-5 w-5" />}
        />
        <StatCard
          label="Winkler Score"
          value={data.overall.winkler_score !== null ? data.overall.winkler_score.toFixed(4) : "N/A"}
          trend="down"
          icon={<TrendingDown className="h-5 w-5" />}
        />
        <StatCard
          label="MASE"
          value={data.overall.mase !== null ? data.overall.mase.toFixed(4) : "N/A"}
          target="≤ 1.0 (not worse than naive)"
          trend={data.overall.mase !== null && data.overall.mase <= 1.0 ? "up" : "down"}
          icon={<TrendingUp className="h-5 w-5" />}
        />
      </div>

      {/* Model Actions */}
      <div className="rounded-lg border bg-muted/30 p-4">
        <h3 className="font-medium mb-2 flex items-center gap-2">
          <Shield className="h-4 w-4" /> Model Actions
        </h3>
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <div className="text-muted-foreground">Auto-Widened</div>
            <div className="font-mono font-semibold">{data.model_actions.auto_widened}</div>
          </div>
          <div>
            <div className="text-muted-foreground">Retired</div>
            <div className="font-mono font-semibold">{data.model_actions.retired}</div>
          </div>
        </div>
      </div>

      {/* Conditional Coverage */}
      <div className="rounded-lg border bg-muted/30 p-4">
        <h3 className="font-medium mb-3 flex items-center gap-2">
          <Database className="h-4 w-4" /> Conditional Coverage by Regime
        </h3>
        <div className="flex flex-wrap gap-2">
          {Object.entries(data.conditional_coverage).map(([regime, cov]) => (
            <span key={regime} className="inline-flex items-center px-3 py-1 rounded-full bg-muted text-sm">
              {regime}: <span className="font-mono ml-1">{(cov * 100).toFixed(1)}%</span>
            </span>
          ))}
          {Object.keys(data.conditional_coverage).length === 0 && (
            <span className="text-sm text-muted-foreground">No conditional coverage data available</span>
          )}
        </div>
      </div>

      {/* Tier Details */}
      <div className="space-y-4">
        {filteredTiers.map((tier) => {
          const t = data.tiers[tier];
          if (!t) return null;

          const gate = data.promotion_gates[tier];
          const coverageGap = t.coverage - t.target_coverage;

          return (
            <TierCard
              key={tier}
              tier={tier}
              data={t}
              gate={gate}
              coverageGap={coverageGap}
            />
          );
        })}
      </div>

      {/* Disclaimer */}
      <div className="rounded-lg bg-muted/30 p-3 text-xs text-muted-foreground text-center">
        {data.disclaimer}
      </div>
    </div>
  );
}

function StatCard({
  label,
  value,
  target,
  trend,
  icon,
}: {
  label: string;
  value: string;
  target?: string;
  trend?: "up" | "down";
  icon: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="flex items-center justify-between">
        <span className="text-sm text-muted-foreground">{label}</span>
        {icon}
      </div>
      <div className="mt-2 font-mono text-2xl font-bold">{value}</div>
      {target && <div className="text-xs text-muted-foreground mt-1">{target}</div>}
      {trend && (
        <div className={cn("mt-1 text-xs font-medium", trend === "up" ? "text-green-500" : "text-red-500")}>
          {trend === "up" ? "✓ Within target" : "✗ Below target"}
        </div>
      )}
    </div>
  );
}

interface TierCardProps {
  tier: string;
  data: ScorecardTier;
  gate: { passed: boolean; coverage_within_tolerance: boolean; mase_not_worse_than_naive: boolean };
  coverageGap: number;
}

function TierCard({ tier, data, gate, coverageGap }: TierCardProps) {
  const tierColors: Record<string, string> = {
    T0: "bg-red-500/10 text-red-500 border-red-500/20",
    T1: "bg-orange-500/10 text-orange-500 border-orange-500/20",
    T2: "bg-yellow-500/10 text-yellow-500 border-yellow-500/20",
    T3: "bg-green-500/10 text-green-500 border-green-500/20",
    T4: "bg-blue-500/10 text-blue-500 border-blue-500/20",
  };

  return (
    <div className="rounded-xl border bg-card p-4 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-4 mb-4">
        <div>
          <span className={cn("inline-flex items-center px-3 py-1 rounded-full text-sm font-medium", tierColors[tier] || "")}>
            {tier}
          </span>
          <span className="ml-2 text-sm text-muted-foreground">{data.n_forecasts} forecasts</span>
        </div>
        <div className="flex items-center gap-3">
          <span className={cn("inline-flex items-center px-2 py-1 rounded-full text-xs font-medium",
            gate.passed ? "bg-green-500/10 text-green-500" : "bg-red-500/10 text-red-500"
          )}>
            {gate.passed ? "Gate Passed" : "Gate Failed"}
          </span>
          <span className={cn("inline-flex items-center px-2 py-1 rounded-full text-xs font-medium",
            coverageGap >= -0.05 && coverageGap <= 0.05 ? "bg-green-500/10 text-green-500" : "bg-red-500/10 text-red-500"
          )}>
            Coverage {coverageGap >= 0 ? "+" : ""}{(coverageGap * 100).toFixed(1)}pp
          </span>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
        <Metric label="Coverage" value={`${(data.coverage * 100).toFixed(1)}%`} target={`Target: ${(data.target_coverage * 100).toFixed(0)}%`} />
        <Metric label="Winkler" value={data.winkler_score.toFixed(4)} />
        <Metric label="MASE" value={data.mase !== null ? data.mase.toFixed(4) : "N/A"} target="≤ 1.0" />
        <Metric label="Dir. Acc." value={data.directional_accuracy !== null ? `${(data.directional_accuracy * 100).toFixed(1)}%` : "N/A"} />
      </div>

      {/* Gate Details */}
      <div className="rounded-lg bg-muted/30 p-3 text-sm">
        <div className="font-medium mb-2">Promotion Gate Checks</div>
        <div className="grid grid-cols-2 gap-2">
          <GateCheck label="Coverage ±5%" passed={gate.coverage_within_tolerance} />
          <GateCheck label="MASE ≤ 1.0" passed={gate.mase_not_worse_than_naive} />
        </div>
      </div>

      {/* Conditional Coverage */}
      {Object.keys(data.conditional_coverage).length > 0 && (
        <div className="mt-3 rounded-lg bg-muted/30 p-3">
          <div className="font-medium mb-2">Conditional Coverage</div>
          <div className="flex flex-wrap gap-2">
            {Object.entries(data.conditional_coverage).map(([regime, cov]) => (
              <span key={regime} className="inline-flex items-center px-2 py-1 rounded bg-muted text-xs">
                {regime}: <span className="font-mono ml-1">{(cov * 100).toFixed(1)}%</span>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Metric({ label, value, target }: { label: string; value: string; target?: string }) {
  return (
    <div className="rounded-lg bg-muted/30 p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="font-mono font-semibold text-lg">{value}</div>
      {target && <div className="text-xs text-muted-foreground mt-1">{target}</div>}
    </div>
  );
}

function GateCheck({ label, passed }: { label: string; passed: boolean }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className={cn("w-1.5 h-1.5 rounded-full", passed ? "bg-green-500" : "bg-red-500")} />
      <span className={cn(passed ? "text-green-500" : "text-red-500")}>{label}</span>
    </div>
  );
}