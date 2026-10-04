"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { inr } from "@/lib/utils";

export function PortfolioPnlChart({ holdings }: { holdings: { symbol: string; pnl: number }[] }) {
  const data = holdings.map((item) => ({ symbol: item.symbol, pnl: Number(item.pnl.toFixed(2)) }));
  if (!data.length) return <div className="grid h-56 place-items-center text-xs text-slate-600">Add holdings to see P&amp;L attribution.</div>;
  return (
    <div className="h-64 w-full" data-testid="portfolio-pnl-chart">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 10, right: 10, left: 16, bottom: 4 }}>
          <CartesianGrid stroke="#172335" vertical={false} />
          <XAxis dataKey="symbol" tick={{ fill: "#728197", fontSize: 10 }} axisLine={{ stroke: "#223047" }} tickLine={false} />
          <YAxis tick={{ fill: "#728197", fontSize: 10 }} axisLine={false} tickLine={false} tickFormatter={(v) => `${Math.round(v / 1000)}k`} />
          <Tooltip contentStyle={{ background: "#0b121d", border: "1px solid #223047", fontSize: 11 }} formatter={(value) => inr(Number(value))} />
          <Bar dataKey="pnl" fill="var(--accent-primary)" radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
