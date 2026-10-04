"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Filter, Play, Plus, Save, Trash2, X } from "lucide-react";
import { TerminalShell } from "@/components/TerminalShell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { api, formatApiError } from "@/lib/api";
import { useIsFeatureEnabled } from "@/lib/features";
import { useScreenerLiveQuotes } from "@/lib/liveQuotes";
import { inr, pct } from "@/lib/utils";

/**
 * Screener (v9 Part E2).
 *
 * Filter builder over the documented fields from `GET /api/v1/screener/fields`.
 * Field names and limits come from the backend catalogue. Operators are the
 * stable request-contract values accepted by ScreenerFilterPayload. Excluded
 * symbols are always shown with their reason.
 */

type FieldMeta = { name: string; label: string; unit: string; sessions_required: number; basis: string };
type Operator = "gt" | "gte" | "lt" | "lte" | "eq" | "between";
type DraftFilter = { field: string; operator: Operator; value: string; value2: string };

type Match = {
  symbol: string;
  as_of?: string;
  sessions_available?: number;
  metrics: Record<string, number | null>;
  matched_filters?: unknown[];
};

type RunResponse = {
  generated_at: string;
  filters: unknown[];
  sort: { field: string; descending: boolean };
  coverage: {
    requested: number;
    evaluated: number;
    matched: number;
    excluded: Array<{ symbol: string; reason: string; fields?: string[]; sessions_available?: number }>;
  };
  matches: Match[];
  truncated: boolean;
  evidence: { basis: string; min_sessions_required: number };
  disclosures: string[];
};

type SavedScreen = {
  id: number;
  name: string;
  filters: Array<{ field: string; op: Operator; value?: number; low?: number; high?: number }>;
  sort: { field?: string | null; descending: boolean };
  symbols?: string[];
};

const OPERATOR_LABELS: Record<Operator, string> = {
  gt: "greater than",
  gte: "at least",
  lt: "less than",
  lte: "at most",
  eq: "equals",
  between: "between",
};

const STARTER_SCREENS: Array<{ name: string; filters: DraftFilter[] }> = [
  {
    name: "Above 200-day average",
    filters: [{ field: "close_vs_sma200_pct", operator: "gt", value: "0", value2: "" }],
  },
  {
    name: "Oversold with volume",
    filters: [
      { field: "rsi_14", operator: "lt", value: "35", value2: "" },
      { field: "volume_vs_20d_avg", operator: "gt", value: "1.5", value2: "" },
    ],
  },
  {
    name: "Near 52-week high",
    filters: [{ field: "distance_from_52w_high_pct", operator: "gte", value: "-5", value2: "" }],
  },
];

function toPayloadFilter(filter: DraftFilter) {
  if (filter.operator === "between") {
    return { field: filter.field, op: filter.operator, low: Number(filter.value), high: Number(filter.value2) };
  }
  return { field: filter.field, op: filter.operator, value: Number(filter.value) };
}

