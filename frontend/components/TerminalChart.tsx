"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AreaSeries,
  CandlestickSeries,
  ColorType,
  createChart,
  HistogramSeries,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { api, Candle, Forecast, formatApiError, requestForecast, type MarketDataContext, wsBase } from "@/lib/api";
import { TimeframeBar, type Timeframe } from "@/components/TimeframeBar";
import { MarketSourceLabel, useMarket } from "@/components/MarketContext";
import { useIsFeatureEnabled } from "@/lib/features";
import { buildLayoutPayload, canApplyRange, type ChartLayout } from "@/lib/chartLayouts";
import { Bookmark, Save, X } from "lucide-react";

const windows: Record<Timeframe, string> = {
  "1m": "1w",
  "5m": "1mo",
  "15m": "3mo",
  "1h": "3mo",
  "4h": "1y",
  "1D": "1y",
  "1W": "5y",
};

function unix(value: string) {
  return Math.floor(new Date(value).getTime() / 1000) as UTCTimestamp;
}

function nextStepSeconds(tf: Timeframe) {
  if (tf === "1W") return 604800;
  if (tf === "1D") return 86400;
  if (tf === "4h") return 14400;
  if (tf === "1h") return 3600;
  return Number.parseInt(tf, 10) * 60;
}

export function TerminalChart({
  onForecast,
}: {
  onForecast?: (value: Forecast | undefined) => void;
}) {
  const { selectedSymbol: symbol, timeframe: tf, setTimeframe, dispatchSurface } = useMarket();
  const root = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const candleSeries = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeries = useRef<ISeriesApi<"Histogram"> | null>(null);
  const liveCandle = useRef<Candle | null>(null);
  const pendingRange = useRef<{ symbol: string; tf: Timeframe; from: number; to: number } | null>(null);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [forecast, setForecast] = useState<Forecast>();
  const [historyContext, setHistoryContext] = useState<MarketDataContext>();
  const [overlays, setOverlays] = useState({ ema: true, vwap: false, band: true });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [streamMode, setStreamMode] = useState<"native"|"redis_relay"|"rest_poll"|"stale"|"connecting">("connecting");
  const [forecastPhase, setForecastPhase] = useState("starting");
  const [requestNonce, setRequestNonce] = useState(0);
  const [layoutName, setLayoutName] = useState("");
  const layoutsEnabled = useIsFeatureEnabled("saved_chart_layouts");
  const queryClient = useQueryClient();

  const layoutsQuery = useQuery({
    queryKey: ["chart-layouts"],
    queryFn: () => api<{ items?: ChartLayout[] }>("/api/v1/chart-layouts"),
    staleTime: 30_000,
    enabled: layoutsEnabled,
  });
  const layouts = layoutsQuery.data?.items ?? [];

  const saveLayout = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api<{ id: number }>("/api/v1/chart-layouts", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => {
      setLayoutName("");
      queryClient.invalidateQueries({ queryKey: ["chart-layouts"] });
    },
  });
  const deleteLayout = useMutation({
    mutationFn: (id: number) => api(`/api/v1/chart-layouts/${id}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["chart-layouts"] }),
  });

  useEffect(() => {
    let dead = false;
    const historyController = new AbortController();
    const forecastController = new AbortController();
    const historyTimeout = window.setTimeout(() => historyController.abort(), 25_000);
    setError("");
    setLoading(true);
    setCandles([]);
    setForecast(undefined);
    setForecastPhase("starting");
    setHistoryContext(undefined);
    onForecast?.(undefined);
    dispatchSurface({ type: "context", surface: "history" });
    dispatchSurface({ type: "context", surface: "forecast" });
    api<{ candles: Candle[]; context?: MarketDataContext }>(
      `/api/v1/market/history/${encodeURIComponent(symbol)}?timeframe=${tf}&window=${windows[tf]}&limit=4000`,
      { signal: historyController.signal },
    )
      .then((response) => { if (!dead) { setCandles(response.candles || []); liveCandle.current = response.candles?.at(-1) || null; setHistoryContext(response.context); dispatchSurface({ type: "context", surface: "history", context: response.context }); setLoading(false); } })
      .catch((reason: unknown) => { if (!dead) { const message = historyController.signal.aborted ? "Chart history timed out. Retry with the same context." : formatApiError(reason); setError(message); dispatchSurface({ type: "error", surface: "history", error: message }); setLoading(false); } });

    const training = windows[tf];
    requestForecast(symbol, training, tf, 0.8, forecastController.signal, setForecastPhase)
      .then((response) => {
        if (!dead) {
          setForecast(response);
          setForecastPhase("completed");
          dispatchSurface({ type: "context", surface: "forecast", context: response.context });
          onForecast?.(response);
        }
      })
      .catch((reason: unknown) => { if (!dead) { const message = formatApiError(reason); setForecastPhase("unavailable"); dispatchSurface({ type: "error", surface: "forecast", error: message }); } });
    return () => {
      dead = true;
      window.clearTimeout(historyTimeout);
      historyController.abort();
      forecastController.abort();
    };
  }, [symbol, tf, requestNonce, onForecast, dispatchSurface]);

  useEffect(() => {
    if (!root.current || !candles.length) return;
    chart.current?.remove();
    const theme = getComputedStyle(document.documentElement);
    const instance = createChart(root.current, {
      height: 520,
      layout: { background: { type: ColorType.Solid, color: theme.getPropertyValue("--terminal-950").trim() }, textColor: theme.getPropertyValue("--slate-500").trim(), fontSize: 11 },
      grid: { vertLines: { color: theme.getPropertyValue("--terminal-800").trim() }, horzLines: { color: theme.getPropertyValue("--terminal-800").trim() } },
      rightPriceScale: { borderColor: theme.getPropertyValue("--slate-800").trim() },
      timeScale: { borderColor: theme.getPropertyValue("--slate-800").trim(), timeVisible: tf !== "1D" && tf !== "1W", secondsVisible: false },
    });
    chart.current = instance;

    const candlesSeries = instance.addSeries(CandlestickSeries, {
      upColor: "#39d98a",
      downColor: "#ff6673",
      borderVisible: false,
      wickUpColor: "#39d98a",
      wickDownColor: "#ff6673",
    });
    candleSeries.current = candlesSeries;
    candlesSeries.setData(candles.map((item) => ({ time: unix(item.time), open: item.open, high: item.high, low: item.low, close: item.close })));

    const volume = instance.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "" });
    volumeSeries.current = volume;
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    volume.setData(
      candles.map((item) => ({
        time: unix(item.time),
        value: item.volume,
        color: item.close >= item.open ? "rgba(57,217,138,.30)" : "rgba(255,102,115,.30)",
      })),
    );

    if (overlays.ema) {
      let ema = candles[0].close;
      const k = 2 / 21;
      const series = instance.addSeries(LineSeries, { color: "#58a6ff", lineWidth: 1, priceLineVisible: false });
      series.setData(
        candles.map((item) => {
          ema = item.close * k + ema * (1 - k);
          return { time: unix(item.time), value: ema };
        }),
      );
    }

    if (overlays.vwap) {
      let day = "";
      let priceVolume = 0;
      let volumeTotal = 0;
      const series = instance.addSeries(LineSeries, { color: "#f5b94c", lineWidth: 1, priceLineVisible: false });
      series.setData(
        candles.map((item) => {
          const currentDay = item.time.slice(0, 10);
          if (currentDay !== day) {
            day = currentDay;
            priceVolume = 0;
            volumeTotal = 0;
          }
          const typical = (item.high + item.low + item.close) / 3;
          priceVolume += typical * item.volume;
          volumeTotal += item.volume;
          return { time: unix(item.time), value: volumeTotal ? priceVolume / volumeTotal : item.close };
        }),
      );
    }

    if (overlays.band && forecast?.research_range) {
      const lastCandle = candles.at(-1)!;
      const last = unix(lastCandle.time);
      const future = forecast.target_timestamp ? unix(forecast.target_timestamp) : (last + nextStepSeconds(tf)) as UTCTimestamp;
      const low = instance.addSeries(LineSeries, { color: "rgba(129,140,248,.78)", lineWidth: 1, lineStyle: 2, priceLineVisible: false });
      const high = instance.addSeries(LineSeries, { color: "rgba(56,189,248,.78)", lineWidth: 1, lineStyle: 2, priceLineVisible: false });
      const mid = instance.addSeries(LineSeries, { color: "#34d399", lineWidth: 2, priceLineVisible: false });
      const area = instance.addSeries(AreaSeries, {
        lineColor: "rgba(52,211,153,.01)",
        topColor: "rgba(52,211,153,.15)",
        bottomColor: "rgba(52,211,153,.02)",
        priceLineVisible: false,
      });
      low.setData([{ time: last, value: lastCandle.close }, { time: future, value: forecast.research_range.low }]);
      high.setData([{ time: last, value: lastCandle.close }, { time: future, value: forecast.research_range.high }]);
      mid.setData([{ time: last, value: lastCandle.close }, { time: future, value: forecast.research_range.median_reference }]);
      area.setData([{ time: last, value: lastCandle.close }, { time: future, value: forecast.research_range.median_reference }]);
    }

    instance.timeScale().fitContent();
    if (
      pendingRange.current
      && pendingRange.current.symbol === symbol
      && pendingRange.current.tf === tf
    ) {
      instance.timeScale().setVisibleRange({ from: pendingRange.current.from as UTCTimestamp, to: pendingRange.current.to as UTCTimestamp });
      pendingRange.current = null;
    }
    const resize = new ResizeObserver(() => root.current && instance.applyOptions({ width: root.current.clientWidth }));
    resize.observe(root.current);
    return () => {
      resize.disconnect();
      instance.remove();
      chart.current = null;
      candleSeries.current = null;
      volumeSeries.current = null;
    };
  }, [candles, forecast, overlays, tf]);

  useEffect(() => {
    if (!["1m", "5m", "15m"].includes(tf)) return;
    const socket = new WebSocket(`${wsBase()}/ws/quotes/${encodeURIComponent(symbol)}?interval_seconds=1&timeframe=${encodeURIComponent(tf)}`);
    setStreamMode("connecting");
    socket.onerror = () => setStreamMode("stale");
    socket.onclose = () => setStreamMode((current) => current === "stale" ? current : "stale");
    socket.onmessage = (event) => {
      try {
        const quote = JSON.parse(event.data) as { price?: number; is_stale?: boolean; stream?: "native"|"redis_relay"|"rest_poll"; context?: MarketDataContext };
        if (quote.stream) setStreamMode(quote.is_stale ? "stale" : quote.stream);
        const context = quote.context;
        const contextMatches = context
          && context.requested_symbol.toUpperCase() === symbol.toUpperCase()
          && context.timeframe === tf
          && (!historyContext || context.provider === historyContext.provider);
        if (!contextMatches) {
          dispatchSurface({ type: "error", surface: "stream", error: "Stream context does not match the chart symbol, provider, or timeframe." });
          setStreamMode("stale");
          return;
        }
        dispatchSurface({ type: "context", surface: "stream", context });
        if (quote.price && !quote.is_stale) {
          const previous = liveCandle.current;
          if (!previous) return;
          const updated = {
            ...previous,
            close: quote.price,
            high: Math.max(previous.high, quote.price),
            low: Math.min(previous.low, quote.price),
          };
          liveCandle.current = updated;
          const time = unix(updated.time);
          candleSeries.current?.update({ time, open: updated.open, high: updated.high, low: updated.low, close: updated.close });
          volumeSeries.current?.update({ time, value: updated.volume, color: updated.close >= updated.open ? "rgba(57,217,138,.30)" : "rgba(255,102,115,.30)" });
        }
      } catch {
        // Ignore malformed one-off frames; the next valid quote can still update the chart.
      }
    };
    return () => socket.close();
  }, [dispatchSurface, historyContext, symbol, tf]);

  return (
    <div className="h-full rounded-lg border border-slate-800 bg-terminal-950">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-800 p-2">
        <TimeframeBar value={tf} onChange={setTimeframe} />
        {!forecast && !error && <span className="inline-flex min-h-8 items-center rounded-full border border-info/25 bg-info/5 px-2 text-[9px] font-semibold uppercase tracking-wide text-info" role="status" aria-live="polite">Forecast: {forecastPhase.replaceAll("_", " ")}</span>}
        <span className="ml-auto inline-flex min-h-8 items-center gap-2 rounded-full border border-slate-800 px-2 py-1 text-[9px] uppercase tracking-wide text-slate-500"><span className={`connection-dot ${streamMode==="native"||streamMode==="redis_relay"?"live":streamMode==="rest_poll"?"rest":"stale"}`}/>{streamMode.replace("_"," ")}</span>
        <span className="sr-only" role="status" aria-live="polite">Forecast job {forecastPhase}</span>
        <span className="mx-1 h-4 w-px bg-slate-800" />
{Object.entries(overlays).map(([key, enabled]) => (
          <button
            key={key}
            type="button"
            aria-pressed={enabled}
            onClick={() => setOverlays((current) => ({ ...current, [key]: !enabled }))}
            className={`min-h-11 rounded px-2 py-1 text-[10px] uppercase ${enabled ? "text-slate-200" : "text-slate-600"}`}
          >
            {key}
          </button>
        ))}
        {layoutsEnabled && (
          <>
            <span className="mx-1 h-4 w-px bg-slate-800" />
            {layouts.length > 0 && (
              <div className="flex min-h-11 items-center gap-1" role="group" aria-label="Apply a saved chart layout">
                {layouts.slice(0, 4).map((layout) => (
                  <button
                    key={layout.id}
                    type="button"
                    aria-pressed={canApplyRange(layout, symbol, tf)}
                    onClick={() => {
                      if (layout.symbol && layout.symbol.toUpperCase() !== symbol.toUpperCase()) return;
                      setOverlays((current) => ({
                        ema: layout.overlays?.ema ?? current.ema,
                        vwap: layout.overlays?.vwap ?? current.vwap,
                        band: layout.overlays?.band ?? current.band,
                      }));
                      if (layout.timeframe && layout.timeframe !== tf) setTimeframe(layout.timeframe);
                      if (canApplyRange(layout, symbol, tf)) {
                        pendingRange.current = { symbol, tf, from: layout.visible_range!.from, to: layout.visible_range!.to };
                      }
                    }}
                    className="min-h-11 rounded border border-slate-700 bg-terminal-950 px-2 py-1 text-[10px] text-slate-300"
                    title={`${layout.name} · ${layout.symbol} · ${layout.timeframe}`}
                  >
                    <Bookmark aria-hidden="true" size={13} className="mr-1 inline-block align-[-2px] text-slate-500" />
                    {layout.name}
                  </button>
                ))}
              </div>
            )}
            {layouts.length > 0 && <span className="mx-1 h-4 w-px bg-slate-800" />}
            <input
              aria-label="Saved layout name"
              placeholder="Name this view…"
              value={layoutName}
              onChange={(event) => setLayoutName(event.target.value)}
              className="w-32 rounded-md border border-slate-700 bg-terminal-950 px-2 py-1 text-[11px] text-slate-200"
            />
            <button
              type="button"
              disabled={!layoutName.trim() || saveLayout.isPending}
              onClick={() => {
                const visible = chart.current?.timeScale().getVisibleRange();
                const payload = buildLayoutPayload(
                  {
                    symbol,
                    timeframe: tf,
                    overlays,
                    visible_range:
                      visible && typeof visible.from === "number" && typeof visible.to === "number"
                        ? { from: visible.from, to: visible.to }
                        : undefined,
                  },
                  layoutName,
                );
                saveLayout.mutate(payload);
              }}
              className="inline-flex min-h-11 items-center rounded px-2 py-1 text-[10px] uppercase text-slate-300"
            >
              <Save aria-hidden="true" size={12} className="mr-1" /> Save
            </button>
            {layouts.length > 0 && (
              <div className="flex flex-wrap items-center gap-1">
                {layouts.map((layout) => (
                  <span key={layout.id} className="inline-flex min-h-8 items-center gap-1 rounded border border-slate-800 px-1.5 text-[9px] uppercase tracking-wide text-slate-500">
                    {layout.name}
                    <button
                      type="button"
                      aria-label={`Delete layout ${layout.name}`}
                      onClick={() => deleteLayout.mutate(layout.id)}
                      className="text-slate-600 hover:text-loss"
                    >
                      <X aria-hidden="true" size={10} />
                    </button>
                  </span>
                ))}
              </div>
            )}
          </>
        )}
      </div>
      <MarketSourceLabel context={historyContext} />
      {forecast?.context && forecast.context.provider !== historyContext?.provider && <MarketSourceLabel context={forecast.context} />}
      {loading ? <div className="space-y-3 p-4"><div className="skeleton h-8 w-44 rounded"/><div className="skeleton h-[450px] w-full rounded-xl"/></div> : error ? <div className="grid h-[520px] place-items-center p-8 text-center"><div><div className="mx-auto mb-3 size-9 rounded-full border border-loss/30 bg-loss/10"/><p className="text-sm font-medium text-loss">Chart data unavailable</p><p className="mt-1 max-w-md text-xs leading-5 text-slate-500">{error}</p><button type="button" onClick={() => setRequestNonce((value) => value + 1)} className="mt-4 rounded border border-accent/40 bg-accent/10 px-4 py-2 text-xs text-accent">Retry</button></div></div> : candles.length ? <div ref={root} role="img" aria-label={`${symbol} ${tf} candlestick chart with ${candles.length} bars. Latest close ${candles.at(-1)?.close}.`} className="stockpilot-chart-cursor w-full" /> : <div className="grid h-[520px] place-items-center text-sm text-slate-500">No candles returned for this interval.</div>}
    </div>
  );
}
