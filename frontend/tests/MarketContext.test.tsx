import { useEffect, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DataSourceBadge } from "@/components/DataSourceBadge";
import { MarketProvider, MixedSourceNotice, useMarket, type MarketMode, type MarketSurface } from "@/components/MarketContext";
import { MarketWorkspace } from "@/components/MarketWorkspace";
import { Providers } from "@/components/Providers";
import type { Forecast, MarketDataContext, Quote } from "@/lib/api";
import type { Timeframe } from "@/components/TimeframeBar";
import { AuthProvider } from "@/lib/auth";

const apiState = vi.hoisted(() => ({ mode: "LIVE_ONLY", providers: [{ provider: "upstox", configured: true }] }));
const EMPTY_CONTEXTS: Partial<Record<MarketSurface, MarketDataContext>> = {};
const EMPTY_ERRORS: Partial<Record<MarketSurface, string>> = {};

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: vi.fn((path: string) => {
       if (path === "/api/v1/health") return Promise.resolve({ market_data: { provider_mode: apiState.mode, providers: apiState.providers } });
      return new Promise(() => {});
    }),
  };
});

vi.mock("@/components/TerminalChart", async () => {
  const { useMarket: useMarketSession } = await import("@/components/MarketContext");
  return {
    TerminalChart: () => {
      const { selectedSymbol, timeframe } = useMarketSession();
      return <div data-testid="chart-session">{selectedSymbol}:{timeframe}</div>;
    },
  };
});

vi.mock("@/components/IndicatorsPanel", () => ({
  IndicatorsPanel: ({ symbol, timeframe }: { symbol: string; timeframe: Timeframe }) => <div data-testid="indicator-session">{symbol}:{timeframe}</div>,
}));

function marketContext(provider: string, overrides: Partial<MarketDataContext> = {}): MarketDataContext {
  return {
    requested_symbol: "RELIANCE",
    resolved_instrument_key: "NSE_EQ|INE002A01018",
    exchange: "NSE",
    instrument_type: "EQUITY",
    provider,
    credential_mode: "oauth",
    timeframe: "15m",
    as_of: "2026-08-30T10:00:00Z",
    received_at: "2026-08-30T10:00:01Z",
    is_live: true,
    is_stale: false,
    fallback_used: false,
    fallback_reason: null,
    request_id: `${provider}-request`,
    ...overrides,
  };
}

function InjectMarketState({
  contexts = EMPTY_CONTEXTS,
  errors = EMPTY_ERRORS,
  symbol,
  timeframe,
}: {
  contexts?: Partial<Record<MarketSurface, MarketDataContext>>;
  errors?: Partial<Record<MarketSurface, string>>;
  symbol?: string;
  timeframe?: Timeframe;
}) {
  const market = useMarket();
  useEffect(() => {
    if (symbol) market.setSelectedSymbol(symbol);
    if (timeframe) market.setTimeframe(timeframe);
    for (const [surface, context] of Object.entries(contexts)) {
      market.dispatchSurface({ type: "context", surface: surface as MarketSurface, context });
    }
    for (const [surface, error] of Object.entries(errors)) {
      market.dispatchSurface({ type: "error", surface: surface as MarketSurface, error });
    }
  }, [contexts, errors, symbol, timeframe, market.dispatchSurface, market.setSelectedSymbol, market.setTimeframe]);
  return null;
}

function renderMarket(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><AuthProvider><MarketProvider>{children}</MarketProvider></AuthProvider></QueryClientProvider>);
}

beforeEach(() => {
  apiState.mode = "LIVE_ONLY";
  apiState.providers = [{ provider: "upstox", configured: true }];
});

