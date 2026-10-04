"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Plus, Play, Save, TestTube, Trash2, Workflow, X } from "lucide-react";
import Link from "next/link";
import { TerminalShell } from "@/components/TerminalShell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { api, ApiError, formatApiError } from "@/lib/api";
import { StrategyEquityChart, EquityPoint } from "@/components/StrategyEquityChart";

/**
 * No-code strategy builder (v11 Part J).
 *
 * Everything the builder offers is driven by `GET /api/v1/strategies/metadata`:
 * metric names, comparators and starter rules come from the backend so the UI
 * cannot drift from what the rule engine actually supports. Rules are paper
 * only — the backend refuses to route them anywhere else.
 */

type MetricMeta = { name: string; label: string; unit: string; sessions_required: number; basis: string };
type OperatorMeta = { name: string; label: string; arity: number };
type StarterStrategy = { name: string; entry: unknown; exit?: unknown; symbols: string[]; quantity: number; stop_loss_pct?: number; target_pct?: number };

type StrategyMetadata = {
  metrics: MetricMeta[];
  operators: OperatorMeta[];
  starters: StarterStrategy[];
  execution?: string;
};

type ConditionDraft = { metric: string; operator: string; value: string; value2: string; compare_metric: string };
type GroupDraft = { join: "and" | "or"; conditions: ConditionDraft[] };

type SavedStrategy = {
  id: number;
  name: string;
  definition: { symbols?: string[]; entry?: unknown; exit?: unknown; quantity?: number; stop_loss_pct?: number; target_pct?: number };
  created_at?: string;
};

type RunSignal = { at: string; action: "BUY" | "SELL"; intent?: string; price: number; reason?: string };
type RunResult = {
  symbol: string;
  evaluated?: boolean;
  reason?: string;
  sessions_available?: number;
  signals?: RunSignal[];
  latest_price?: number;
};
type RunResponse = {
  strategy: { name?: string; execution?: string };
  coverage: { requested: number; evaluated: number; excluded: Array<{ symbol: string; reason: string }> };
  results: RunResult[];
  evidence: { basis: string };
  disclosures: string[];
  request?: { timeframe?: string; window?: string };
};

type BacktestTrade = { entry_at: string; exit_at: string; exit_reason?: string; quantity: number; net_pnl: number; net_return_pct: number };
type BacktestResponse = {
  symbol: string;
  strategy: { name?: string; direction?: string };
  assumptions: { quantity: number; spread_bps: number; slippage_bps: number; fill_basis: string; positions?: string };
  sessions_available?: number;
  summary: {
    trades: number;
    wins: number;
    losses: number;
    win_rate_pct?: number;
    gross_pnl: number;
    costs: number;
    net_pnl: number;
    avg_net_return_pct?: number;
    best_net_pnl?: number;
    worst_net_pnl?: number;
    max_drawdown_pct?: number;
  };
  trades: BacktestTrade[];
  open_position?: { entry_at: string; entry_price: number; state: string } | null;
  evidence: { basis: string };
  disclosures: string[];
  request?: { timeframe?: string; window?: string };
};

const WINDOWS = [
  ["1w", "1 week"],
  ["1mo", "1 month"],
  ["3mo", "3 months"],
  ["1y", "1 year"],
  ["5y", "5 years"],
] as const;

function isFeatureDisabled(error: unknown): boolean {
  return error instanceof ApiError && error.code === "feature_disabled";
}

function parseOptional(text: string): number | undefined {
  const value = Number(text);
  return text.trim() === "" || Number.isNaN(value) || value <= 0 ? undefined : value;
}

function parseSymbols(text: string): string[] {
  const list = text.split(",").map((item) => item.trim().toUpperCase()).filter(Boolean);
  return Array.from(new Set(list));
}

function emptyCondition(): ConditionDraft {
  return { metric: "close", operator: "gt", value: "0", value2: "0", compare_metric: "" };
}

