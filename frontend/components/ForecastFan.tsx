"use client";

import { motion, useReducedMotion } from "motion/react";

type FanPoint = { sessions: number; low: number; median: number; high: number; nominal: number };

export function ForecastFan({ current, points }: { current: number; points: FanPoint[] }) {
  const reduceMotion = useReducedMotion();
  const valid = points.filter(p => [p.sessions, p.low, p.median, p.high, p.nominal].every(Number.isFinite)
    && p.sessions > 0 && p.low <= p.median && p.median <= p.high && p.nominal > 0 && p.nominal < 1).sort((a, b) => a.sessions - b.sessions);
  if (!Number.isFinite(current) || !valid.length) return <p>No verified horizon bounds available for an uncertainty fan.</p>;
  const min = Math.min(current, ...valid.map(p => p.low));
  const max = Math.max(current, ...valid.map(p => p.high));
  const spread = Math.max(max - min, 1);
  const x = (sessions: number) => 30 + sessions / valid.at(-1)!.sessions * 520;
  const y = (price: number) => 160 - (price - min) / spread * 130;
  const polygon = [[x(0), y(current)], ...valid.map(p => [x(p.sessions), y(p.high)]),
    ...valid.slice().reverse().map(p => [x(p.sessions), y(p.low)])].map(p => p.join(",")).join(" ");
  const medians = [[x(0), y(current)], ...valid.map(p => [x(p.sessions), y(p.median)])].map(p => p.join(",")).join(" ");
  return <figure className="mt-4 rounded-lg border border-slate-700 p-3">
    <figcaption className="text-sm font-semibold">Uncertainty across sessions</figcaption>
    <motion.svg viewBox="0 0 580 195" role="img" aria-label="Forecast uncertainty fan with published lower, median and upper bounds" className="mt-2 w-full text-accent" initial={reduceMotion ? false : { opacity: 0, scaleX: .96 }} animate={{ opacity: 1, scaleX: 1 }} transition={{ duration: reduceMotion ? 0 : .35 }}>
      <polygon points={polygon} fill="currentColor" fillOpacity=".18" stroke="currentColor" strokeWidth="1" />
      <polyline points={medians} fill="none" stroke="currentColor" strokeWidth="2" strokeDasharray="5 4" />
      <circle cx={x(0)} cy={y(current)} r="4" fill="currentColor" />
      <text x="30" y="185" fill="currentColor" fontSize="12">Today</text>
      {valid.map(p => <text key={p.sessions} x={x(p.sessions)} y="185" textAnchor="end" fill="currentColor" fontSize="12">{p.sessions} sessions</text>)}
    </motion.svg>
    <p className="mt-2 text-xs text-slate-400">Connecting lines are visual guides between independent forecasts. Nominal coverage is a target, not a chance of profit.</p>
    <details className="mt-2 text-xs"><summary className="min-h-11 cursor-pointer py-3">Published bounds</summary><ul>{valid.map(p => <li key={p.sessions}>{p.sessions} sessions: ₹{p.low.toFixed(2)}–₹{p.high.toFixed(2)}; median ₹{p.median.toFixed(2)}; nominal {Math.round(p.nominal * 100)}%.</li>)}</ul></details>
  </figure>;
}