export default function ScreenerPage() {
  const queryClient = useQueryClient();
  const [filters, setFilters] = useState<DraftFilter[]>([
    { field: "rsi_14", operator: "lt", value: "40", value2: "" },
  ]);
  const [symbols, setSymbols] = useState("");
  const [sortField, setSortField] = useState("");
  const [descending, setDescending] = useState(true);
  const [screenName, setScreenName] = useState("");
  const [error, setError] = useState("");

  const fieldsQuery = useQuery({
    queryKey: ["screener-fields"],
    queryFn: () => api<{ fields: FieldMeta[] } | FieldMeta[]>("/api/v1/screener/fields"),
    staleTime: 10 * 60_000,
  });

  const fields: FieldMeta[] = useMemo(() => {
    const payload = fieldsQuery.data;
    if (!payload) return [];
    return Array.isArray(payload) ? payload : payload.fields ?? [];
  }, [fieldsQuery.data]);

  const savedQuery = useQuery({
    queryKey: ["screener-saved"],
    queryFn: () => api<{ screens?: SavedScreen[]; items?: SavedScreen[] }>("/api/v1/screener/saved"),
    staleTime: 60_000,
  });
  const savedScreens = savedQuery.data?.screens ?? savedQuery.data?.items ?? [];

  const runMutation = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api<RunResponse>("/api/v1/screener/run", { method: "POST", body: JSON.stringify(body) }),
    onError: (err) => setError(formatApiError(err)),
    onSuccess: () => setError(""),
  });

  const saveMutation = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api<{ id: number }>("/api/v1/screener/saved", { method: "POST", body: JSON.stringify(body) }),
    onError: (err) => setError(formatApiError(err)),
    onSuccess: () => {
      setError("");
      setScreenName("");
      queryClient.invalidateQueries({ queryKey: ["screener-saved"] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api(`/api/v1/screener/saved/${id}`, { method: "DELETE" }),
    onError: (err) => setError(formatApiError(err)),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["screener-saved"] }),
  });

  const runSavedMutation = useMutation({
    mutationFn: (id: number) => api<RunResponse>(`/api/v1/screener/saved/${id}/run`, { method: "POST" }),
    onError: (err) => setError(formatApiError(err)),
    onSuccess: () => setError(""),
  });

  const results = runSavedMutation.data ?? runMutation.data;
  const busy = runMutation.isPending || runSavedMutation.isPending;

  const liveEnabled = useIsFeatureEnabled("screener_live_quotes");
  const live = useScreenerLiveQuotes(liveEnabled ? (results?.matches.map((row) => row.symbol) ?? []) : [], liveEnabled);

  function requestBody() {
    const body: Record<string, unknown> = { filters: filters.map(toPayloadFilter) };
    const list = symbols.split(",").map((item) => item.trim()).filter(Boolean);
    if (list.length) body.symbols = list;
    if (sortField) {
      body.sort_by = sortField;
      body.descending = descending;
    }
    return body;
  }

  const resultColumns = useMemo(() => {
    const used = filters.map((filter) => filter.field);
    if (sortField && !used.includes(sortField)) used.push(sortField);
    return used.length ? used : ["last_close"];
  }, [filters, sortField]);

  return (
    <TerminalShell>
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 px-3 py-4 sm:px-4">
        <header>
          <h1 className="display-font text-lg font-semibold text-slate-100">Screener</h1>
          <p className="text-xs text-slate-500">
            Filters run on realized daily OHLCV history. Results are not forecasts, rankings of quality, or recommendations.
          </p>
        </header>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Filter aria-hidden="true" size={14} /> Filters
            </CardTitle>
            <div className="flex flex-wrap gap-2">
              {STARTER_SCREENS.map((starter) => (
                <Button
                  key={starter.name}
                  variant="ghost"
                  className="text-[11px]"
                  onClick={() => setFilters(starter.filters.map((item) => ({ ...item })))}
                >
                  {starter.name}
                </Button>
              ))}
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            {fieldsQuery.isError && (
              <p role="alert" className="text-xs text-loss">{formatApiError(fieldsQuery.error)}</p>
            )}

            {filters.map((filter, index) => (
              <div key={`filter-${index}`} className="flex flex-wrap items-center gap-2">
                <Select
                  aria-label={`Field for filter ${index + 1}`}
                  value={filter.field}
                  onChange={(event) =>
                    setFilters((current) =>
                      current.map((item, position) => (position === index ? { ...item, field: event.target.value } : item)),
                    )
                  }
                >
                  {fields.map((field) => (
                    <option key={field.name} value={field.name}>{field.label}</option>
                  ))}
                </Select>
                <Select
                  aria-label={`Comparator for filter ${index + 1}`}
                  value={filter.operator}
                  onChange={(event) =>
                    setFilters((current) =>
                      current.map((item, position) =>
                        position === index ? { ...item, operator: event.target.value as Operator } : item,
                      ),
                    )
                  }
                >
                  {(Object.keys(OPERATOR_LABELS) as Operator[]).map((operator) => (
                    <option key={operator} value={operator}>{OPERATOR_LABELS[operator]}</option>
                  ))}
                </Select>
                <Input
                  aria-label={`Value for filter ${index + 1}`}
                  className="w-28"
                  inputMode="decimal"
                  value={filter.value}
                  onChange={(event) =>
                    setFilters((current) =>
                      current.map((item, position) => (position === index ? { ...item, value: event.target.value } : item)),
                    )
                  }
                />
                {filter.operator === "between" && (
                  <Input
                    aria-label={`Upper value for filter ${index + 1}`}
                    className="w-28"
                    inputMode="decimal"
                    value={filter.value2}
                    onChange={(event) =>
                      setFilters((current) =>
                        current.map((item, position) => (position === index ? { ...item, value2: event.target.value } : item)),
                      )
                    }
                  />
                )}
                <Button
                  variant="ghost"
                  aria-label={`Remove filter ${index + 1}`}
                  onClick={() => setFilters((current) => current.filter((_, position) => position !== index))}
                >
                  <X aria-hidden="true" size={14} />
                </Button>
                <span className="text-[10px] text-slate-500">
                  {fields.find((field) => field.name === filter.field)?.basis}
                </span>
              </div>
            ))}

            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="ghost"
                onClick={() =>
                  setFilters((current) => [
                    ...current,
                    { field: fields[0]?.name ?? "last_close", operator: "gt", value: "0", value2: "" },
                  ])
                }
              >
                <Plus aria-hidden="true" size={14} /> Add filter
              </Button>
              <Input
                aria-label="Symbols, comma separated"
                placeholder="Symbols (blank = default universe)"
                className="w-64"
                value={symbols}
                onChange={(event) => setSymbols(event.target.value)}
              />
              <Select aria-label="Sort field" value={sortField} onChange={(event) => setSortField(event.target.value)}>
                <option value="">Sort: symbol</option>
                {fields.map((field) => (
                  <option key={`sort-${field.name}`} value={field.name}>{field.label}</option>
                ))}
              </Select>
              <label className="flex items-center gap-1 text-xs text-slate-400">
                <input type="checkbox" checked={descending} onChange={(event) => setDescending(event.target.checked)} />
                Descending
              </label>
              <Button onClick={() => runMutation.mutate(requestBody())} disabled={busy || !filters.length}>
                <Play aria-hidden="true" size={14} /> {busy ? "Running…" : "Run screen"}
              </Button>
            </div>

            <div className="flex flex-wrap items-center gap-2 border-t border-slate-800 pt-3">
              <Input
                aria-label="Saved screen name"
                placeholder="Save this screen as…"
                className="w-64"
                value={screenName}
                onChange={(event) => setScreenName(event.target.value)}
              />
              <Button
                variant="ghost"
                disabled={!screenName.trim() || saveMutation.isPending}
                onClick={() => saveMutation.mutate({ name: screenName.trim(), ...requestBody() })}
              >
                <Save aria-hidden="true" size={14} /> Save
              </Button>
            </div>

            {error && <p role="alert" className="text-xs text-loss">{error}</p>}
          </CardContent>
        </Card>

        {savedScreens.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>Saved screens</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {savedScreens.map((screen) => (
                <div key={screen.id} className="flex flex-wrap items-center justify-between gap-2 text-xs">
                  <span className="text-slate-200">{screen.name}</span>
                  <span className="flex items-center gap-2">
                    <Button variant="ghost" onClick={() => runSavedMutation.mutate(screen.id)} aria-label={`Run ${screen.name}`}>
                      <Play aria-hidden="true" size={14} /> Run
                    </Button>
                    <Button variant="danger" onClick={() => deleteMutation.mutate(screen.id)} aria-label={`Delete ${screen.name}`}>
                      <Trash2 aria-hidden="true" size={14} />
                    </Button>
                  </span>
                </div>
              ))}
            </CardContent>
          </Card>
        )}

        {results && (
          <Card>
            <CardHeader>
              <CardTitle>Matches</CardTitle>
              <span className="text-[10px] text-slate-500">
                {results.coverage.matched} matched of {results.coverage.evaluated} evaluated
                {results.truncated ? " (truncated)" : ""}
              </span>
            </CardHeader>
            <CardContent className="overflow-x-auto p-0">
              <table className="w-full min-w-[560px] text-left text-xs">
                <thead className="border-b border-slate-800 text-[10px] uppercase tracking-wider text-slate-500">
                  <tr>
                    <th className="px-4 py-2">Symbol</th>
                    {liveEnabled && <th className="px-4 py-2">Live</th>}
                    {resultColumns.map((column) => (
                      <th key={column} className="px-4 py-2">{column}</th>
                    ))}
                    <th className="px-4 py-2">As of</th>
                  </tr>
                </thead>
                <tbody>
                  {results.matches.map((row) => {
                    const quote = live[row.symbol.toUpperCase()];
                    return (
                    <tr key={row.symbol} className="border-b border-slate-900/80">
                      <td className="px-4 py-2 text-slate-100">{row.symbol}</td>
                      {liveEnabled && (
                        <td data-testid={`live-${row.symbol.toUpperCase()}`} className="px-4 py-2 tabular text-slate-200">
                          {quote?.price != null ? (
                            <span className="inline-flex flex-col leading-4">
                              <span>{inr(quote.price)}</span>
                              {quote.change_pct != null && (
                                <span className={`text-[10px] ${quote.change_pct >= 0 ? "text-gain" : "text-loss"}`}>
                                  {pct(quote.change_pct)}
                                </span>
                              )}
                              <span className={`text-[9px] uppercase ${quote.connection_status === "disconnected" ? "text-loss" : quote.is_stale ? "text-warning" : "text-slate-600"}`}>
                                {quote.connection_status === "disconnected" ? "disconnected" : quote.is_stale ? "stale" : quote.stream ?? "…"}
                              </span>
                            </span>
                          ) : (
                            <span className="text-slate-600">…</span>
                          )}
                        </td>
                      )}
                      {resultColumns.map((column) => (
                        <td key={`${row.symbol}-${column}`} className="px-4 py-2 text-slate-300">
                          {row.metrics?.[column] == null ? "—" : Number(row.metrics[column]).toFixed(2)}
                        </td>
                      ))}
                      <td className="px-4 py-2 text-slate-500">{row.as_of ?? "—"}</td>
                    </tr>
                    );
                  })}
                  {results.matches.length === 0 && (
                    <tr>
                      <td colSpan={resultColumns.length + (liveEnabled ? 2 : 1) + 1} className="px-4 py-3 text-slate-500">
                        No symbol matched every filter.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </CardContent>
            {results.coverage.excluded.length > 0 && (
              <CardContent className="border-t border-slate-800 text-[10px] leading-4 text-slate-500">
                Excluded:{" "}
                {results.coverage.excluded
                  .map((row) => `${row.symbol} (${row.reason}${row.fields?.length ? `: ${row.fields.join(", ")}` : ""})`)
                  .join("; ")}
              </CardContent>
            )}
            <CardContent className="border-t border-slate-800">
              <ul className="space-y-1 text-[10px] leading-4 text-slate-500">
                <li>{results.evidence.basis}</li>
                {results.disclosures.map((line) => <li key={line}>{line}</li>)}
              </ul>
            </CardContent>
          </Card>
        )}
      </div>
    </TerminalShell>
  );
}
