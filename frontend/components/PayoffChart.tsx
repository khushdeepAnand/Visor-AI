"use client";

import { useMemo } from "react";

/**
 * Expiry payoff diagram for a multi-leg strategy.
 *
 * Pure presentation over the payload from `POST /api/v1/options/payoff`: the
 * backend owns all arithmetic, including breakevens and whether an extreme lies
 * outside the plotted range. Rendered as an inline SVG so it needs no charting
 * dependency and works in jsdom tests.
 *
 * The bound labels deliberately repeat the backend's own wording: a downside
 * that is finite only because price cannot fall below zero must not read as a
 * hedged position.
 */

export type PayoffExtreme = {
  unbounded: boolean;
  value: number | null;
  at_price: number | null;
  outside_plotted_range?: boolean;
  note?: string | null;
};

export type PayoffResponse = {
  underlying?: string | null;
  analysis_label?: string;
  spot: number;
  net_premium?: number;
  position?: string;
  payoff_at_spot?: number;
  max_profit: PayoffExtreme;
  max_loss: PayoffExtreme;
  risk_reward_ratio?: number | null;
  breakevens?: number[];
  grid?: { low: number; high: number; points: number; span: number };
  curve: Array<{ price: number; payoff: number }>;
  tails?: {
    upside_slope_per_point?: number;
    downside_slope_per_point?: number;
    profit_unbounded?: boolean;
    loss_unbounded?: boolean;
    downside_bounded_by_zero?: boolean;
  };
  is_forecast?: boolean;
  is_recommendation?: boolean;
  margin?: { state: string; reason?: string; note?: string; span_margin?: number; exposure_margin?: number; total_margin?: number; approximate_margin?: boolean };
  live_pnl?: { state: "available" | "unavailable"; pnl?: number; basis?: string; missing_legs?: number[] };
  margin_unavailable_reason?: string;
  disclosures?: string[];
};

const WIDTH = 640;
const HEIGHT = 260;
const PAD = { top: 14, right: 16, bottom: 26, left: 52 };

function formatInr(value?: number | null) {
  if (value == null || !Number.isFinite(value)) return "—";
  return `₹${value.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}

export function PayoffChart({ data }: { data: PayoffResponse }) {
  const geometry = useMemo(() => {
    const curve = (data.curve ?? []).filter(
      (point) => Number.isFinite(point?.price) && Number.isFinite(point?.payoff),
    );
    if (curve.length < 2) return null;
    const prices = curve.map((point) => point.price);
    const payoffs = curve.map((point) => point.payoff);
    const minPrice = Math.min(...prices);
    const maxPrice = Math.max(...prices);
    const rawMin = Math.min(...payoffs, 0);
    const rawMax = Math.max(...payoffs, 0);
    const cushion = (rawMax - rawMin) * 0.08 || 1;
    const minPayoff = rawMin - cushion;
    const maxPayoff = rawMax + cushion;
    const plotWidth = WIDTH - PAD.left - PAD.right;
    const plotHeight = HEIGHT - PAD.top - PAD.bottom;
    const x = (price: number) =>
      PAD.left + ((price - minPrice) / (maxPrice - minPrice || 1)) * plotWidth;
    const y = (payoff: number) =>
      PAD.top + (1 - (payoff - minPayoff) / (maxPayoff - minPayoff || 1)) * plotHeight;
    const zeroY = y(0);
    const line = curve.map((point) => `${x(point.price)},${y(point.payoff)}`).join(" ");
    const profitArea = `${PAD.left},${zeroY} ${line} ${PAD.left + plotWidth},${zeroY}`;
    return { curve, x, y, zeroY, line, profitArea, minPrice, maxPrice, plotWidth, plotHeight };
  }, [data.curve]);

  if (!geometry) {
    return (
      <p role="status" className="text-xs text-slate-500">
        No payoff curve is available for this strategy.
      </p>
    );
  }

  const { x, y, zeroY, line, profitArea, plotWidth } = geometry;
  const breakevens = (data.breakevens ?? []).filter((price) => Number.isFinite(price));

  return (
    <div className="flex flex-col gap-3">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="w-full"
        role="img"
        aria-label={`Payoff at expiry for ${data.underlying ?? "the selected strategy"}. Maximum profit ${data.max_profit?.unbounded ? "unbounded" : formatInr(data.max_profit?.value)}, maximum loss ${formatInr(data.max_loss?.value)}.`}
      >
        <polygon points={profitArea} fill="currentColor" className="text-accent/15" />
        <line x1={PAD.left} y1={zeroY} x2={PAD.left + plotWidth} y2={zeroY} stroke="currentColor" strokeDasharray="3 3" className="text-slate-600" />
        <polyline points={line} fill="none" stroke="currentColor" strokeWidth={2} className="text-accent" />
        {Number.isFinite(data.spot) && (
          <g>
            <line x1={x(data.spot)} y1={PAD.top} x2={x(data.spot)} y2={HEIGHT - PAD.bottom} stroke="currentColor" strokeDasharray="4 4" className="text-slate-500" />
            <text x={x(data.spot)} y={HEIGHT - 8} textAnchor="middle" className="fill-slate-500 text-[9px]">spot {data.spot}</text>
          </g>
        )}
        {breakevens.map((price) => (
          <g key={`be-${price}`}>
            <circle cx={x(price)} cy={zeroY} r={3.5} fill="currentColor" className="text-warning" />
            <text x={x(price)} y={zeroY - 7} textAnchor="middle" className="fill-warning text-[9px]">{price}</text>
          </g>
        ))}
        <text x={PAD.left - 8} y={y(0) + 3} textAnchor="end" className="fill-slate-500 text-[9px]">0</text>
      </svg>

      <dl className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
        <div>
          <dt className="text-[10px] uppercase tracking-wider text-slate-500">Max profit</dt>
          <dd className="text-gain">{data.max_profit?.unbounded ? "Unbounded" : formatInr(data.max_profit?.value)}</dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase tracking-wider text-slate-500">Max loss</dt>
          <dd className="text-loss">{data.max_loss?.unbounded ? "Unbounded" : formatInr(data.max_loss?.value)}</dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase tracking-wider text-slate-500">Breakeven</dt>
          <dd className="text-slate-200">{breakevens.length ? breakevens.join(", ") : "None in range"}</dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase tracking-wider text-slate-500">At spot</dt>
          <dd className="text-slate-200">{formatInr(data.payoff_at_spot)}</dd>
        </div>
      </dl>

      {data.net_premium != null && (
        <p className="text-[10px] leading-4 text-slate-500">
          Net premium {data.net_premium >= 0 ? "debit" : "credit"}: {formatInr(Math.abs(data.net_premium))}
        </p>
      )}

      {(data.max_profit?.note || data.max_loss?.note) && (
        <ul className="space-y-1 text-[10px] leading-4 text-slate-500">
          {data.max_profit?.note ? <li>Upside: {data.max_profit.note}</li> : null}
          {data.max_loss?.note ? <li>Downside: {data.max_loss.note}</li> : null}
        </ul>
      )}

      {data.margin?.state === "unavailable" && (
        <p className="text-[10px] leading-4 text-slate-500">
          Margin is not estimated: {data.margin.note ?? data.margin.reason ?? "broker risk parameters are not held"}
        </p>
      )}

      {data.disclosures?.length ? (
        <ul className="space-y-1 text-[10px] leading-4 text-slate-500">
          {data.disclosures.map((line) => <li key={line}>{line}</li>)}
        </ul>
      ) : null}
    </div>
  );
}

export default PayoffChart;