function starterToGroups(entry: unknown): GroupDraft[] {
  if (Array.isArray(entry)) {
    return entry.map((group) => {
      const conditions: ConditionDraft[] = Array.isArray(group?.conditions)
        ? (group.conditions as Array<Record<string, unknown>>).map((item) => ({
            metric: String(item?.metric ?? "close"),
            operator: String(item?.operator ?? "gt"),
            value: item?.values ? String((item.values as number[])[0] ?? "") : String(item?.value ?? ""),
            value2: item?.values ? String((item.values as number[])[1] ?? "") : "",
            compare_metric: String(item?.compare_metric ?? ""),
          }))
        : [emptyCondition()];
      return { join: String(group?.join ?? "and") === "or" ? "or" : "and", conditions };
    });
  }
  return [{ join: "and", conditions: [emptyCondition()] }];
}

export default function StrategiesPage() {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [symbols, setSymbols] = useState("RELIANCE, TCS");
  const [quantity, setQuantity] = useState("1");
  const [stopLoss, setStopLoss] = useState("");
  const [target, setTarget] = useState("");
  const [entryGroups, setEntryGroups] = useState<GroupDraft[]>([{ join: "and", conditions: [emptyCondition()] }]);
  const [exitGroups, setExitGroups] = useState<GroupDraft[]>([{ join: "and", conditions: [emptyCondition()] }]);
  const [useExit, setUseExit] = useState(false);
  const [windowValue, setWindowValue] = useState("1y");
  const [backtestSymbol, setBacktestSymbol] = useState("RELIANCE");
  const [error, setError] = useState("");
  const [runResult, setRunResult] = useState<RunResponse | null>(null);
  const [backtestResult, setBacktestResult] = useState<BacktestResponse | null>(null);

  const metadataQuery = useQuery({
    queryKey: ["strategies-metadata"],
    queryFn: () => api<StrategyMetadata>("/api/v1/strategies/metadata"),
    staleTime: 10 * 60_000,
  });

  const savedQuery = useQuery({
    queryKey: ["strategies-saved"],
    queryFn: () => api<{ strategies: SavedStrategy[] }>("/api/v1/strategies"),
    staleTime: 60_000,
  });

  const metrics = useMemo(() => metadataQuery.data?.metrics ?? [], [metadataQuery.data]);
  const operators = useMemo(() => metadataQuery.data?.operators ?? [], [metadataQuery.data]);
  const starters = useMemo(() => metadataQuery.data?.starters ?? [], [metadataQuery.data]);

  const metadataDisabled = isFeatureDisabled(metadataQuery.error);
  const savedDisabled = isFeatureDisabled(savedQuery.error);

  function operatorMeta(name: string): OperatorMeta | undefined {
    return operators.find((operator) => operator.name === name);
  }

  function conditionForApi(condition: ConditionDraft): Record<string, unknown> {
    const op = operatorMeta(condition.operator);
    if (op?.arity === 2) {
      return { metric: condition.metric, operator: condition.operator, values: [Number(condition.value) || 0, Number(condition.value2) || 0] };
    }
    if (condition.compare_metric) {
      return { metric: condition.metric, operator: condition.operator, compare_metric: condition.compare_metric };
    }
    return { metric: condition.metric, operator: condition.operator, value: Number(condition.value) || 0 };
  }

  function definitionBody(): Record<string, unknown> {
    const body: Record<string, unknown> = {
      name: name.trim() || "Untitled strategy",
      symbols: parseSymbols(symbols),
      quantity: Number(quantity) || 1,
      entry: entryGroups.map((group) => ({ join: group.join, conditions: group.conditions.map(conditionForApi) })),
    };
    if (parseOptional(stopLoss)) body.stop_loss_pct = parseOptional(stopLoss);
    if (parseOptional(target)) body.target_pct = parseOptional(target);
    if (useExit) body.exit = exitGroups.map((group) => ({ join: group.join, conditions: group.conditions.map(conditionForApi) }));
    return body;
  }

  function groupsHaveConditions(groups: GroupDraft[]): boolean {
    return groups.some((group) => group.conditions.length > 0);
  }

  const saveMutation = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<SavedStrategy>("/api/v1/strategies", { method: "POST", body: JSON.stringify(body) }),
    onError: (err) => {
      if (isFeatureDisabled(err)) setRunResult(null);
      setError(formatApiError(err));
    },
    onSuccess: () => {
      setError("");
      queryClient.invalidateQueries({ queryKey: ["strategies-saved"] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api(`/api/v1/strategies/${id}`, { method: "DELETE" }),
    onError: (err) => setError(formatApiError(err)),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["strategies-saved"] }),
  });

  const runMutation = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<RunResponse>("/api/v1/strategies/run", { method: "POST", body: JSON.stringify(body) }),
    onError: (err) => {
      setBacktestResult(null);
      setError(formatApiError(err));
    },
    onSuccess: (data) => {
      setError("");
      setRunResult(data);
    },
  });

  const backtestMutation = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<BacktestResponse>("/api/v1/strategies/backtest", { method: "POST", body: JSON.stringify(body) }),
    onError: (err) => {
      setRunResult(null);
      setError(formatApiError(err));
    },
    onSuccess: (data) => {
      setError("");
      setBacktestResult(data);
    },
  });

  function loadStarter(starter: StarterStrategy) {
    setName(starter.name);
    setSymbols(starter.symbols.join(", "));
    setQuantity(String(starter.quantity ?? 1));
    setStopLoss(starter.stop_loss_pct ? String(starter.stop_loss_pct) : "");
    setTarget(starter.target_pct ? String(starter.target_pct) : "");
    setEntryGroups(starterToGroups(starter.entry));
    if (starter.exit) {
      setUseExit(true);
      setExitGroups(starterToGroups(starter.exit));
    } else {
      setUseExit(false);
    }
    setError("");
    setRunResult(null);
    setBacktestResult(null);
  }

  function loadSaved(strategy: SavedStrategy) {
    setName(strategy.name);
    setSymbols((strategy.definition?.symbols ?? []).join(", "));
    setQuantity(String(strategy.definition?.quantity ?? 1));
    setStopLoss(strategy.definition?.stop_loss_pct ? String(strategy.definition.stop_loss_pct) : "");
    setTarget(strategy.definition?.target_pct ? String(strategy.definition.target_pct) : "");
    setEntryGroups(starterToGroups(strategy.definition?.entry));
    if (strategy.definition?.exit) {
      setUseExit(true);
      setExitGroups(starterToGroups(strategy.definition.exit));
    } else {
      setUseExit(false);
    }
    setError("");
    setRunResult(null);
    setBacktestResult(null);
  }

  function updateGroup(setter: React.Dispatch<React.SetStateAction<GroupDraft[]>>, index: number, patch: Partial<GroupDraft>) {
    setter((current) => current.map((group, position) => (position === index ? { ...group, ...patch } : group)));
  }

  function updateCondition(setter: React.Dispatch<React.SetStateAction<GroupDraft[]>>, groupIndex: number, conditionIndex: number, patch: Partial<ConditionDraft>) {
    setter((current) =>
      current.map((group, gIndex) =>
        gIndex === groupIndex
          ? { ...group, conditions: group.conditions.map((condition, cIndex) => (cIndex === conditionIndex ? { ...condition, ...patch } : condition)) }
          : group,
      ),
    );
  }

  const crossOperators = operators.filter((operator) => operator.name === "cross_above" || operator.name === "cross_below");
  const equityPoints: EquityPoint[] = useMemo(() => {
    if (!backtestResult) return [];
    let running = 0;
    return backtestResult.trades.map((trade, index) => {
      running += trade.net_pnl ?? 0;
      return { trade: index + 1, equity: running };
    });
  }, [backtestResult]);

  if (metadataDisabled || savedDisabled) {
    return (
      <TerminalShell>
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-3 py-4 sm:px-4">
          <header>
            <h1 className="display-font text-lg font-semibold text-slate-100">Strategies</h1>
          </header>
          <Card>
            <CardContent className="space-y-2 py-4">
              <p role="status" className="text-sm text-slate-300">
                The no-code strategy builder is currently turned off by an operator feature flag.
              </p>
              <p className="text-xs text-slate-500">
                An administrator can enable the <code>strategy_builder</code> flag under Admin → Operations. Builder rules are paper only.
              </p>
              {metadataQuery.isError && <p className="text-xs text-loss">{formatApiError(metadataQuery.error)}</p>}
            </CardContent>
          </Card>
        </div>
      </TerminalShell>
    );
  }

  return (
    <TerminalShell>
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 px-3 py-4 sm:px-4">
        <header className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h1 className="display-font text-lg font-semibold text-slate-100">Strategies</h1>
            <p className="text-xs text-slate-500">
              No-code rules over realized OHLCV history. Paper only — no broker order path exists in this application.
            </p>
          </div>
          {metadataQuery.data?.execution === "paper_only" && (
            <span className="rounded bg-slate-800 px-2 py-1 text-[10px] uppercase tracking-wide text-slate-400">Paper only</span>
          )}
        </header>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Workflow aria-hidden="true" size={14} /> Rule builder
            </CardTitle>
            {metadataQuery.isError && <p role="alert" className="text-xs text-loss">{formatApiError(metadataQuery.error)}</p>}
            {starters.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {starters.map((starter) => (
                  <Button key={starter.name} variant="ghost" className="text-[11px]" onClick={() => loadStarter(starter)}>
                    Load: {starter.name}
                  </Button>
                ))}
              </div>
            )}
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <Input aria-label="Strategy name" placeholder="Strategy name" className="w-56" value={name} onChange={(event) => setName(event.target.value)} />
              <Input aria-label="Symbols, comma separated" placeholder="RELIANCE, TCS" className="w-56" value={symbols} onChange={(event) => setSymbols(event.target.value)} />
              <Input aria-label="Quantity per trade" type="number" inputMode="decimal" className="w-24" title="Quantity per trade" value={quantity} onChange={(event) => setQuantity(event.target.value)} />
              <label className="flex items-center gap-1 text-xs text-slate-400" title="In percent">
                <span>Stop loss %</span>
                <Input aria-label="Stop loss percent" type="number" inputMode="decimal" className="w-16" value={stopLoss} onChange={(event) => setStopLoss(event.target.value)} />
              </label>
              <label className="flex items-center gap-1 text-xs text-slate-400" title="In percent">
                <span>Target %</span>
                <Input aria-label="Target percent" type="number" inputMode="decimal" className="w-16" value={target} onChange={(event) => setTarget(event.target.value)} />
              </label>
            </div>

            <div className="space-y-3 rounded-lg border border-slate-800 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="text-xs font-medium uppercase tracking-wide text-slate-400">Entry rules</h2>
                <div className="flex items-center gap-2">
                  <Button variant="ghost" onClick={() => setEntryGroups((current) => [...current, { join: "and", conditions: [emptyCondition()] }])}>
                    <Plus aria-hidden="true" size={14} /> Group
                  </Button>
                  <label className="flex items-center gap-1 text-xs text-slate-400">
                    <input type="checkbox" checked={useExit} onChange={(event) => setUseExit(event.target.checked)} />
                    Use exit rules
                  </label>
                </div>
              </div>

              {entryGroups.map((group, gIndex) => (
                <BuildGroup
                  key={`entry-${gIndex}`}
                  group={group}
                  metrics={metrics}
                  operators={operators}
                  crossOperators={crossOperators}
                  accentId={`entry-${gIndex}`}
                  onJoin={(join) => updateGroup(setEntryGroups, gIndex, { join })}
                  onCondition={(cIndex, patch) => updateCondition(setEntryGroups, gIndex, cIndex, patch)}
                  onAddCondition={() =>
                    setEntryGroups((current) =>
                      current.map((item, position) =>
                        position === gIndex ? { ...item, conditions: [...item.conditions, emptyCondition()] } : item,
                      ),
                    )
                  }
                  onRemoveCondition={(cIndex) =>
                    setEntryGroups((current) =>
                      current.map((item, position) =>
                        position === gIndex ? { ...item, conditions: item.conditions.filter((_, i) => i !== cIndex) } : item,
                      ),
                    )
                  }
                  onRemoveGroup={() => setEntryGroups((current) => current.filter((_, i) => i !== gIndex))}
                />
              ))}
            </div>

            {useExit && (
              <div className="space-y-3 rounded-lg border border-slate-800 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h2 className="text-xs font-medium uppercase tracking-wide text-slate-400">Exit rules</h2>
                  <Button variant="ghost" onClick={() => setExitGroups((current) => [...current, { join: "and", conditions: [emptyCondition()] }])}>
                    <Plus aria-hidden="true" size={14} /> Group
                  </Button>
                </div>
                {exitGroups.map((group, gIndex) => (
                  <BuildGroup
                    key={`exit-${gIndex}`}
                    group={group}
                    metrics={metrics}
                    operators={operators}
                    crossOperators={crossOperators}
                    accentId={`exit-${gIndex}`}
                    onJoin={(join) => updateGroup(setExitGroups, gIndex, { join })}
                    onCondition={(cIndex, patch) => updateCondition(setExitGroups, gIndex, cIndex, patch)}
                    onAddCondition={() =>
                      setExitGroups((current) =>
                        current.map((item, position) =>
                          position === gIndex ? { ...item, conditions: [...item.conditions, emptyCondition()] } : item,
                        ),
                      )
                    }
                    onRemoveCondition={(cIndex) =>
                      setExitGroups((current) =>
                        current.map((item, position) =>
                          position === gIndex ? { ...item, conditions: item.conditions.filter((_, i) => i !== cIndex) } : item,
                        ),
                      )
                    }
                    onRemoveGroup={() => setExitGroups((current) => current.filter((_, i) => i !== gIndex))}
                  />
                ))}
              </div>
            )}

            {error && <p role="alert" className="text-xs text-loss">{error}</p>}

            <div className="flex flex-wrap items-center gap-2 border-t border-slate-800 pt-3">
              {!groupsHaveConditions(entryGroups) && (
                <p className="text-xs text-loss">Entry rules need at least one condition.</p>
              )}
              <Button disabled={saveMutation.isPending || !groupsHaveConditions(entryGroups)} onClick={() => saveMutation.mutate(definitionBody())}>
                <Save aria-hidden="true" size={14} /> {saveMutation.isPending ? "Saving…" : "Save strategy"}
              </Button>
              <Button
                onClick={() => runMutation.mutate(definitionBody())}
                disabled={runMutation.isPending || !groupsHaveConditions(entryGroups) || !parseSymbols(symbols).length}
              >
                <Play aria-hidden="true" size={14} /> {runMutation.isPending ? "Running…" : "Run on history"}
              </Button>
            </div>

            <div className="flex flex-wrap items-center gap-2 border-t border-slate-800 pt-3">
              <span className="text-xs text-slate-400">Backtest one symbol</span>
              <Input aria-label="Backtest symbol" className="w-28" value={backtestSymbol} onChange={(event) => setBacktestSymbol(event.target.value.toUpperCase())} />
              <Select aria-label="Backtest window" value={windowValue} onChange={(event) => setWindowValue(event.target.value)}>
                {WINDOWS.map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </Select>
              <Button
                onClick={() => backtestMutation.mutate({ ...definitionBody(), symbol: backtestSymbol.toUpperCase(), window: windowValue, timeframe: "1D" })}
                disabled={backtestMutation.isPending || !groupsHaveConditions(entryGroups)}
              >
                <TestTube aria-hidden="true" size={14} /> {backtestMutation.isPending ? "Backtesting…" : "Backtest"}
              </Button>
            </div>
          </CardContent>
        </Card>

        {runResult && (
          <Card>
            <CardHeader>
              <CardTitle>Signals on history</CardTitle>
              <span className="text-[10px] text-slate-500">
                {runResult.coverage.evaluated} of {runResult.coverage.requested} evaluated{runResult.request?.window ? ` · ${runResult.request.window}` : ""}
              </span>
            </CardHeader>
            <CardContent className="space-y-3">
              {runResult.results.length === 0 && <p className="text-xs text-slate-500">No symbol produced a rule match.</p>}
              {runResult.results.map((result) => (
                <div key={result.symbol} className="rounded-lg border border-slate-800 p-3">
                  <span className="text-sm font-medium text-slate-100">{result.symbol}</span>
                  {result.reason && (
                    <span className="ml-2 text-[10px] text-slate-500">{result.reason}{result.sessions_available != null ? ` · ${result.sessions_available} sessions` : ""}</span>
                  )}
                  {result.signals && result.signals.length > 0 && (
                    <div className="mt-2 overflow-x-auto">
                      <table className="w-full min-w-[420px] text-left text-xs">
                        <thead className="border-b border-slate-800 text-[10px] uppercase tracking-wider text-slate-500">
                          <tr>
                            <th className="px-3 py-1">At</th>
                            <th className="px-3 py-1">Action</th>
                            <th className="px-3 py-1">Price</th>
                            <th className="px-3 py-1">Reason</th>
                          </tr>
                        </thead>
                        <tbody>
                          {result.signals.map((signal, index) => (
                            <tr key={`${result.symbol}-${signal.at}-${index}`} className="border-b border-slate-900/80">
                              <td className="px-3 py-1 text-slate-500">{signal.at}</td>
                              <td className={`px-3 py-1 font-medium ${signal.action === "BUY" ? "text-gain" : "text-loss"}`}>{signal.action}</td>
                              <td className="px-3 py-1 text-slate-300">{signal.price.toFixed(2)}</td>
                              <td className="px-3 py-1 text-slate-500">{signal.reason ?? "—"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              ))}
              {runResult.coverage.excluded.length > 0 && (
                <p className="text-[10px] leading-4 text-slate-500">
                  Excluded: {runResult.coverage.excluded.map((row) => `${row.symbol} (${row.reason})`).join("; ")}
                </p>
              )}
              <ul className="space-y-1 border-t border-slate-800 pt-2 text-[10px] leading-4 text-slate-500">
                <li>{runResult.evidence.basis}</li>
                {runResult.disclosures.map((line) => <li key={line}>{line}</li>)}
              </ul>
            </CardContent>
          </Card>
        )}

        {backtestResult && (
          <Card>
            <CardHeader>
              <CardTitle>
                Backtest: {backtestResult.symbol}
                {backtestResult.strategy?.name ? ` · ${backtestResult.strategy.name}` : ""}
              </CardTitle>
              <span className="text-[10px] text-slate-500">
                {backtestResult.summary.trades} closed trades{backtestResult.request?.window ? ` · ${backtestResult.request.window}` : ""}
              </span>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-slate-800 bg-slate-800 text-center text-xs sm:grid-cols-4">
                <MetricCell label="Win rate" value={backtestResult.summary.win_rate_pct == null ? "—" : `${backtestResult.summary.win_rate_pct}%`} />
                <MetricCell label="Net P&L" value={`₹${backtestResult.summary.net_pnl.toFixed(2)}`} />
                <MetricCell label="Costs" value={`₹${backtestResult.summary.costs.toFixed(2)}`} />
                <MetricCell label="Max drawdown" value={backtestResult.summary.max_drawdown_pct == null ? "—" : `${backtestResult.summary.max_drawdown_pct}%`} />
                <MetricCell label="Best / worst" value={`₹${backtestResult.summary.best_net_pnl?.toFixed(2)} / ₹${backtestResult.summary.worst_net_pnl?.toFixed(2)}`} />
                <MetricCell label="Avg return" value={backtestResult.summary.avg_net_return_pct == null ? "—" : `${backtestResult.summary.avg_net_return_pct}%`} />
                <MetricCell label="Wins / losses" value={`${backtestResult.summary.wins} / ${backtestResult.summary.losses}`} />
                <MetricCell label="Spread / slippage" value={`${backtestResult.assumptions.spread_bps} / ${backtestResult.assumptions.slippage_bps} bps`} />
              </div>

              {equityPoints.length > 0 && (
                <div>
                  <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-400">Cumulative net P&L</h3>
                  <StrategyEquityChart points={equityPoints} />
                </div>
              )}

              {backtestResult.trades.length > 0 && (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[560px] text-left text-xs">
                    <thead className="border-b border-slate-800 text-[10px] uppercase tracking-wider text-slate-500">
                      <tr>
                        <th className="px-3 py-1">Entry</th>
                        <th className="px-3 py-1">Exit</th>
                        <th className="px-3 py-1">Reason</th>
                        <th className="px-3 py-1">Net P&L</th>
                        <th className="px-3 py-1">Return</th>
                      </tr>
                    </thead>
                    <tbody>
                      {backtestResult.trades.map((trade, index) => (
                        <tr key={`${backtestResult.symbol}-${trade.entry_at}-${index}`} className="border-b border-slate-900/80">
                          <td className="px-3 py-1 text-slate-500">{trade.entry_at}</td>
                          <td className="px-3 py-1 text-slate-500">{trade.exit_at}</td>
                          <td className="px-3 py-1 text-slate-500">{trade.exit_reason ?? "—"}</td>
                          <td className={`px-3 py-1 ${trade.net_pnl >= 0 ? "text-gain" : "text-loss"}`}>₹{trade.net_pnl.toFixed(2)}</td>
                          <td className={`px-3 py-1 ${trade.net_return_pct >= 0 ? "text-gain" : "text-loss"}`}>{trade.net_return_pct.toFixed(2)}%</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {backtestResult.open_position && (
                <p className="text-[10px] text-slate-500">
                  Open position at {backtestResult.open_position.entry_at} (₹{backtestResult.open_position.entry_price}) — {backtestResult.open_position.state.replaceAll("_", " ")}.
                </p>
              )}

              <ul className="space-y-1 border-t border-slate-800 pt-2 text-[10px] leading-4 text-slate-500">
                <li>{backtestResult.assumptions.fill_basis}</li>
                <li>{backtestResult.evidence.basis}</li>
                {backtestResult.disclosures.map((line) => <li key={line}>{line}</li>)}
              </ul>
            </CardContent>
          </Card>
        )}

        {savedQuery.data?.strategies?.length ? (
          <Card>
            <CardHeader>
              <CardTitle>Saved strategies</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {savedQuery.data.strategies.map((strategy) => (
                <div key={strategy.id} className="flex flex-wrap items-center justify-between gap-2 text-xs">
                  <button type="button" className="text-left text-slate-200 hover:text-accent" onClick={() => loadSaved(strategy)} aria-label={`Load ${strategy.name}`}>
                    <b className="block">{strategy.name}</b>
                    <small className="text-slate-500">{(strategy.definition?.symbols ?? []).join(", ")}</small>
                  </button>
                  <span className="flex items-center gap-2">
                    <Link href={`/forward-tests?strategy=${strategy.id}`} className="interactive-surface inline-flex min-h-9 items-center gap-1 rounded border border-slate-800 px-2 text-[11px] text-slate-400 hover:text-slate-100">
                      <FlaskConical aria-hidden="true" size={13} /> Forward test
                    </Link>
                    <Button variant="danger" onClick={() => deleteMutation.mutate(strategy.id)} aria-label={`Delete ${strategy.name}`}>
                      <Trash2 aria-hidden="true" size={14} />
                    </Button>
                  </span>
                </div>
              ))}
            </CardContent>
          </Card>
        ) : null}
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

type BuildGroupProps = {
  group: GroupDraft;
  metrics: MetricMeta[];
  operators: OperatorMeta[];
  crossOperators: OperatorMeta[];
  accentId: string;
  onJoin: (join: "and" | "or") => void;
  onCondition: (index: number, patch: Partial<ConditionDraft>) => void;
  onAddCondition: () => void;
  onRemoveCondition: (index: number) => void;
  onRemoveGroup: () => void;
};

function BuildGroup({ group, metrics, operators, crossOperators, accentId, onJoin, onCondition, onAddCondition, onRemoveCondition, onRemoveGroup }: BuildGroupProps) {
  return (
    <div className="rounded border border-slate-800/80 bg-slate-950/40 p-2">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-[10px] uppercase tracking-wide text-slate-500">
        <span className="text-slate-400">Group</span>
        <span className="flex items-center gap-2">
          <Select aria-label={`Join for ${accentId}`} className="text-[10px]" value={group.join} onChange={(event) => onJoin(event.target.value === "or" ? "or" : "and")}>
            <option value="and">Match ALL conditions (AND)</option>
            <option value="or">Match ANY condition (OR)</option>
          </Select>
          <Button variant="ghost" aria-label={`Remove group ${accentId}`} onClick={onRemoveGroup} disabled={group.conditions.length === 0}>
            <X aria-hidden="true" size={13} />
          </Button>
        </span>
      </div>
      <div className="space-y-2">
        {group.conditions.map((condition, cIndex) => {
          const op = operators.find((item) => item.name === condition.operator);
          return (
            <div key={`${accentId}-cond-${cIndex}`} className="flex flex-wrap items-center gap-2">
              <Select
                aria-label={`Metric for condition ${cIndex + 1}`}
                value={condition.metric}
                onChange={(event) => onCondition(cIndex, { metric: event.target.value })}
              >
                {metrics.map((metric) => (
                  <option key={metric.name} value={metric.name}>{metric.label}</option>
                ))}
              </Select>
              <Select
                aria-label={`Comparator for condition ${cIndex + 1}`}
                value={condition.operator}
                onChange={(event) => onCondition(cIndex, { operator: event.target.value })}
              >
                {operators.map((operator) => (
                  <option key={operator.name} value={operator.name}>{operator.label}</option>
                ))}
              </Select>
              {op?.arity === 2 ? (
                <>
                  <Input
                    aria-label={`Lower bound for condition ${cIndex + 1}`}
                    className="w-24"
                    inputMode="decimal"
                    value={condition.value}
                    onChange={(event) => onCondition(cIndex, { value: event.target.value })}
                  />
                  <Input
                    aria-label={`Upper bound for condition ${cIndex + 1}`}
                    className="w-24"
                    inputMode="decimal"
                    value={condition.value2}
                    onChange={(event) => onCondition(cIndex, { value2: event.target.value })}
                  />
                </>
              ) : (
                <>
                  <Input
                    aria-label={`Value for condition ${cIndex + 1}`}
                    className="w-28"
                    inputMode="decimal"
                    value={condition.value}
                    onChange={(event) => onCondition(cIndex, { value: event.target.value })}
                  />
                  {crossOperators.some((item) => item.name === condition.operator) ? (
                    <Select
                      aria-label={`Compare metric for condition ${cIndex + 1}`}
                      value={condition.compare_metric}
                      onChange={(event) => onCondition(cIndex, { compare_metric: event.target.value })}
                    >
                      <option value="">Against value</option>
                      {metrics.map((metric) => (
                        <option key={`cmp-${metric.name}`} value={metric.name}>{metric.label}</option>
                      ))}
                    </Select>
                  ) : null}
                </>
              )}
              <Button variant="ghost" aria-label={`Remove condition ${cIndex + 1}`} onClick={() => onRemoveCondition(cIndex)}>
                <X aria-hidden="true" size={13} />
              </Button>
              <span className="text-[10px] text-slate-500">{metrics.find((metric) => metric.name === condition.metric)?.basis}</span>
            </div>
          );
        })}
        <Button variant="ghost" onClick={onAddCondition}>
          <Plus aria-hidden="true" size={13} /> Condition
        </Button>
      </div>
    </div>
  );
}
