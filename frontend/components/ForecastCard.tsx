import { Activity, CheckCircle2, CircleSlash2, Clock3, Database, MinusCircle, RefreshCw, ShieldCheck, TriangleAlert, Beaker } from "lucide-react";
import type { ExpectedMoveBlock, Forecast, ForecastSupportState, ResearchBand, CQRCanaryStatus } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { Button } from "./ui/button";
import { inr } from "@/lib/utils";
import { MarketSourceLabel } from "@/components/MarketContext";
import { ForecastFan } from "@/components/ForecastFan";

const stateDetails: Record<ForecastSupportState, { label: string; tone: string; explanation: string; icon: typeof ShieldCheck | typeof CircleSlash2 | typeof TriangleAlert | typeof Clock3 | typeof Database | typeof MinusCircle }> = {
   model_supported: { label: "Model supported", tone: "border-gain/35 bg-gain/5 text-gain", explanation: "The untouched chronological test reports skill against persistence. Future performance remains uncertain.", icon: ShieldCheck },
  baseline_only: { label: "Baseline only", tone: "border-warning/35 bg-warning/5 text-warning", explanation: "A calibrated model is unavailable; only a naive persistence baseline was published.", icon: CircleSlash2 },
  low_evidence: { label: "Low evidence", tone: "border-warning/35 bg-warning/5 text-warning", explanation: "Insufficient history or calibration data to promote a model for this symbol.", icon: TriangleAlert },
  drift_blocked: { label: "Drift blocked", tone: "border-loss/35 bg-loss/5 text-loss", explanation: "A promoted model showed performance drift and was blocked from publishing.", icon: MinusCircle },
  data_quality_blocked: { label: "Data quality blocked", tone: "border-loss/35 bg-loss/5 text-loss", explanation: "Input data failed freshness or quality checks; no range was published.", icon: Database },
  abstained: { label: "Abstained", tone: "border-info/35 bg-info/5 text-info", explanation: "The system declined to publish a range for this symbol under current conditions.", icon: Clock3 },
};

function CQRCanaryBadge({ status }: { status: CQRCanaryStatus | null | undefined }) {
  if (!status) return null;
  
  const isActive = status.enabled && status.canary_assigned;
  const isControl = status.status === "control_arm";
  
  if (!isActive && !isControl) return null; // Don't show badge if disabled
  
  const tone = isActive ? "border-accent/35 bg-accent/5 text-accent" : "border-info/35 bg-info/5 text-info";
  const label = isActive ? "CQR Canary" : "CQR Control";
  const tooltip = isActive 
    ? `Conformalized Quantile Regression active for this symbol (${status.canary_percentage}% rollout)`
    : "CQR control arm - offsets withheld for coverage comparison";
  
  return (
    <span 
      title={tooltip} 
      className={`whitespace-nowrap rounded-full border px-2 py-1 text-[10px] font-semibold uppercase tracking-wide ${tone}`}
    >
      <Beaker aria-hidden="true" className="mr-1 inline" size={11} />
      {label}
    </span>
  );
}

export function forecastSupportState(data: Forecast): ForecastSupportState {
  if (data.forecast_status && data.forecast_status in stateDetails) return data.forecast_status;
  if (data.support_state && data.support_state in stateDetails) return data.support_state;
  if (data.forecast_state && data.forecast_state in stateDetails) return data.forecast_state;
  if (data.abstained) return "abstained";
  if (data.low_evidence || data.support_state === "low_evidence" || data.forecast_state === "low_evidence") return "low_evidence";
  if (data.baseline_only || data.support_state === "baseline_only" || data.forecast_state === "baseline_only" || data.model_supported === false) return "baseline_only";
  if (data.model_label?.toLowerCase().includes("baseline")) return "baseline_only";
  if (data.confidence?.level === "low") return "low_evidence";
  return "model_supported";
}

