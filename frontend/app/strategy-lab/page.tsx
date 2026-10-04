/**
 * Strategy Lab — multi-leg option structure builder and live mark-to-market.
 *
 * Render-only surface over three backend endpoints:
 *   - GET  /api/v1/derivatives/strategies/templates  (structure catalog)
 *   - POST /api/v1/derivatives/strategies/build      (legs + expiry payoff + SPAN-style margin)
 *   - POST /api/v1/derivatives/strategies/pnl        (revalue the built book at spot/IV/DTE)
 *
 * Nothing here recommends a trade, forecasts a price, or places an order. Every
 * number is arithmetic or a theoretical model value on the user's own inputs, and
 * the margin block is explicitly a non-official scenario estimate.
 */
"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { Boxes, Calculator, TrendingUp, Wallet } from "lucide-react";
import { PayoffChart, type PayoffExtreme, type PayoffResponse } from "@/components/PayoffChart";
import { MarginEstimateCard } from "@/components/MarginEstimateCard";
import { TerminalShell } from "@/components/TerminalShell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { api, formatApiError, type MarginEstimate, type PayoffMargin } from "@/lib/api";
import { inr } from "@/lib/utils";

type StrategyTemplate = {
  name?: string;
  label?: string;
  direction?: string;
  legs?: number;
  description?: string;
  params?: Record<string, string>;
};

type TemplatesResponse = {
  templates?: StrategyTemplate[];
  max_legs?: number;
  disclosures?: string[];
};

type BuiltLeg = {
  index?: number;
  type?: string;
  side?: string;
  strike?: number | null;
  premium?: number;
  quantity?: number;
  lot_size?: number;
  contracts?: number;
  label?: string | null;
  premium_basis?: string;
  days_to_expiry?: number;
};

/** `payoff` is the standard build_payoff() block for most templates, but calendar
 * templates return a leaner shape where max_profit/max_loss are plain numbers. */
type BuildPayoff = {
  curve?: Array<{ price: number; payoff: number }>;
  breakevens?: number[];
  payoff_at_spot?: number;
  net_premium?: number;
  position?: string;
  spot?: number;
  analysis_label?: string;
  label?: string;
  basis?: string;
  max_profit?: PayoffExtreme | number | null;
  max_loss?: PayoffExtreme | number | null;
};

type BuildResponse = {
  template?: string;
  label?: string;
  legs?: BuiltLeg[];
  premium_basis?: string[];
  metadata?: {
    spot?: number;
    params?: Record<string, number>;
    strike_step?: number;
    quantity?: number;
    lot_size?: number;
    volatility?: number | null;
    days_to_expiry?: number;
    all_model_priced?: boolean;
    all_user_supplied?: boolean;
  };
  payoff?: BuildPayoff | null;
  payoff_unavailable_reason?: string | null;
  margin?: MarginEstimate | null;
  margin_unavailable_reason?: string | null;
  disclosures?: string[];
};

type PnlLegResponse = {
  index?: number;
  label?: string;
  type?: string;
  side?: string;
  strike?: number;
  entry_premium?: number;
  current_mark?: number | null;
  mark_basis?: string;
  mark_unavailable_reason?: string | null;
  pnl?: number | null;
  contracts?: number;
};

type PnlResponse = {
  legs?: PnlLegResponse[];
  total_pnl?: number;
  total_pnl_basis?: string;
  spot_now?: number;
  volatility_now?: number | null;
  days_to_expiry_now?: number;
  disclaimer?: string;
};

function paramLabel(key: string) {
  return key.replace(/_/g, " ");
}

function netPremiumOf(legs: BuiltLeg[]): number {
  return legs.reduce(
    (total, leg) =>
      total +
      (leg.side === "buy" ? 1 : -1) * Number(leg.premium ?? 0) * Number(leg.contracts ?? 0),
    0
  );
}

function asExtreme(raw: PayoffExtreme | number | null | undefined, spot: number): PayoffExtreme {
  if (typeof raw === "number" && Number.isFinite(raw)) {
    return { unbounded: false, value: raw, at_price: spot };
  }
  if (raw && typeof raw === "object") return raw;
  return { unbounded: false, value: null, at_price: null };
}

