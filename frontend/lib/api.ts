// Empty by default so every REST call is same-origin and is proxied to the
// backend by the Next.js rewrite in next.config.ts. Same-origin requests always
// carry the host-only session cookie, which removes the localhost vs 127.0.0.1
// mismatch that used to sign users out immediately after a successful login.
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";
export const API_ORIGIN = process.env.NEXT_PUBLIC_API_ORIGIN || "http://127.0.0.1:8000";

/** WebSocket origin. Reuses the page hostname so the browser never crosses hosts. */
export function wsBase(): string {
  const configured = process.env.NEXT_PUBLIC_WS_BASE;
  if (configured) return configured;
  if (typeof window === "undefined") return API_ORIGIN.replace(/^http/, "ws");
  const port = new URL(API_ORIGIN).port || (window.location.protocol === "https:" ? "443" : "80");
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.hostname}:${port}`;
}

export const WS_BASE = process.env.NEXT_PUBLIC_WS_BASE || API_ORIGIN.replace(/^http/, "ws");

/** Error carrying the HTTP status and the backend's stable error code. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly supportId: string | null;
  readonly retryable: boolean;

  constructor(message: string, status: number, code: string | null, supportId: string | null, retryable: boolean) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.supportId = supportId;
    this.retryable = retryable;
  }
}

export type MarketDataContext = {
  requested_symbol: string;
  resolved_instrument_key: string | null;
  exchange: string | null;
  instrument_type: string | null;
  provider: string;
  credential_mode: string;
  timeframe: string;
  as_of: string | null;
  received_at: string;
  is_live: boolean;
  is_stale: boolean;
  fallback_used: boolean;
  fallback_reason: string | null;
  request_id: string;
  provider_mode?: "OFFLINE_DEMO" | "FALLBACK_ALLOWED" | "LIVE_ONLY";
};

export type Candle = { time: string; open: number; high: number; low: number; close: number; volume: number };
export type Quote = { symbol: string; price: number; change?: number | null; change_pct?: number | null; source: string; timestamp: string; is_stale?: boolean; depth?: { bids?: [number, number][]; asks?: [number, number][] } | null; context?: MarketDataContext };
export type ResearchBand = { label: string; low: number; high: number; description: string };
export type ForecastSupportState = "model_supported" | "baseline_only" | "low_evidence" | "drift_blocked" | "data_quality_blocked" | "abstained";
export type TrustCheck = { status: "strong" | "limited" | "stale" | "blocked"; summary: string };
export type ProbabilityReport = { up: number; down: number; basis: string };
export type RegimeReport = {
  available: boolean;
  label?: string | null;
  trend?: string | null;
  volatility?: string | null;
  volatility_percentile?: number | null;
  trend_score?: number | null;
  adx?: number | null;
  signals?: string[];
  reason?: string | null;
  basis?: string | null;
};
export type ModelAgreementReport = {
  available: boolean;
  score?: number | null;
  level?: "high" | "moderate" | "low" | null;
  dispersion_pct?: number | null;
  member_count?: number | null;
  reason?: string | null;
  basis?: string | null;
};
export type ConfidenceScoreReport = {
  score: number;
  level: "high" | "moderate" | "low";
  components: { evidence: number; calibration: number; skill: number; agreement: number; data_quality: number };
  basis: string;
};
export type DataQualityReport = { score: number; level: "strong" | "limited" | "blocked"; notes: string[]; basis: string };
export type ExplanationBlock = { available: boolean; reasons: string[]; summary: string };
export type HorizonConsistencyReport = {
  available: boolean;
  score?: number | null;
  level: "high" | "moderate" | "low" | "unavailable";
  signal: "supportive" | "caution" | "weak" | "insufficient_horizons";
  summary: string;
  directional_agreement?: boolean;
  median_path_monotonic?: boolean;
  uncertainty_width_monotonic?: boolean;
  flags: string[];
  horizons_compared?: number[];
  basis: string;
};
export type ForecastAssessment = {
  probability?: ProbabilityReport | null;
  expected_return_pct?: number | null;
  expected_volatility_pct?: number | null;
  market_regime?: RegimeReport | null;
  model_agreement?: ModelAgreementReport | null;
  confidence_score?: ConfidenceScoreReport | null;
  data_quality?: DataQualityReport | null;
  explanation?: ExplanationBlock | null;
};

export type PayoffLeg = {
  index: number;
  type: "call" | "put" | "future" | "equity";
  side: "buy" | "sell";
  strike: number | null;
  premium: number;
  quantity: number;
  lot_size: number;
  contracts: number;
  label: string;
};

export type PayoffMargin = {
  state: "available" | "unavailable";
  reason?: string;
  span_margin?: number;
  exposure_margin?: number;
  total_margin?: number;
  approximate_margin?: boolean;
  note?: string;
};

export type PayoffLivePnl = {
  state: "available" | "unavailable";
  pnl?: number;
  basis?: string;
  missing_legs?: number[];
};

export type PayoffValuation = {
  state: "not_requested" | "available" | "unavailable";
  model?: string;
  label?: string;
  assumptions?: { volatility: number; days_to_expiry: number; risk_free_rate: number; dividend_yield: number };
  net_model_value?: number;
  legs?: { leg: number; state: string; theoretical_price: number; supplied_premium: number; delta?: number; theta_per_day?: number; vega_per_vol_point?: number }[];
  is_predictive?: boolean;
  reason?: string;
};

export type PayoffTails = {
  upside_slope_per_point: number;
  downside_slope_per_point: number;
  profit_unbounded: boolean;
  loss_unbounded: boolean;
  downside_bounded_by_zero: boolean;
  reference_spot: number;
};

export type PayoffGrid = { low: number; high: number; points: number; span: number };

export type PayoffCurvePoint = { price: number; payoff: number };

export type PayoffMaxProfit = {
  unbounded: boolean;
  value: number | null;
  at_price: number | null;
  outside_plotted_range: boolean;
  note: string;
};

export type PayoffMaxLoss = {
  unbounded: boolean;
  value: number | null;
  at_price: number | null;
  outside_plotted_range: boolean;
  note: string;
};

export type PayoffResponse = {
  underlying: string | null;
  analysis_label: string;
  legs: PayoffLeg[];
  spot: number;
  net_premium: number;
  position: "debit" | "credit" | "flat";
  costs_applied: number;
  payoff_at_spot: number;
  max_profit: PayoffMaxProfit;
  max_loss: PayoffMaxLoss;
  risk_reward_ratio: number | null;
  breakevens: number[];
  grid: PayoffGrid;
  curve: PayoffCurvePoint[];
  tails: PayoffTails;
  valuation: PayoffValuation;
  is_forecast: boolean;
  is_recommendation: boolean;
  margin: PayoffMargin;
  disclosures: string[];
  live_pnl?: PayoffLivePnl;
  margin_unavailable_reason?: string;
};

export type StrategyLabResponse = {
  underlying: string | null;
  analysis_label: string;
  legs: PayoffLeg[];
  spot: number;
  net_premium: number;
  position: "debit" | "credit" | "flat";
  costs_applied: number;
  payoff_at_spot: number;
  max_profit: PayoffMaxProfit;
  max_loss: PayoffMaxLoss;
  risk_reward_ratio: number | null;
  breakevens: number[];
  grid: PayoffGrid;
  curve: PayoffCurvePoint[];
  tails: PayoffTails;
  valuation: PayoffValuation;
  is_forecast: boolean;
  is_recommendation: boolean;
  margin: PayoffMargin;
  disclosures: string[];
  live_pnl?: PayoffLivePnl;
  margin_unavailable_reason?: string;
};

export type Forecast = {
  symbol: string;
  title: string;
  model_label: string;
  research_range?: {
    low: number;
    median_reference: number;
    high: number;
    confidence_level: number;
    confidence_label?: string | null;
    currency?: string;
  } | null;
  reference_price: number;
  target_timestamp?: string | null;
  horizon?: { bars: number; timeframe: string; label: string } | null;
  horizons?: {
    sessions: number;
    label: string;
    target_timestamp?: string | null;
    forecast_status?: string;
    abstained?: boolean;
    abstention_reason?: string | null;
    forecast?: {
      low: number;
      median_reference: number;
      high: number;
      confidence_level: number;
      confidence_label?: string | null;
      currency?: string;
    } | null;
    low_utility?: boolean;
    width_pct?: number | null;
  }[];
  horizon_consistency?: HorizonConsistencyReport | null;
  unavailable_horizons?: { sessions: number; reason?: string }[];
  evidence?: { grade: string; summary: string };
  trust?: { data?: TrustCheck; model?: TrustCheck; market_regime?: TrustCheck; liquidity?: TrustCheck };
  confidence: { level: "low" | "moderate" | "high"; summary: string };
  uncertainty: { range_width: number; range_width_pct: number; band: string; summary: string };
  observation_zone?: ResearchBand | null;
  risk_zone?: ResearchBand | null;
  zones_unavailable_reason?: string | null;
  scenarios: ResearchBand[];
  generated_at?: string | null;
  disclaimer: string;
  provenance?: {
    source?: string;
    as_of?: string | null;
    is_stale?: boolean;
    is_live?: boolean;
    market_state?: string | null;
    bars_used?: number;
  };
  context?: MarketDataContext;
  admin_detail?: Record<string, unknown>;
  support_state?: ForecastSupportState;
  forecast_status?: ForecastSupportState;
  forecast_state?: ForecastSupportState;
  model_supported?: boolean;
  baseline_only?: boolean;
  low_evidence?: boolean;
  abstained?: boolean;
  abstention_reason?: string | null;
  low_data?: boolean;
  low_data_branch?: string | null;
  expected_move?: ExpectedMoveBlock | null;
  assessment?: ForecastAssessment | null;
  cqr_canary_status?: CQRCanaryStatus | null;
};

export type CQRCanaryStatus = {
  enabled: boolean;
  receipt_valid: boolean;
  status: "promoted_canary" | "disabled_pending_real_promotion_gate" | "control_arm" | "unavailable";
  canary_percentage: number;
  canary_bucket: number;
  canary_assigned: boolean;
  config_path: string;
  determines_published_bounds: boolean;
  required_gate_fields?: string[];
  reason?: string;
  note?: string;
};

export type MarginEstimate = {
  total_margin: number;
  basis: string;
  valuation_basis?: string;
  scenarios_evaluated: number;
  short_premium_floor: number;
  sum_standalone: number;
  strategy_offset: number;
  worst_scenario: {
    underlying_move_pct: number;
    volatility_change_pct: number;
    portfolio_value?: number;
    loss: number;
  };
  per_leg: {
    index: number;
    label: string;
    side: string;
    type: string;
    standalone_margin: number;
    derivation: string;
  }[];
  is_official_requirement: boolean;
  is_forecast: boolean;
  disclosures: string[];
};

export type ExpectedMoveEntry = { sigma: number; low: number; high: number; move_pct: number };
export type ModelCrossover = {
  flag: "aligned" | "model_tighter_than_implied" | "model_wider_than_implied";
  message: string;
  model_range_width: number;
  implied_1sigma_width: number;
  implied_2sigma_width: number;
  model_vs_1sigma_ratio: number;
};
export type ExpectedMoveBlock =
  | { available: false; reason?: string | null; message?: string | null }
  | {
      available: true;
      underlying?: string | null;
      expiry?: string | null;
      days_to_expiry?: number | null;
      source?: string | null;
      fetched_at?: string | null;
      spot_price?: number | null;
      atm_iv?: number | null;
      expected_moves?: ExpectedMoveEntry[];
      model_crossover?: ModelCrossover | null;
      basis?: string | null;
      disclosure?: string | null;
    };

export type QualityGroup = {
  symbol: string;
  timeframe: string;
  horizon: number;
  settled_samples: number;
  state: string;
  rolling_coverage?: number | null;
  nominal_coverage?: number | null;
  coverage_gap?: number | null;
  mase?: number | null;
  directional_accuracy?: number | null;
  drift_detected: boolean;
  drift_reasons?: string[];
  severity?: string | null;
};
export type ModelHealthRecord = {
  id: number;
  symbol: string;
  timeframe: string;
  horizon: number;
  settled_samples: number;
  rolling_coverage?: number | null;
  nominal_coverage?: number | null;
  coverage_gap?: number | null;
  mase?: number | null;
  drift_detected: number | boolean;
  drift_reasons_json?: string;
  severity?: string | null;
  action_taken?: string | null;
  evaluated_at?: string | null;
};
export type ModelQuality = {
  dashboard: { groups: QualityGroup[]; group_count: number; drift_active_groups: number; last_evaluated: string };
  health_records: ModelHealthRecord[];
  last_refreshed: Record<string, { last_refreshed: string; trigger: string; artifact: string }>;
  scope: Record<string, unknown>;
  disclaimer?: string;
};

/** Admin-only model-health insight: drift + auto-adaptation + last model refresh. */
export function fetchModelQuality(symbol?: string): Promise<ModelQuality> {
  return api<ModelQuality>(`/api/v1/predictions/quality${symbol ? `?symbol=${encodeURIComponent(symbol)}` : ""}`);
}

type ForecastJobResponse = {
  id?: string;
  job_id?: string;
  status?: string;
  state?: string;
  status_url?: string;
  poll_url?: string;
  retry_after_ms?: number;
  result?: Forecast | { forecast?: Forecast; prediction?: Forecast };
  forecast?: Forecast;
  prediction?: Forecast;
  error?: string | { message?: string; code?: string; support_id?: string; retryable?: boolean };
  detail?: string;
};

export function formatApiError(reason: unknown): string {
  if (reason instanceof ApiError) {
    const actions: Record<string, string> = {
      provider_not_configured: "Open System Status to configure a history-capable provider.",
      provider_auth_expired: "Reauthorize the configured broker provider, then test the connection.",
      provider_rate_limited: "Wait briefly, then retry after the provider limit clears.",
      history_unavailable: "Check provider status or choose an explicitly labelled fallback mode.",
      history_range_unsupported: "Choose a supported timeframe or shorter training window.",
      insufficient_history: "Use a longer history window or accept that no reliable range is available.",
      data_quality_failed: "Review freshness and data-quality details before retrying.",
      forecast_timed_out: "The bounded job ended. Retry once or ask an administrator to inspect job diagnostics.",
      forecast_job_failed: "Retry once; use the support ID if the failure repeats.",
      instrument_forecast_unsupported: "Open Derivatives for transparent contract scenarios instead of an equity-style prediction.",
    };
    const action = reason.code ? actions[reason.code] : undefined;
    return `${reason.message}${action ? ` ${action}` : ""}${reason.supportId ? ` Support ID: ${reason.supportId}.` : ""}`;
  }
  return reason instanceof Error ? reason.message : "The request could not be completed.";
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init.headers || {}) },
    cache: "no-store"
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    let code: string | null = null;
    let supportId: string | null = null;
    let retryable = response.status >= 500 || response.status === 429;
    try {
      const body = await response.json();
      const detail = body?.detail;
      if (typeof detail === "string") {
        message = detail;
      } else if (detail && typeof detail === "object") {
        message = detail.message || message;
        code = detail.code ?? null;
        supportId = detail.support_id ?? null;
        if (typeof detail.retryable === "boolean") retryable = detail.retryable;
      }
    } catch {}
    throw new ApiError(message, response.status, code, supportId, retryable);
  }
  return response.json() as Promise<T>;
}

function forecastFromJob(payload: ForecastJobResponse): Forecast | undefined {
  if (payload.forecast) return payload.forecast;
  if (payload.prediction) return payload.prediction;
  if (payload.result && "symbol" in payload.result) return payload.result as Forecast;
  if (payload.result && "forecast" in payload.result) return payload.result.forecast || payload.result.prediction;
  if ("symbol" in payload && ("research_range" in payload || (payload as ForecastJobResponse & Partial<Forecast>).abstained)) return payload as ForecastJobResponse & Forecast;
  return undefined;
}

function waitForPoll(milliseconds: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("The request was cancelled.", "AbortError"));
      return;
    }
    const timer = setTimeout(resolve, milliseconds);
    signal?.addEventListener("abort", () => {
      clearTimeout(timer);
      reject(new DOMException("The request was cancelled.", "AbortError"));
    }, { once: true });
  });
}

/** Prefer the asynchronous forecast contract and use the legacy route on older deployments. */
export async function requestForecast(
  symbol: string,
  trainingWindow: string,
  timeframe: string,
  confidence = 0.8,
  signal?: AbortSignal,
  onStatus?: (status: string) => void,
): Promise<Forecast> {
  const legacy = () => api<Forecast>(
    `/api/v1/predict/${encodeURIComponent(symbol)}?training_window=${trainingWindow}&timeframe=${timeframe}&confidence=${confidence}`,
    { signal },
  );

  let job: ForecastJobResponse;
  try {
    job = await api<ForecastJobResponse>("/api/v1/forecast-jobs", {
      method: "POST",
      body: JSON.stringify({ symbol, training_window: trainingWindow, timeframe, confidence }),
      signal,
    });
  } catch (reason) {
    if (reason instanceof ApiError && [401, 404, 405, 501].includes(reason.status)) return legacy();
    throw reason;
  }

  const immediate = forecastFromJob(job);
  if (immediate) return immediate;
  const jobId = job.job_id || job.id;
  if (!jobId) throw new Error("The forecast job response did not include a job ID.");
  const suppliedPollUrl = job.status_url || job.poll_url;
  const pollUrl = suppliedPollUrl?.startsWith("/api/") ? suppliedPollUrl : `/api/v1/forecast-jobs/${encodeURIComponent(jobId)}`;

  while (true) {
    const status = String(job.status || job.state || "queued").toLowerCase();
    onStatus?.(status);
    if (["failed", "error", "cancelled", "canceled", "expired"].includes(status)) {
      const structured = typeof job.error === "object" ? job.error : undefined;
      const message = typeof job.error === "string" ? job.error : structured?.message;
      throw new ApiError(
        message || job.detail || `Forecast job ${status}.`,
        structured?.code === "provider_rate_limited" ? 429 : 500,
        structured?.code || "forecast_job_failed",
        structured?.support_id || null,
        structured?.retryable ?? status !== "cancelled",
      );
    }
    const completed = forecastFromJob(job);
    if (completed) return completed;
    if (["completed", "complete", "succeeded", "success", "done"].includes(status)) {
      throw new Error("The forecast job completed without a result.");
    }
    await waitForPoll(Math.max(250, Math.min(job.retry_after_ms || 1000, 10_000)), signal);
    job = await api<ForecastJobResponse>(pollUrl, { signal });
  }
}