function Scenario({ band }: { band: ResearchBand }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-950/35 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-semibold text-slate-200">{band.label}</span>
        <span className="tabular text-xs text-slate-300">{inr(band.low)} to {inr(band.high)}</span>
      </div>
      <p className="mt-1 text-[11px] leading-4 text-slate-500">{band.description}</p>
    </div>
  );
}

function ExpectedMoveBlock({ block }: { block: ExpectedMoveBlock }) {
  if (!block.available) {
    return block.message ? (
      <p className="mt-3 rounded-lg border border-slate-800 bg-slate-950/30 p-3 text-[11px] leading-4 text-slate-500"><Activity aria-hidden="true" className="mr-1 inline" size={13} />{block.message}</p>
    ) : null;
  }
  const moves = block.expected_moves || [];
  const crossover = block.model_crossover;
  return (
    <section className="mt-4 rounded-lg border border-accent/15 bg-slate-950/35 p-3" aria-labelledby="expected-move-title">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="expected-move-title" className="text-xs font-semibold uppercase tracking-[.16em] text-slate-300">Option-implied expected move</h3>
        <span className="text-[10px] text-slate-500">{block.expiry ? `${block.expiry} · ${block.days_to_expiry ?? "–"}d` : "derived from ATM IV"}{block.source ? ` · ${block.source}` : ""}</span>
      </div>
      <div className="mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-1 text-xs text-slate-400">
        {block.spot_price != null && <span>Spot <b className="tabular text-slate-200">{inr(block.spot_price)}</b></span>}
        {block.atm_iv != null && <span>ATM IV <b className="tabular text-slate-200">{(block.atm_iv * 100).toFixed(1)}%</b></span>}
      </div>
      {moves.length > 0 && (
        <div className="mt-2 grid gap-1 sm:grid-cols-2">
          {moves.map((move) => (
            <div key={move.sigma} className="flex items-center justify-between rounded border border-slate-800 bg-terminal-950/60 px-2 py-1 text-[11px]">
              <span className="text-slate-500">±{move.sigma}σ</span>
              <span className="tabular text-slate-200">{move.move_pct.toFixed(2)}% · {inr(move.low)}–{inr(move.high)}</span>
            </div>
          ))}
        </div>
      )}
      {crossover && (
        <p className={`mt-2 rounded border px-2 py-1.5 text-[11px] leading-4 ${crossover.flag === "aligned" ? "border-info/25 bg-info/5 text-info" : "border-warning/35 bg-warning/5 text-warning"}`}>{crossover.message}</p>
      )}
      {block.disclosure && <p className="mt-2 text-[10px] leading-4 text-slate-600">{block.disclosure}</p>}
    </section>
  );
}

function TrustStack({ data, state }: { data: Forecast; state: ForecastSupportState }) {
  const details = stateDetails[state];
  const StateIcon = details.icon;
  const fallback = (label: string) => ({ status: "limited", summary: `${label} was not reported.` });
  const checks = [
    ["Data", data.trust?.data || fallback("Data quality")],
    ["Model", data.trust?.model || fallback("Model evidence")],
    ["Market regime", data.trust?.market_regime || fallback("Market regime")],
    ["Liquidity", data.trust?.liquidity || fallback("Liquidity")],
  ] as const;

  return (
    <section className="mt-4" aria-labelledby="trust-stack-title">
      <div className="mb-2 flex items-center gap-2">
        <ShieldCheck aria-hidden="true" size={15} className="text-accent" />
        <h3 id="trust-stack-title" className="text-xs font-semibold uppercase tracking-[.16em] text-slate-300">Trust Stack</h3>
      </div>
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        {checks.map(([label, check]) => <div key={label} className={`rounded-lg border p-3 ${check.status === "blocked" ? "border-loss/35 bg-loss/5" : check.status === "strong" ? "border-info/35 bg-info/5" : "border-warning/35 bg-warning/5"}`}><div className="flex items-center gap-2 text-[10px] uppercase tracking-wide text-slate-400">{label === "Data" ? <Database aria-hidden="true" size={12}/> : <StateIcon aria-hidden="true" size={12}/>} {label}</div><div className="mt-1 text-xs font-semibold text-slate-200">{check.status}</div><p className="mt-1 text-[10px] leading-4 text-slate-500">{check.summary}</p></div>)}
      </div>
      <p className="mt-2 text-[11px] leading-5 text-slate-500">{data.abstention_reason || details.explanation}</p>
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-terminal-950/60 px-2.5 py-2">
      <div className="text-[9px] uppercase tracking-[.14em] text-slate-500">{label}</div>
      <div className="mt-0.5 tabular text-xs font-semibold text-slate-200">{value}</div>
    </div>
  );
}