/** Normalise either payoff shape into the block PayoffChart consumes. */
function chartResponseFor(build: BuildResponse): PayoffResponse | null {
  const raw = build.payoff;
  if (!raw || !Array.isArray(raw.curve) || raw.curve.length === 0) return null;
  const spot = Number(build.metadata?.spot ?? raw.spot ?? 0);
  const net = netPremiumOf(build.legs ?? []);
  return {
    spot,
    curve: raw.curve,
    breakevens: Array.isArray(raw.breakevens) ? raw.breakevens : [],
    payoff_at_spot: raw.payoff_at_spot,
    max_profit: asExtreme(raw.max_profit, spot),
    max_loss: asExtreme(raw.max_loss, spot),
    net_premium: typeof raw.net_premium === "number" ? raw.net_premium : net,
    position: typeof raw.position === "string" ? raw.position : net > 0 ? "debit" : net < 0 ? "credit" : "flat",
    analysis_label: raw.analysis_label ?? raw.label ?? "Expiry payoff arithmetic on the supplied legs",
    is_forecast: false,
    is_recommendation: false,
margin: (build.margin ?? undefined) as PayoffResponse["margin"],
      margin_unavailable_reason: build.margin_unavailable_reason ?? undefined,
    disclosures: build.disclosures,
  };
}

function Metric({ label, value, tone = "" }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-lg border border-slate-800 p-3">
      <div className="text-[10px] uppercase tracking-wider text-slate-600">{label}</div>
      <div className={`mt-1 tabular text-sm ${tone || "text-white"}`}>{value}</div>
    </div>
  );
}

