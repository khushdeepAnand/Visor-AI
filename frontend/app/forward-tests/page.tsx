"use client";

import { Suspense, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { FlaskConical, Play, Square, Trophy, Plus } from "lucide-react";
import { TerminalShell } from "@/components/TerminalShell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { api, formatApiError } from "@/lib/api";

/**
 * Forward tests (v11 Part J).
 *
 * A saved strategy is tracked forward: every end-of-session rule match since
 * the start date is recorded, and the scorecard is built from closed,
 * signal-to-signal pairs only. No order is ever placed — the backend returns
 * `order_placement: "never"` from every endpoint so the UI cannot mislabel it.
 */

type ForwardTest = {
  id: number;
  strategy_id: number;
  name: string;
  symbols: string[];
  status: string;
  started_at?: string;
  stopped_at?: string;
  last_evaluated_at?: string;
};

type ForwardTestList = { forward_tests: ForwardTest[]; order_placement?: string };

type Scorecard = {
  forward_test: ForwardTest;
  scorecard: {
    closed_trades: number;
    wins: number;
    losses: number;
    win_rate_pct?: number;
    avg_return_pct?: number;
    best_return_pct?: number;
    worst_return_pct?: number;
    max_drawdown_pct?: number;
    compounded_return_pct?: number;
    basis: string;
  };
  trades: Array<{ symbol: string; entry_at: string; exit_at: string; entry_price: number; exit_price: number; exit_reason?: string; return_pct: number }>;
  open_positions: Array<{ symbol: string; entry_at: string; entry_price: number }>;
  observed_signals: number;
  order_placement?: string;
  evidence: { basis: string };
  disclosures: string[];
};

type Evaluated = {
  forward_test: ForwardTest;
  evaluated_at?: string;
  new_signals: number;
  signals_considered: number;
  skipped?: number;
  order_placement?: string;
  disclosures: string[];
};

const WINDOWS = [
  ["1w", "1 week"],
  ["1mo", "1 month"],
  ["3mo", "3 months"],
  ["1y", "1 year"],
  ["5y", "5 years"],
] as const;

function isFeatureDisabled(error: unknown): boolean {
  return typeof error === "object" && error !== null && "code" in error && (error as { code?: string }).code === "feature_disabled";
}

export default function ForwardTestsPage() {
  return (
    <Suspense fallback={null}>
      <ForwardTestsContent />
    </Suspense>
  );
}

function ForwardTestsContent() {
  const queryClient = useQueryClient();
  const params = useSearchParams();
  const queryStrategyId = params.get("strategy");
  const [selectedStrategy, setSelectedStrategy] = useState(queryStrategyId ?? "");
  const [testName, setTestName] = useState("");
  const [symbols, setSymbols] = useState("");
  const [windowValue, setWindowValue] = useState("1y");
  const [error, setError] = useState("");
  const [activeScorecardId, setActiveScorecardId] = useState<number | null>(null);
  const [lastEvaluated, setLastEvaluated] = useState<Evaluated | null>(null);

  const listQuery = useQuery({
    queryKey: ["forward-tests"],
    queryFn: () => api<ForwardTestList>("/api/v1/forward-tests"),
    staleTime: 30_000,
  });

  const strategiesQuery = useQuery({
    queryKey: ["strategies-saved"],
    queryFn: () => api<{ strategies: Array<{ id: number; name: string; definition: { symbols?: string[] } }> }>("/api/v1/strategies"),
    staleTime: 60_000,
  });

  const tests = useMemo(() => listQuery.data?.forward_tests ?? [], [listQuery.data]);
  const strategies = useMemo(() => strategiesQuery.data?.strategies ?? [], [strategiesQuery.data]);
  const orderPlacement = listQuery.data?.order_placement ?? "never";

  const scorecardQuery = useQuery({
    queryKey: ["forward-scorecard", activeScorecardId],
    queryFn: () => api<Scorecard>(`/api/v1/forward-tests/${activeScorecardId}/scorecard`),
    enabled: activeScorecardId != null,
    staleTime: 15_000,
  });

  const startMutation = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<ForwardTest>("/api/v1/forward-tests", { method: "POST", body: JSON.stringify(body) }),
    onError: (err) => setError(formatApiError(err)),
    onSuccess: (test) => {
      setError("");
      setTestName("");
      setSymbols("");
      setActiveScorecardId(test.id);
      queryClient.invalidateQueries({ queryKey: ["forward-tests"] });
    },
  });

  const evaluateMutation = useMutation({
    mutationFn: (id: number) =>
      api<Evaluated>(`/api/v1/forward-tests/${id}/evaluate`, { method: "POST", body: JSON.stringify({ timeframe: "1D", window: windowValue }) }),
    onError: (err) => setError(formatApiError(err)),
    onSuccess: (evaluated) => {
      setError("");
      setLastEvaluated(evaluated);
      setActiveScorecardId(evaluated.forward_test.id);
      queryClient.invalidateQueries({ queryKey: ["forward-tests"] });
      queryClient.invalidateQueries({ queryKey: ["forward-scorecard"] });
    },
  });

  const stopMutation = useMutation({
    mutationFn: (id: number) => api<{ id: number; status: string }>(`/api/v1/forward-tests/${id}/stop`, { method: "POST" }),
    onError: (err) => setError(formatApiError(err)),
    onSuccess: () => {
      setError("");
      queryClient.invalidateQueries({ queryKey: ["forward-tests"] });
      queryClient.invalidateQueries({ queryKey: ["forward-scorecard"] });
    },
  });

  if (isFeatureDisabled(listQuery.error) || isFeatureDisabled(strategiesQuery.error)) {
    return (
      <TerminalShell>
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-3 py-4 sm:px-4">
          <header>
            <h1 className="display-font text-lg font-semibold text-slate-100">Forward tests</h1>
          </header>
          <Card>
            <CardContent className="space-y-2 py-4">
              <p role="status" className="text-sm text-slate-300">
                Forward-test tracking is currently turned off by an operator feature flag.
              </p>
              <p className="text-xs text-slate-500">
                An administrator can enable the <code>forward_test_tracking</code> flag under Admin → Operations. Forward tests never place an order.
              </p>
              {listQuery.isError && <p className="text-xs text-loss">{formatApiError(listQuery.error)}</p>}
            </CardContent>
          </Card>
        </div>
      </TerminalShell>
    );
  }

  const activeTest = (id: number) => tests.find((test) => test.id === id);

  return (
    <TerminalShell>
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 px-3 py-4 sm:px-4">
        <header className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h1 className="display-font text-lg font-semibold text-slate-100">Forward tests</h1>
            <p className="text-xs text-slate-500">
              Record how a saved strategy behaves on live stored history after you started tracking it. Paper observation only.
            </p>
          </div>
          <span className="rounded bg-slate-800 px-2 py-1 text-[10px] uppercase tracking-wide text-slate-400">
            {orderPlacement === "never" ? "Never places an order" : "Paper only"}
          </span>
        </header>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Plus aria-hidden="true" size={14} /> Start a forward test
            </CardTitle>
            <p className="text-xs text-slate-500">Signals are recorded at each end-of-session rule match from the start date onward.</p>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <Select aria-label="Strategy to forward test" value={selectedStrategy} onChange={(event) => setSelectedStrategy(event.target.value)}>
                <option value="">Choose a saved strategy…</option>
                {strategies.map((strategy) => (
                  <option key={strategy.id} value={String(strategy.id)}>{strategy.name} ({(strategy.definition.symbols ?? []).join(", ")})</option>
                ))}
              </Select>
              <Input aria-label="Test name" placeholder="Optional name" className="w-52" value={testName} onChange={(event) => setTestName(event.target.value)} />
              <Input aria-label="Symbols, comma separated" placeholder="Symbols (defaults to strategy)" className="w-64" value={symbols} onChange={(event) => setSymbols(event.target.value)} />
              <Select aria-label="Evaluation window" value={windowValue} onChange={(event) => setWindowValue(event.target.value)}>
                {WINDOWS.map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </Select>
              <Button
                disabled={startMutation.isPending || !selectedStrategy}
                onClick={() => {
                  const body: Record<string, unknown> = { strategy_id: Number(selectedStrategy) };
                  if (testName.trim()) body.name = testName.trim();
                  const list = symbols.split(",").map((item) => item.trim().toUpperCase()).filter(Boolean);
                  if (list.length) body.symbols = Array.from(new Set(list));
                  startMutation.mutate(body);
                }}
              >
                <FlaskConical aria-hidden="true" size={14} /> {startMutation.isPending ? "Starting…" : "Start tracking"}
              </Button>
            </div>
            {error && <p role="alert" className="text-xs text-loss">{error}</p>}
          </CardContent>
        </Card>

        {lastEvaluated && (
          <Card>
            <CardHeader>
              <CardTitle>Last evaluation</CardTitle>
            </CardHeader>
            <CardContent className="text-xs text-slate-400">
              <p>
                {lastEvaluated.forward_test.name}: {lastEvaluated.new_signals} new signal{lastEvaluated.new_signals === 1 ? "" : "s"} recorded (
                {lastEvaluated.signals_considered} considered{lastEvaluated.skipped ? `, ${lastEvaluated.skipped} skipped` : ""}).
              </p>
            </CardContent>
          </Card>
        )}

        {tests.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>Running tests</CardTitle>
            </CardHeader>
            <CardContent className="overflow-x-auto p-0">
              <table className="w-full min-w-[640px] text-left text-xs">
                <thead className="border-b border-slate-800 text-[10px] uppercase tracking-wider text-slate-500">
                  <tr>
                    <th className="px-4 py-2">Name</th>
                    <th className="px-4 py-2">Symbols</th>
                    <th className="px-4 py-2">Status</th>
                    <th className="px-4 py-2">Started</th>
                    <th className="px-4 py-2">Last evaluated</th>
                    <th className="px-4 py-2 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {tests.map((test) => (
                    <tr key={test.id} className="border-b border-slate-900/80">
                      <td className="px-4 py-2 text-slate-100">{test.name}</td>
                      <td className="px-4 py-2 text-slate-400">{test.symbols.join(", ")}</td>
                      <td className="px-4 py-2">
                        <span className={`rounded px-1.5 py-0.5 text-[10px] uppercase ${test.status === "active" ? "bg-accent/10 text-accent" : "bg-slate-800 text-slate-500"}`}>
                          {test.status}
                        </span>
                      </td>
                      <td className="px-4 py-2 text-slate-500">{test.started_at ?? "—"}</td>
                      <td className="px-4 py-2 text-slate-500">{test.last_evaluated_at ?? "—"}</td>
                      <td className="px-4 py-2">
                        <div className="flex justify-end gap-1">
                          {test.status === "active" && (
                            <>
                              <Button
                                variant="ghost"
                                disabled={evaluateMutation.isPending}
                                onClick={() => evaluateMutation.mutate(test.id)}
                                aria-label={`Evaluate ${test.name}`}
                              >
                                <Play aria-hidden="true" size={13} /> Record matches
                              </Button>
                              <Button variant="danger" onClick={() => stopMutation.mutate(test.id)} aria-label={`Stop ${test.name}`}>
                                <Square aria-hidden="true" size={13} /> Stop
                              </Button>
                            </>
                          )}
                          <Button variant="ghost" onClick={() => setActiveScorecardId(test.id)} aria-label={`Scorecard for ${test.name}`}>
                            <Trophy aria-hidden="true" size={13} /> Scorecard
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </CardContent>
          </Card>
        )}

        {scorecardQuery.isError && <p role="alert" className="text-xs text-loss">{formatApiError(scorecardQuery.error)}</p>}

        {scorecardQuery.data && activeScorecardId != null && (
          <Card>
            <CardHeader>
              <CardTitle>
                Scorecard: {scorecardQuery.data.forward_test.name}
              </CardTitle>
              <span className="text-[10px] text-slate-500">
                {scorecardQuery.data.observed_signals} observed signals{scorecardQuery.data.order_placement === "never" ? " · order placement: never" : ""}
              </span>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-slate-800 bg-slate-800 text-center text-xs sm:grid-cols-4">
                <MetricCell label="Closed trades" value={String(scorecardQuery.data.scorecard.closed_trades)} />
                <MetricCell label="Win rate" value={scorecardQuery.data.scorecard.win_rate_pct == null ? "—" : `${scorecardQuery.data.scorecard.win_rate_pct}%`} />
                <MetricCell label="Compounded return" value={scorecardQuery.data.scorecard.compounded_return_pct == null ? "—" : `${scorecardQuery.data.scorecard.compounded_return_pct}%`} />
                <MetricCell label="Max drawdown" value={scorecardQuery.data.scorecard.max_drawdown_pct == null ? "—" : `${scorecardQuery.data.scorecard.max_drawdown_pct}%`} />
                <MetricCell label="Avg return" value={scorecardQuery.data.scorecard.avg_return_pct == null ? "—" : `${scorecardQuery.data.scorecard.avg_return_pct}%`} />
                <MetricCell label="Best / worst" value={scorecardQuery.data.scorecard.best_return_pct == null ? "—" : `${scorecardQuery.data.scorecard.best_return_pct}% / ${scorecardQuery.data.scorecard.worst_return_pct}%`} />
                <MetricCell label="Wins / losses" value={`${scorecardQuery.data.scorecard.wins} / ${scorecardQuery.data.scorecard.losses}`} />
                <MetricCell label="Open positions" value={String(scorecardQuery.data.open_positions.length)} />
              </div>

              <p className="text-[10px] text-slate-500">{scorecardQuery.data.scorecard.basis}</p>

              {scorecardQuery.data.open_positions.map((position) => (
                <p key={`${position.symbol}-${position.entry_at}`} className="text-[10px] text-slate-500">
                  Open position in {position.symbol} from {position.entry_at} (₹{position.entry_price}) — recorded, not held, and not yet closed, so it is excluded from the summary.
                </p>
              ))}

              {scorecardQuery.data.trades.length > 0 && (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[560px] text-left text-xs">
                    <thead className="border-b border-slate-800 text-[10px] uppercase tracking-wider text-slate-500">
                      <tr>
                        <th className="px-3 py-1">Symbol</th>
                        <th className="px-3 py-1">Entry</th>
                        <th className="px-3 py-1">Exit</th>
                        <th className="px-3 py-1">Return</th>
                        <th className="px-3 py-1">Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {scorecardQuery.data.trades.map((trade, index) => (
                        <tr key={`${trade.symbol}-${trade.entry_at}-${index}`} className="border-b border-slate-900/80">
                          <td className="px-3 py-1 text-slate-100">{trade.symbol}</td>
                          <td className="px-3 py-1 text-slate-500">{trade.entry_at}</td>
                          <td className="px-3 py-1 text-slate-500">{trade.exit_at}</td>
                          <td className={`px-3 py-1 ${trade.return_pct >= 0 ? "text-gain" : "text-loss"}`}>{trade.return_pct.toFixed(2)}%</td>
                          <td className="px-3 py-1 text-slate-500">{trade.exit_reason ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {scorecardQuery.data.trades.length === 0 && (
                <p className="text-xs text-slate-500">No closed signal-to-signal pairs yet.</p>
              )}

              <ul className="space-y-1 border-t border-slate-800 pt-2 text-[10px] leading-4 text-slate-500">
                <li>{scorecardQuery.data.evidence.basis}</li>
                {scorecardQuery.data.disclosures.map((line) => <li key={line}>{line}</li>)}
              </ul>
            </CardContent>
          </Card>
        )}

        {tests.length === 0 && (
          <Card>
            <CardContent className="py-4 text-xs text-slate-500">
              No forward tests yet. Save a strategy first, then start tracking it here.
            </CardContent>
          </Card>
        )}

        {activeScorecardId != null && !scorecardQuery.data && scorecardQuery.isPending && (
          <p role="status" className="text-xs text-slate-500">Loading scorecard…</p>
        )}

        <p className="text-[10px] text-slate-600">
          Forward-test records are signal observations, not holdings. This application has no live broker order path for these rules (or any others).
        </p>
      </div>
    </TerminalShell>
  );
}

function MetricCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-terminal-900 px-3 py-2">
      <p className="text-[9px] uppercase tracking-wider text-slate-500">{label}</p>
      <p className="mt-0.5 font-medium text-slate-200">{value}</p>
    </div>
  );
}