describe("shared market session", () => {
  it("keeps chart, indicators, quote/depth, forecast, badge, and quick order on one symbol/provider/timeframe", async () => {
    apiState.mode = "FALLBACK_ALLOWED";
    const context = marketContext("yfinance", { is_live: false, fallback_used: true, fallback_reason: "Upstox timed out" });
    const quote: Quote = { symbol: "RELIANCE", price: 3010, source: "yfinance", timestamp: context.as_of || context.received_at, context };
    const forecast: Forecast = {
      symbol: "RELIANCE", title: "Research range", model_label: "calibrated interval ensemble",
      research_range: { low: 2950, median_reference: 3050, high: 3120, confidence_level: 0.8 },
      reference_price: 3010,
      confidence: { level: "moderate", summary: "The range mostly held on unseen history." },
      uncertainty: { range_width: 170, range_width_pct: 5.65, band: "moderate", summary: "The range is moderate." },
      scenarios: [
        { label: "Bear", low: 2950, high: 3010, description: "Lower scenario." },
        { label: "Base", low: 3010, high: 3050, description: "Central scenario." },
        { label: "Bull", low: 3050, high: 3120, description: "Upper scenario." },
      ],
      disclaimer: "Research support only.", context,
    };
    const contexts = { quote: context, history: context, forecast: context, indicators: context };

    renderMarket(
      <>
        <InjectMarketState symbol="RELIANCE" timeframe="15m" contexts={contexts} />
        <DataSourceBadge />
        <MarketWorkspace quote={quote} forecast={forecast} onForecast={() => undefined} />
      </>,
    );

    expect((await screen.findAllByTestId("chart-session"))[0]).toHaveTextContent("RELIANCE:15m");
    expect(screen.getAllByTestId("indicator-session")[0]).toHaveTextContent("RELIANCE:15m");
    expect(screen.getAllByText((content: string) => content.includes("RELIANCE")).length).toBeGreaterThan(0);
    expect(screen.getAllByText((content: string) => content.includes("15m")).length).toBeGreaterThan(0);
    expect(await screen.findByLabelText(/Fallback: yfinance: Upstox timed out/i)).toBeInTheDocument();
  });
});

describe("market data mode and provenance states", () => {
  it("keeps the full-width demo banner mounted across application route content changes", async () => {
    apiState.mode = "OFFLINE_DEMO";
    apiState.providers = [{ provider: "demo_india", configured: true }];
    const view = render(<Providers><div>Overview route</div></Providers>);

    expect(await screen.findByText("Demo / synthetic data")).toHaveAttribute("role", "status");
    view.rerender(<Providers><div>System route</div></Providers>);
    expect(screen.getByText("Demo / synthetic data")).toHaveAttribute("role", "status");
    expect(screen.getByText("System route")).toBeInTheDocument();
  });

  it("does not infer demo mode from configured provider order", async () => {
    apiState.mode = "LIVE_ONLY";
    apiState.providers = [{ provider: "demo_india", configured: true }];
    renderMarket(<DataSourceBadge />);

    expect(await screen.findByLabelText(/Configured: demo_india/i)).toBeInTheDocument();
    expect(screen.queryByText("Demo / synthetic data")).not.toBeInTheDocument();
  });

  it("shows the actual yfinance fallback and reason without classifying it as demo", async () => {
    apiState.mode = "FALLBACK_ALLOWED";
    const fallback = marketContext("yfinance", { is_live: false, fallback_used: true, fallback_reason: "Broker credentials unavailable" });
    renderMarket(<><InjectMarketState contexts={{ quote: fallback }} /><DataSourceBadge /></>);

    const badge = await screen.findByLabelText(/Fallback: yfinance: Broker credentials unavailable/i);
    expect(badge).toBeInTheDocument();
    expect(badge).not.toHaveTextContent(/demo/i);
  });

  it("renders LIVE_ONLY failures as unavailable and stale rather than demo", async () => {
    const unavailable = renderMarket(<><InjectMarketState errors={{ quote: "broker offline" }} /><DataSourceBadge /></>);
    expect(await screen.findByLabelText(/Live data unavailable: broker offline/i)).toBeInTheDocument();
    unavailable.unmount();

    const stale = marketContext("upstox", { is_stale: true, is_live: false });
    renderMarket(<><InjectMarketState contexts={{ quote: stale }} /><DataSourceBadge /></>);
    expect(await screen.findByLabelText(/Stale: upstox/i)).toBeInTheDocument();
    expect(screen.queryByText(/Demo: upstox/i)).not.toBeInTheDocument();
  });

  it("visibly isolates mixed providers instead of presenting a coherent source", async () => {
    const upstox = marketContext("upstox");
    const yfinance = marketContext("yfinance", { is_live: false, request_id: "yf-request" });
    renderMarket(
      <>
        <InjectMarketState contexts={{ quote: upstox, history: yfinance }} />
        <DataSourceBadge />
        <MixedSourceNotice />
      </>,
    );

    expect(await screen.findByLabelText(/Mixed sources: upstox \+ yfinance/i)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/Each panel is isolated and labeled with its own source/i);
  });
});