export default function StrategyLab() {
  const [templates, setTemplates] = useState<StrategyTemplate[]>([]);
  const [templatesErr, setTemplatesErr] = useState("");
  const [catalogLoading, setCatalogLoading] = useState(false);
  const templatesAbort = useRef<AbortController | null>(null);

  const [template, setTemplate] = useState("");
  const [spot, setSpot] = useState("");
  const [vol, setVol] = useState("");
  const [dte, setDte] = useState("30");
  const [quantity, setQuantity] = useState("1");
  const [lotSize, setLotSize] = useState("50");
  const [rate, setRate] = useState("6.5");
  const [costs, setCosts] = useState("");
  const [params, setParams] = useState<Record<string, string>>({});
  const [build, setBuild] = useState<BuildResponse | null>(null);
  const [buildErr, setBuildErr] = useState("");
  const [buildLoading, setBuildLoading] = useState(false);

  const [spotNow, setSpotNow] = useState("");
  const [volNow, setVolNow] = useState("");
  const [marks, setMarks] = useState<Record<string, string>>({});
  const [pnl, setPnl] = useState<PnlResponse | null>(null);
  const [pnlErr, setPnlErr] = useState("");
  const [pnlLoading, setPnlLoading] = useState(false);

  const selected = templates.find(item => item.name === template);
  const paramKeys = Object.keys(selected?.params ?? {});
  const builtLegs = build?.legs ?? [];
  const chart = build ? chartResponseFor(build) : null;

  useEffect(() => {
    templatesAbort.current?.abort();
    const controller = new AbortController();
    templatesAbort.current = controller;
    setCatalogLoading(true);
    setTemplatesErr("");
    api<TemplatesResponse>("/api/v1/derivatives/strategies/templates", { signal: controller.signal })
      .then(result => {
        if (controller.signal.aborted) return;
        const items = Array.isArray(result?.templates) ? result.templates.filter(item => item?.name) : [];
        setTemplates(items);
        if (items.length) setTemplate(current => current || String(items[0].name));
      })
      .catch(error => {
        if (!controller.signal.aborted) setTemplatesErr(formatApiError(error));
      })
      .finally(() => {
        if (templatesAbort.current === controller) setCatalogLoading(false);
      });
    return () => controller.abort();
  }, []);

  function changeParam(key: string, value: string) {
    setParams(current => ({ ...current, [key]: value }));
  }

  async function runBuild(event: FormEvent) {
    event.preventDefault();
    setBuildErr("");
    setBuildLoading(true);
    setBuild(null);
    setPnl(null);
    setMarks({});
    try {
      const resolvedParams: Record<string, number> = {};
      for (const [key, raw] of Object.entries(params)) {
        const parsed = Number(raw);
        if (raw !== "" && Number.isFinite(parsed)) resolvedParams[key] = parsed;
      }
      const body: Record<string, unknown> = {
        template,
        spot: Number(spot),
        days_to_expiry: dte === "" ? undefined : Number(dte),
        quantity: quantity === "" ? undefined : Number(quantity),
        lot_size: lotSize === "" ? undefined : Number(lotSize),
        risk_free_rate: rate === "" ? undefined : Number(rate) / 100,
        costs: costs === "" ? undefined : Number(costs),
      };
      if (vol !== "") body.volatility = Number(vol) / 100;
      if (Object.keys(resolvedParams).length) body.params = resolvedParams;
      const result = await api<BuildResponse>("/api/v1/derivatives/strategies/build", {
        method: "POST",
        body: JSON.stringify(body),
      });
      setBuild(result);
    } catch (error) {
      setBuildErr(formatApiError(error));
    } finally {
      setBuildLoading(false);
    }
  }

  async function runPnl(event: FormEvent) {
    event.preventDefault();
    setPnlErr("");
    setPnlLoading(true);
    setPnl(null);
    try {
      const legs = builtLegs.map((leg, position) => {
        const entry: Record<string, unknown> = {
          type: leg.type ?? "call",
          side: leg.side ?? "buy",
          strike: Number(leg.strike ?? 0),
          premium: Number(leg.premium ?? 0),
          quantity: Number(leg.quantity ?? 1),
          lot_size: Number(leg.lot_size ?? 1),
        };
        if (typeof leg.days_to_expiry === "number") entry.days_to_expiry = leg.days_to_expiry;
        if (leg.label) entry.label = leg.label;
        const mark = marks[String(position)] ?? marks[String(leg.index ?? position)];
        if (mark !== undefined && mark !== "") entry.mark_price = Number(mark);
        return entry;
      });
      const body: Record<string, unknown> = {
        legs,
        spot_now: Number(spotNow),
        days_to_expiry_now: dte === "" ? 0 : Number(dte),
      };
      if (volNow !== "") body.volatility_now = Number(volNow) / 100;
      if (rate !== "") body.risk_free_rate = Number(rate) / 100;
      setPnl(
        await api<PnlResponse>("/api/v1/derivatives/strategies/pnl", {
          method: "POST",
          body: JSON.stringify(body),
        })
      );
    } catch (error) {
      setPnlErr(formatApiError(error));
    } finally {
      setPnlLoading(false);
    }
  }

  return (
    <TerminalShell>
      <div className="mb-5">
        <div className="flex items-center gap-2 text-secondary">
          <Boxes aria-hidden="true" size={17} />
          <span className="text-[10px] uppercase tracking-[.2em]">Multi-leg option builder</span>
        </div>
        <h1 className="mt-1 text-2xl font-semibold text-white">Strategy Lab</h1>
        <p className="mt-1 text-sm text-slate-500">
          Build a named option structure from your own inputs, read its expiry payoff and breakevens, then
          revalue it against a new spot or volatility. Paper analysis only.
        </p>
      </div>

      <div className="rounded-lg border border-info/25 bg-info/5 p-3 text-xs leading-5 text-info">
        <b>Analytical arithmetic, not a forecast.</b> Every payoff, P&amp;L and margin figure is computed from
        the values you supply. Model-priced premiums are theoretical Black-Scholes values, not quotes. Margin is a
        SPAN-style scenario estimate, never an exchange requirement.
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[420px_1fr]">
        <Card className="panel-glow">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Boxes aria-hidden="true" size={15} /> Structure
            </CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={runBuild} className="space-y-3">
              <label className="block text-xs text-slate-400">
                Template
                <Select
                  aria-label="Strategy template"
                  className="mt-1"
                  required
                  value={template}
                  onChange={event => {
                    setTemplate(event.target.value);
                    setParams({});
                    setBuild(null);
                    setPnl(null);
                  }}
                >
                  <option value="">{catalogLoading ? "Loading catalog…" : "Select a structure"}</option>
                  {templates.map(item => (
                    <option key={item.name} value={item.name}>
                      {item.label ?? item.name}
                    </option>
                  ))}
                </Select>
              </label>
              {selected?.description && (
                <p className="text-[11px] leading-4 text-slate-500">{selected.description}</p>
              )}

              <label className="block text-xs text-slate-400">
                Underlying spot
                <Input
                  type="number"
                  min="0.01"
                  step="any"
                  required
                  aria-label="Underlying spot"
                  className="mt-1"
                  value={spot}
                  onChange={event => setSpot(event.target.value)}
                />
              </label>

              <div className="grid grid-cols-2 gap-2">
                <label className="text-xs text-slate-400">
                  Volatility %
                  <Input
                    type="number"
                    min="0.01"
                    step="any"
                    aria-label="Volatility percent"
                    className="mt-1"
                    value={vol}
                    onChange={event => setVol(event.target.value)}
                  />
                </label>
                <label className="text-xs text-slate-400">
                  Days to expiry
                  <Input
                    type="number"
                    min="0"
                    max="730"
                    step="1"
                    aria-label="Days to expiry"
                    className="mt-1"
                    value={dte}
                    onChange={event => setDte(event.target.value)}
                  />
                </label>
              </div>
              <p className="text-[10px] leading-4 text-slate-500">
                Supplying volatility lets the builder price each leg and produce a margin scan. Leave it blank to
                see the reason margin is unavailable.
              </p>

              <div className="grid grid-cols-2 gap-2">
                <label className="text-xs text-slate-400">
                  Contracts per leg
                  <Input
                    type="number"
                    min="0.01"
                    step="any"
                    aria-label="Contracts per leg"
                    className="mt-1"
                    value={quantity}
                    onChange={event => setQuantity(event.target.value)}
                  />
                </label>
                <label className="text-xs text-slate-400">
                  Lot size
                  <Input
                    type="number"
                    min="1"
                    step="1"
                    aria-label="Lot size"
                    className="mt-1"
                    value={lotSize}
                    onChange={event => setLotSize(event.target.value)}
                  />
                </label>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <label className="text-xs text-slate-400">
                  Risk-free rate %
                  <Input
                    type="number"
                    step="any"
                    aria-label="Risk-free rate percent"
                    className="mt-1"
                    value={rate}
                    onChange={event => setRate(event.target.value)}
                  />
                </label>
                <label className="text-xs text-slate-400">
                  Costs
                  <Input
                    type="number"
                    min="0"
                    step="any"
                    aria-label="Transaction costs"
                    className="mt-1"
                    value={costs}
                    onChange={event => setCosts(event.target.value)}
                  />
                </label>
              </div>

              {paramKeys.length > 0 && (
                <fieldset className="space-y-2 rounded-lg border border-slate-800 p-3">
                  <legend className="px-1 text-[10px] uppercase tracking-wider text-slate-500">
                    Structure inputs
                  </legend>
                  <p className="text-[10px] leading-4 text-slate-500">
                    Leave blank to use the template default.
                  </p>
                  {paramKeys.map(key => (
                    <label key={key} className="block text-xs text-slate-400">
                      {paramLabel(key)}
                      <Input
                        type="number"
                        step="any"
                        aria-label={`Template input ${paramLabel(key)}`}
                        className="mt-1"
                        value={params[key] ?? ""}
                        onChange={event => changeParam(key, event.target.value)}
                      />
                      {selected?.params?.[key] && (
                        <span className="mt-1 block text-[10px] leading-4 text-slate-500">{selected.params[key]}</span>
                      )}
                    </label>
                  ))}
                </fieldset>
              )}

              <Button type="submit" disabled={buildLoading || !template} className="w-full gap-2">
                <Calculator aria-hidden="true" size={14} />
                {buildLoading ? "Building…" : "Build structure"}
              </Button>
            </form>
            {buildErr && (
              <p role="alert" className="mt-2 text-xs text-loss">
                {buildErr}
              </p>
            )}
            {templatesErr && (
              <p role="alert" className="mt-2 text-xs text-loss">
                {templatesErr}
              </p>
            )}
          </CardContent>
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Payoff at expiry</CardTitle>
            </CardHeader>
            <CardContent>
              {chart ? (
                <PayoffChart data={chart} />
              ) : build?.payoff_unavailable_reason ? (
                <p role="status" className="text-sm text-slate-500">
                  No payoff curve: {build.payoff_unavailable_reason}
                </p>
              ) : (
                <div className="grid min-h-52 place-items-center text-center text-sm text-slate-600">
                  {build
                    ? "This structure produced no plottable curve."
                    : "Pick a template and supply spot to plot the expiry payoff."}
                </div>
              )}
            </CardContent>
          </Card>

          {build && (
            <Card>
              <CardHeader>
                <CardTitle>Legs</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[620px] text-left text-xs">
                    <caption className="sr-only">Legs of the built structure</caption>
                    <thead className="text-[10px] uppercase text-slate-600">
                      <tr>
                        <th scope="col" className="py-2">
                          Leg
                        </th>
                        <th scope="col">Side</th>
                        <th scope="col">Strike</th>
                        <th scope="col">Premium</th>
                        <th scope="col">Contracts</th>
                        <th scope="col">DTE</th>
                        <th scope="col">Basis</th>
                      </tr>
                    </thead>
                    <tbody>
                      {builtLegs.map((leg, position) => (
                        <tr key={`leg-${position}`} className="border-t border-slate-800">
                          <td className="py-2 font-medium text-white">{leg.label ?? `Leg ${position + 1}`}</td>
                          <td className={leg.side === "sell" ? "text-loss" : "text-gain"}>{leg.side}</td>
                          <td className="tabular text-slate-200">
                            {leg.strike == null ? "—" : Number(leg.strike).toFixed(2)}
                          </td>
                          <td className="tabular text-slate-200">{inr(Number(leg.premium ?? 0))}</td>
                          <td className="tabular text-slate-300">{Number(leg.contracts ?? 0)}</td>
                          <td className="tabular text-slate-400">
                            {leg.days_to_expiry == null ? "—" : `${leg.days_to_expiry}d`}
                          </td>
                          <td className="text-[10px] text-slate-500">
                            {leg.premium_basis === "user" ? "your input" : "model value"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="mt-4 grid gap-2 sm:grid-cols-3">
                  <Metric
                    label="Net premium"
                    value={inr(netPremiumOf(builtLegs))}
                    tone={netPremiumOf(builtLegs) >= 0 ? "text-loss" : "text-gain"}
                  />
                  <Metric label="Breakevens" value={(chart?.breakevens ?? []).length ? (chart?.breakevens ?? []).map(value => value.toFixed(2)).join(" / ") : "—"} />
                  <Metric
                    label="Spot reference"
                    value={build.metadata?.spot == null ? "—" : String(build.metadata.spot)}
                  />
                </div>
                {Array.isArray(build.disclosures) && build.disclosures.length > 0 && (
                  <ul className="mt-4 space-y-1 border-t border-slate-800 pt-2 text-[10px] leading-4 text-slate-500">
                    {build.disclosures.map(line => (
                      <li key={line}>{line}</li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
          )}

          {build && (
            <Card>
              <CardHeader>
                <CardTitle>Margin estimate</CardTitle>
              </CardHeader>
              <CardContent>
                {build.margin ? (
                  <MarginEstimateCard
                    margin={build.margin as PayoffMargin | MarginEstimate}
                    disclosure="Margin estimates are approximate and non-official; this application does not hold broker SPAN/ELM risk parameters."
                  />
                ) : <p className="text-sm text-warning">No margin estimate: {build.margin_unavailable_reason || "not supplied"}</p>}
              </CardContent>
            </Card>
          )}
        </div>
      </div>

      {build && (
        <Card className="mt-4">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Wallet aria-hidden="true" size={15} /> Revalue this structure
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="mb-3 text-xs leading-5 text-slate-500">
              Value the built legs at a different spot or volatility. Supply a mark for any leg you are quoting, or
              let the model price it. Legs with neither are reported as unavailable rather than guessed.
            </p>
            <form onSubmit={runPnl} className="space-y-3">
              <div className="grid gap-2 sm:grid-cols-3">
                <label className="text-xs text-slate-400">
                  Spot now
                  <Input
                    type="number"
                    min="0.01"
                    step="any"
                    required
                    aria-label="Spot now"
                    className="mt-1"
                    value={spotNow}
                    onChange={event => setSpotNow(event.target.value)}
                  />
                </label>
                <label className="text-xs text-slate-400">
                  Volatility now %
                  <Input
                    type="number"
                    min="0.01"
                    step="any"
                    aria-label="Volatility now percent"
                    className="mt-1"
                    value={volNow}
                    onChange={event => setVolNow(event.target.value)}
                  />
                </label>
                <label className="text-xs text-slate-400">
                  Days to expiry now
                  <Input
                    type="number"
                    min="0"
                    max="730"
                    step="1"
                    aria-label="Days to expiry now"
                    className="mt-1"
                    value={dte}
                    onChange={event => setDte(event.target.value)}
                  />
                </label>
              </div>
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                {builtLegs.map((leg, position) => (
                  <label key={`mark-${position}`} className="text-xs text-slate-400">
                    Mark · {leg.label ?? `Leg ${position + 1}`}
                    <Input
                      type="number"
                      min="0"
                      step="any"
                      aria-label={`Mark for ${leg.label ?? `leg ${position + 1}`}`}
                      className="mt-1"
                      value={marks[String(position)] ?? ""}
                      onChange={event =>
                        setMarks(current => ({ ...current, [String(position)]: event.target.value }))
                      }
                    />
                  </label>
                ))}
              </div>
              <Button type="submit" disabled={pnlLoading} className="gap-2">
                <TrendingUp aria-hidden="true" size={14} />
                {pnlLoading ? "Valuing…" : "Value position"}
              </Button>
            </form>
            {pnlErr && (
              <p role="alert" className="mt-2 text-xs text-loss">
                {pnlErr}
              </p>
            )}
            {pnl && (
              <div className="mt-4">
                <div className="flex flex-wrap items-end justify-between gap-3">
                  <div>
                    <div className="text-[10px] uppercase tracking-wider text-slate-600">Total P&amp;L</div>
                    <div className={`tabular mt-1 text-xl font-semibold ${Number(pnl.total_pnl ?? 0) >= 0 ? "text-gain" : "text-loss"}`}>
                      {inr(Number(pnl.total_pnl ?? 0))}
                    </div>
                  </div>
                  <div className="text-right text-[10px] text-slate-500">
                    <div>
                      {pnl.total_pnl_basis === "all_legs_valued"
                        ? "Every leg was valued"
                        : "Partial — some legs could not be valued"}
                    </div>
                    <div className="mt-1">
                      Spot {pnl.spot_now ?? "—"} · Volatility{" "}
                      {pnl.volatility_now == null ? "—" : `${(Number(pnl.volatility_now) * 100).toFixed(1)}%`} · DTE{" "}
                      {pnl.days_to_expiry_now ?? "—"}
                    </div>
                  </div>
                </div>
                <div className="mt-4 overflow-x-auto">
                  <table className="w-full min-w-[680px] text-left text-xs">
                    <caption className="sr-only">Mark to market P and L by leg</caption>
                    <thead className="text-[10px] uppercase text-slate-600">
                      <tr>
                        <th scope="col" className="py-2">
                          Leg
                        </th>
                        <th scope="col">Entry</th>
                        <th scope="col">Mark</th>
                        <th scope="col">Basis</th>
                        <th scope="col">Contracts</th>
                        <th scope="col">P&amp;L</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(pnl.legs ?? []).map((leg, position) => (
                        <tr key={`pnl-leg-${position}`} className="border-t border-slate-800">
                          <td className="py-2 font-medium text-white">{leg.label ?? `Leg ${position + 1}`}</td>
                          <td className="tabular text-slate-300">{inr(Number(leg.entry_premium ?? 0))}</td>
                          <td className="tabular text-slate-200">
                            {leg.current_mark == null ? "—" : Number(leg.current_mark).toFixed(4)}
                          </td>
                          <td className="text-[10px] text-slate-500">
                            {leg.mark_basis === "user_mark"
                              ? "your mark"
                              : leg.mark_basis === "model_mark"
                                ? "model value"
                                : "unavailable"}
                            {leg.mark_unavailable_reason && (
                              <span className="block text-[9px] text-slate-600">{leg.mark_unavailable_reason}</span>
                            )}
                          </td>
                          <td className="tabular text-slate-400">{Number(leg.contracts ?? 0)}</td>
                          <td className={`tabular ${Number(leg.pnl ?? 0) >= 0 ? "text-gain" : "text-loss"}`}>
                            {leg.pnl == null ? "—" : inr(Number(leg.pnl))}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {pnl.disclaimer && <p className="mt-3 text-[10px] leading-4 text-slate-500">{pnl.disclaimer}</p>}
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </TerminalShell>
  );
}
