"use client";

import { Line, LineChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { inr } from "@/lib/utils";

/**
 * Cumulative net P&L curve for a strategy backtest. Points are built
 * client-side from the closed trades the backtest returns; the backend does
 * not publish an equity series, so the curve is recomputed here from the same
 * running-net-pnl accumulation the backend uses for `max_drawdown_pct`.
 */

export type EquityPoint = { trade: number; equity: number };

export function StrategyEquityChart({ points }: { points: EquityPoint[] }) {
  if (!points.length) {
    return <p className="py-6 text-center text-xs text-slate-500">No closed trades to plot.</p>;
  }
  return (
    <div className="h-56 w-full" data-testid="strategy-equity-chart">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 10, right: 12, bottom: 2, left: 18 }}>
          <CartesianGrid stroke="#172335" vertical={false} />
          <XAxis dataKey="trade" tick={{ fill: "#728197", fontSize: 10 }} axisLine={{ stroke: "#223047" }} tickLine={false} />
          <YAxis tick={{ fill: "#728197", fontSize: 10 }} axisLine={false} tickLine={false} tickFormatter={(value) => `₹${Math.round(value)}`} />
          <Tooltip contentStyle={{ background: "#0b121d", border: "1px solid #223047", fontSize: 11 }} formatter={(value) => inr(Number(value))} labelFormatter={(trade) => `Closed trade ${trade}`} />
          <Line dataKey="equity" name="Cumulative net P&L" type="monotone" stroke="#19d3ae" strokeWidth={2} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}