function AssessmentSection({ data }: { data: Forecast }) {
  const a = data.assessment;
  if (!a) return null;
  const prob = a.probability;
  const regime = a.market_regime;
  const agreement = a.model_agreement;
  const confidence = a.confidence_score;
  const dq = a.data_quality;
  const explanation = a.explanation;
  const upPct = prob == null ? null : Math.max(0, Math.min(100, Math.round(prob.up * 100)));
  const downPct = upPct == null ? null : 100 - upPct;
  return (
    <section className="mt-5 rounded-lg border border-slate-800 bg-slate-950/35 p-3" aria-labelledby="assessment-title">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="assessment-title" className="text-xs font-semibold uppercase tracking-[.16em] text-slate-400">Model assessment</h3>
        {confidence && (
          <span className="text-xs text-slate-400">Evidence diagnostic <b className="tabular text-base text-white">{confidence.score}/100</b> <span className="text-[10px]">{confidence.level}</span></span>
        )}
      </div>
      {confidence && <p className="mt-2 text-xs text-slate-400">This diagnostic summarizes evidence checks. It is not a calibrated probability of accuracy or profit.</p>}
      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Expected move" value={a.expected_return_pct != null ? `${a.expected_return_pct >= 0 ? "+" : ""}${a.expected_return_pct.toFixed(2)}%` : "–"} />
        <Stat label="Expected volatility" value={a.expected_volatility_pct != null ? `±${a.expected_volatility_pct.toFixed(2)}%` : "–"} />
        <Stat label="Market regime" value={regime?.available ? (regime.label ?? "Unclassified") : "Unclassified"} />
        <Stat label="Model agreement" value={agreement?.available ? `${agreement.level ?? "–"} · ${agreement.dispersion_pct?.toFixed(2) ?? "–"}%` : "–"} />
      </div>
      {prob != null && upPct != null && downPct != null && (
        <div className="mt-3">
          <div className="flex items-center justify-between text-[10px] uppercase tracking-wide text-slate-500">
            <span>Downside {downPct}%</span>
            <span>Upside {upPct}%</span>
          </div>
          <div className="mt-1 flex h-2 overflow-hidden rounded-full border border-slate-700 bg-slate-900" role="img" aria-label={`Probability of upside ${upPct}%, downside ${downPct}%`}>
            <div className="bg-loss/70" style={{ width: `${downPct}%` }} />
            <div className="bg-gain/70" style={{ width: `${upPct}%` }} />
          </div>
          <p className="mt-1 text-[10px] leading-4 text-slate-500">{prob.basis}</p>
        </div>
      )}
      {explanation && explanation.reasons.length > 0 && (
        <details className="mt-3" open>
          <summary className="cursor-pointer select-none text-[11px] font-semibold uppercase tracking-[.14em] text-slate-400">Why this range?</summary>
          <ul className="mt-2 space-y-1">
            {explanation.reasons.map((reason, i) => (
              <li key={i} className="rounded border border-slate-800 bg-terminal-950/60 px-2 py-1.5 text-[11px] leading-4 text-slate-400">{reason}</li>
            ))}
          </ul>
        </details>
      )}
      {dq && dq.notes.length > 0 && <p className="mt-2 text-[10px] leading-4 text-warning">{dq.notes.join(" · ")}</p>}
    </section>
  );
}

