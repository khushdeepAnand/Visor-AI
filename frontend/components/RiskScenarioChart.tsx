"use client";

import { Line, LineChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { inr } from "@/lib/utils";

type MonteCarlo = { last_price: number; horizon_days: number; sample_paths: number[][]; terminal_percentiles: Record<string, number> };

function percentile(values: number[], p: number) {
  if (!values.length) return 0;
  const ordered = [...values].sort((a, b) => a - b);
  const index = Math.min(ordered.length - 1, Math.max(0, Math.round((ordered.length - 1) * p)));
  return ordered[index];
}

export function RiskScenarioChart({ monteCarlo }: { monteCarlo: MonteCarlo }) {
  const data = monteCarlo.sample_paths.map((values, index) => ({
    day: index + 1,
    p05: percentile(values, 0.05),
    p50: percentile(values, 0.5),
    p95: percentile(values, 0.95),
  }));
  return (
    <div className="h-72 w-full" data-testid="risk-scenario-chart">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 10, right: 12, bottom: 2, left: 18 }}>
          <CartesianGrid stroke="#172335" vertical={false} />
          <XAxis dataKey="day" tick={{ fill: "#728197", fontSize: 10 }} axisLine={{ stroke: "#223047" }} tickLine={false} />
          <YAxis tick={{ fill: "#728197", fontSize: 10 }} axisLine={false} tickLine={false} tickFormatter={(value) => `₹${Math.round(value)}`} />
          <Tooltip contentStyle={{ background: "#0b121d", border: "1px solid #223047", fontSize: 11 }} formatter={(value) => inr(Number(value))} labelFormatter={(day) => `Trading day ${day}`} />
          <Line dataKey="p05" name="5th pct" type="monotone" stroke="#ff5c67" strokeWidth={1} dot={false} />
          <Line dataKey="p50" name="Median" type="monotone" stroke="#19d3ae" strokeWidth={2} dot={false} />
          <Line dataKey="p95" name="95th pct" type="monotone" stroke="#28d17c" strokeWidth={1} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