export function ForecastCard({ data, loading, error, onRetry, retrainedAt }: { data?: Forecast; loading?: boolean; error?: string; onRetry?: () => void; retrainedAt?: string | null }) {
  const range = data?.research_range;
  const state = data ? forecastSupportState(data) : undefined;
  const marker = range && range.high > range.low ? Math.max(0, Math.min(100, ((data!.reference_price - range.low) / (range.high - range.low)) * 100)) : 50;
  const medianMarker = range && range.high > range.low ? Math.max(0, Math.min(100, ((range.median_reference - range.low) / (range.high - range.low)) * 100)) : 50;
  return (
    <Card className="h-full border-accent/15 bg-terminal-900/90">
      <MarketSourceLabel context={data?.context} />
      <CardHeader className="flex-wrap gap-2">
        <div>
          <div className="text-[10px] uppercase tracking-[.2em] text-accent">Decision support</div>
          <CardTitle className="mt-1 text-base">Forecast Corridor</CardTitle>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {state && <span className={`whitespace-nowrap rounded-full border px-2 py-1 text-[10px] font-semibold uppercase tracking-wide ${stateDetails[state].tone}`}>{stateDetails[state].label}</span>}
          {data?.evidence && <span className="whitespace-nowrap rounded-full border border-warning/35 bg-warning/5 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-warning">Evidence {data.evidence.grade}</span>}
          {data?.low_data && <span title="Limited verified history: the stabilised fallback branch produced this range with an extra safety margin." className="whitespace-nowrap rounded-full border border-warning/35 bg-warning/5 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-warning"><Activity aria-hidden="true" className="mr-1 inline" size={11} />Low history</span>}
          {data?.provenance?.as_of && (
            <span title={`Market data as of ${new Date(data.provenance.as_of).toLocaleString()}`} className={`whitespace-nowrap rounded-full border px-2 py-1 text-[10px] font-semibold uppercase tracking-wide ${data.provenance.is_stale ? "border-warning/35 bg-warning/5 text-warning" : "border-gain/35 bg-gain/5 text-gain"}`}><Clock3 aria-hidden="true" className="mr-1 inline" size={11} />{data.provenance.is_stale ? "Stale data" : `As of ${new Date(data.provenance.as_of).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`}</span>
          )}
          {retrainedAt && <span title={`Model last (re)trained ${new Date(retrainedAt).toLocaleString()}`} className="whitespace-nowrap rounded-full border border-accent/35 bg-accent/5 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-accent"><RefreshCw aria-hidden="true" className="mr-1 inline" size={11} />Retrained {new Date(retrainedAt).toLocaleDateString()}</span>}
          {data?.cqr_canary_status && <CQRCanaryBadge status={data.cqr_canary_status} />}
        </div>
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="space-y-3" role="status" aria-live="polite">
            <p className="text-xs text-slate-400">Building the corridor. Long-running healthy forecasts will continue in the background.</p>
            <div className="skeleton h-8 w-4/5 rounded" />
            <div className="skeleton h-3 w-full rounded" />
            <div className="skeleton h-24 w-full rounded-lg" />
          </div>
        ) : error ? (
          <div role="alert" className="rounded-lg border border-loss/30 bg-loss/5 p-4">
            <div className="text-sm font-semibold text-loss">Forecast corridor unavailable</div>
            <p className="mt-2 text-xs leading-5 text-slate-400">{error}</p>
            {onRetry && <Button type="button" variant="ghost" className="mt-3" onClick={onRetry}>Retry with the same context</Button>}
          </div>
        ) : data ? (
          <>
            {state && <TrustStack data={data} state={state} />}
            {range && state && ["model_supported", "baseline_only", "low_evidence"].includes(state) ? (
              <section className="mt-5" aria-labelledby="corridor-range-title">
                <h3 id="corridor-range-title" className="text-xs font-semibold uppercase tracking-[.16em] text-slate-400">Research range</h3>
                <div className="mt-2" aria-label={`${range.confidence_level * 100}% research range from ${range.low} to ${range.high}`}>
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <span className="tabular text-xl font-semibold text-white sm:text-2xl">{inr(range.low)} to {inr(range.high)}</span>
                    <span className="text-xs text-slate-400">{Math.round(range.confidence_level * 100)}% range</span>
                  </div>
                  <div className="relative mt-4 h-3 overflow-hidden rounded-full border border-accent/20 bg-slate-900">
                    <div className="absolute inset-0 bg-gradient-to-r from-indigo-400/45 via-accent/75 to-sky-400/45" />
                    <div className="absolute inset-y-0 w-px bg-white/90" style={{ left: `${medianMarker}%` }} aria-hidden="true" />
                    <div className="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-slate-950 bg-warning" style={{ left: `${marker}%` }} aria-hidden="true" />
                  </div>
                  <div className="mt-2 grid grid-cols-3 gap-2 text-[10px] text-slate-500 sm:text-[11px]">
                    <span>Lower scenario</span><span className="tabular text-center">Median {inr(range.median_reference)}</span><span className="text-right">Upper scenario</span>
                  </div>
                  <div className="mt-3 grid gap-1 text-[11px] text-slate-400 sm:grid-cols-2"><span>Width: {inr(data.uncertainty.range_width)} / {data.uncertainty.range_width_pct.toFixed(2)}%</span><span className="sm:text-right">Current marker: {inr(data.reference_price)}</span></div>
                </div>
              </section>
            ) : (
              <div className="mt-4 rounded-lg border border-slate-700 bg-slate-950/35 p-4 text-sm leading-6 text-slate-300"><b>No numerical corridor released.</b><p className="mt-1 text-xs text-slate-400">{data.abstention_reason || stateDetails[state || "abstained"].explanation}</p><p className="mt-2 text-xs text-slate-500">You can still inspect verified history. Retry after a fresh candle or after the blocked trust check is resolved.</p></div>
            )}

            <AssessmentSection data={data} />
            {range && data.reference_price != null && <ForecastFan current={data.reference_price} points={data.horizons?.filter(entry => !entry.abstained && entry.forecast).map(entry => ({ sessions: entry.sessions, low: entry.forecast!.low, median: entry.forecast!.median_reference, high: entry.forecast!.high, nominal: entry.forecast!.confidence_level })) ?? [{ sessions: data.horizon?.bars ?? 1, low: range.low, median: range.median_reference, high: range.high, nominal: range.confidence_level }]} />}

            {data.horizons && data.horizons.length > 0 && (
              <section className="mt-5" aria-labelledby="horizon-ladder-title">
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <h3 id="horizon-ladder-title" className="text-xs font-semibold uppercase tracking-[.16em] text-slate-400">Session ladder</h3>
                  <span className="text-[10px] text-slate-500">independent horizons, own test evidence</span>
                </div>
                <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                  {data.horizons.map((entry) => {
                    const corridor = entry.forecast;
                    const blocked = entry.abstained || !corridor;
                    return (
                      <div key={entry.sessions} className={`rounded-lg border p-3 ${blocked ? "border-loss/25 bg-loss/5" : "border-accent/20 bg-slate-950/35"}`}>
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-[10px] font-semibold uppercase tracking-wide text-slate-300">{entry.label}</span>
                          <span className="tabular text-[10px] text-slate-500">{entry.target_timestamp ? new Date(entry.target_timestamp).toLocaleDateString() : "–"}</span>
                        </div>
                        {blocked ? (
                          <p className="mt-2 text-[11px] leading-4 text-slate-500">{entry.abstention_reason || "No corridor released for this horizon."}</p>
                        ) : (
                          <>
                            <div className="mt-2 tabular text-sm font-semibold text-white">{inr(corridor!.low)} – {inr(corridor!.high)}</div>
                            <div className="mt-1 text-[11px] text-slate-400">Median {inr(corridor!.median_reference)}</div>
                            {entry.width_pct != null && <div className="mt-1 text-[10px] text-slate-500">Width {entry.width_pct.toFixed(2)}%</div>}
                          </>
                        )}
                      </div>
                    );
                  })}
                </div>
                {data.unavailable_horizons && data.unavailable_horizons.length > 0 && (
                  <p className="mt-2 text-[11px] leading-4 text-warning">{data.unavailable_horizons.map((u) => `${u.sessions} sessions: not enough verified history`).join("  ·  ")}</p>
                )}
                {data.horizon_consistency && (
                  <div className={`mt-3 rounded-lg border p-3 ${data.horizon_consistency.level === "high" ? "border-gain/25 bg-gain/5" : data.horizon_consistency.level === "low" ? "border-loss/25 bg-loss/5" : "border-warning/25 bg-warning/5"}`}>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <h4 className="text-[11px] font-semibold uppercase tracking-wide text-slate-300">Cross-horizon consistency</h4>
                      <span className="tabular text-[10px] text-slate-400">{data.horizon_consistency.score == null ? "Unavailable" : `${data.horizon_consistency.score}/100 · ${data.horizon_consistency.level}`}</span>
                    </div>
                    <p className="mt-1 text-[11px] leading-4 text-slate-400">{data.horizon_consistency.summary}</p>
                    <p className="mt-1 text-[10px] leading-4 text-slate-500">Consistency measures agreement between horizons, not forecast accuracy; no corridor is adjusted or hidden.</p>
                  </div>
                )}
              </section>
            )}

            {data.expected_move && <ExpectedMoveBlock block={data.expected_move} />}

            {data.confidence && data.uncertainty && <div className="mt-4 rounded-lg border border-slate-800 bg-slate-950/30 p-3"><p className="text-xs leading-5 text-slate-300">{data.confidence.summary}</p><p className="mt-1 text-[11px] leading-4 text-slate-500">{data.uncertainty.summary}</p></div>}
            {data.scenarios?.length > 0 && <div className="mt-3 grid gap-2">{data.scenarios.map((band) => <Scenario key={band.label} band={band} />)}</div>}
            {(data.observation_zone || data.risk_zone) && <div className="mt-3 grid gap-2 sm:grid-cols-2">{data.observation_zone && <Scenario band={data.observation_zone} />}{data.risk_zone && <Scenario band={data.risk_zone} />}</div>}
            {!data.observation_zone && !data.risk_zone && data.zones_unavailable_reason && <p className="mt-3 rounded-lg border border-warning/20 bg-warning/5 p-3 text-[11px] leading-4 text-warning"><TriangleAlert aria-hidden="true" className="mr-1 inline" size={13} />{data.zones_unavailable_reason}</p>}

            <div className="mt-4 border-t border-slate-800 pt-3 text-[11px] leading-5 text-slate-500">
              <div>Source: {data.provenance?.source || data.context?.provider || "unavailable"}{data.provenance?.market_state ? ` / ${data.provenance.market_state}` : ""}{data.provenance?.is_stale ? " / stale" : data.provenance?.is_live ? " / live" : ""}</div>
              <div>As of: {data.provenance?.as_of ? new Date(data.provenance.as_of).toLocaleString() : "timestamp unavailable"}</div>
              <div>For: {data.target_timestamp ? new Date(data.target_timestamp).toLocaleString() : data.horizon?.label || "target time unavailable"}</div>
              <div>Status: {state ? stateDetails[state].label : "Unavailable"} / Evidence: {data.evidence?.grade || "not reported"}</div>
              <details className="mt-2"><summary className="min-h-11 cursor-pointer py-3 text-slate-300">Why this grade?</summary><p className="mt-1">{data.evidence?.summary || data.confidence.summary}</p><p className="mt-1">An {Math.round((range?.confidence_level || 0.8) * 100)}% interval describes historical coverage, not an {Math.round((range?.confidence_level || 0.8) * 100)}% chance of profit. The median is a reference, not a target.</p></details>
              <p className="mt-2 text-slate-400">{data.disclaimer}</p>
            </div>
          </>
        ) : (
          <p className="text-sm leading-6 text-slate-500">Choose a supported timeframe to build a forecast corridor. Unsupported combinations remain unavailable rather than using substitute data.</p>
        )}
      </CardContent>
    </Card>
  );
}
