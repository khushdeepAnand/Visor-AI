"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Megaphone, ShieldAlert, ToggleLeft, Trash2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { api, formatApiError } from "@/lib/api";

type KillSwitch = {
  id: number;
  scope: string;
  target: string;
  reason: string;
  created_by: string | null;
  created_at: string;
  expires_at: string;
  revoked_at: string | null;
  active: boolean;
  expired?: boolean;
};

type FeatureFlag = {
  name: string;
  description: string;
  default: boolean;
  enabled: boolean;
  rollout_percent: number;
  updated_by: string | null;
  updated_at: string | null;
  reason: string | null;
  auto_rolled_back_at: string | null;
  auto_rollback_detail: Record<string, unknown> | null;
};

type OperatorBanner = {
  id: number;
  level: string;
  headline: string;
  body: string;
  starts_at: string | null;
  ends_at: string | null;
  published?: boolean;
  published_by?: string | null;
  withdrawn_at?: string | null;
};

type OperationsPayload = {
  operations: {
    kill_switches: { scopes: string[]; asset_classes: string[]; max_hours: number; active: KillSwitch[] };
    feature_flags: FeatureFlag[];
    banners: { levels: string[]; max_hours: number; active: OperatorBanner[]; all: OperatorBanner[] };
  };
  step_up: {
    actions: string[];
    token_ttl_seconds: number;
    single_use: boolean;
    max_failed_attempts: number;
    failed_attempt_window_seconds: number;
    notes?: unknown;
  };
  low_history_model?: Record<string, unknown>;
  invalidatable_caches: string[];
};

type Challenge = {
  action: string;
  target: string | null;
  label: string;
  run: (token: string) => Promise<unknown>;
};

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wider text-slate-500">
      {label}
      {children}
    </label>
  );
}

const inputClass = "h-11 w-full rounded-md border border-slate-800 bg-terminal-950 px-3 text-xs text-slate-200 outline-none focus:border-accent/50";

/**
 * Operations and content controls for administrators.
 *
 * Every destructive control here is gated by a single-use step-up token, which
 * the operator mints by re-entering their password. Nothing in this panel
 * exposes user-owned portfolios, credentials, or raw provider keys.
 */
export function AdminOperationsPanel() {
  const qc = useQueryClient();
  const operations = useQuery({
    queryKey: ["admin-operations"],
    queryFn: () => api<OperationsPayload>("/api/v1/admin/operations"),
    retry: 1,
  });

  const [challenge, setChallenge] = useState<Challenge | null>(null);
  const [password, setPassword] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Kill switch form
  const [scope, setScope] = useState("symbol");
  const [target, setTarget] = useState("");
  const [switchReason, setSwitchReason] = useState("");
  const [switchHours, setSwitchHours] = useState(6);

  // Feature flag form
  const [flagReason, setFlagReason] = useState("");
  const [rollouts, setRollouts] = useState<Record<string, number>>({});

  // Banner form
  const [level, setLevel] = useState("maintenance");
  const [headline, setHeadline] = useState("");
  const [body, setBody] = useState("");
  const [bannerHours, setBannerHours] = useState(6);
  const [previewConfirmed, setPreviewConfirmed] = useState(false);

  // Cache form
  const [cache, setCache] = useState("forecast_cache");
  const [cacheReason, setCacheReason] = useState("");

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["admin-operations"] });
    qc.invalidateQueries({ queryKey: ["admin-audit"] });
  };

  const gated = (next: Challenge) => {
    setError(null);
    setNotice(null);
    setPassword("");
    setChallenge(next);
  };

  const confirmStepUp = useMutation({
    mutationFn: async () => {
      if (!challenge) return;
      const grant = await api<{ step_up_token: string }>("/api/v1/admin/step-up", {
        method: "POST",
        body: JSON.stringify({ password, action: challenge.action, target: challenge.target }),
      });
      await challenge.run(grant.step_up_token);
      return challenge.label;
    },
    onSuccess: (label) => {
      setNotice(`${label ?? "Action"} applied.`);
      setChallenge(null);
      setPassword("");
      refresh();
    },
    onError: (err) => setError(formatApiError(err)),
  });

  const draftBanner = useMutation({
    mutationFn: () =>
      api("/api/v1/admin/operations/banners", {
        method: "POST",
        body: JSON.stringify({ level, headline, body, ends_in_hours: bannerHours }),
      }),
    onSuccess: () => {
      setNotice("Banner drafted. It stays invisible to users until you publish it.");
      setHeadline("");
      setBody("");
      refresh();
    },
    onError: (err) => setError(formatApiError(err)),
  });

  const withdrawBanner = useMutation({
    mutationFn: (id: number) => api(`/api/v1/admin/operations/banners/${id}/withdraw`, { method: "POST" }),
    onSuccess: () => {
      setNotice("Banner withdrawn.");
      refresh();
    },
    onError: (err) => setError(formatApiError(err)),
  });

  const setFlag = useMutation({
    mutationFn: (input: { name: string; enabled: boolean; rollout: number }) =>
      api(`/api/v1/admin/operations/flags/${input.name}`, {
        method: "PUT",
        body: JSON.stringify({ enabled: input.enabled, rollout_percent: input.rollout, reason: flagReason }),
      }),
    onSuccess: () => {
      setNotice("Feature flag updated.");
      refresh();
    },
    onError: (err) => setError(formatApiError(err)),
  });

  if (operations.isError) {
    return (
      <Card className="mt-3">
        <CardHeader>
          <CardTitle>Operations and content</CardTitle>
        </CardHeader>
        <CardContent>
          <p role="alert" className="text-xs text-loss">
            {formatApiError(operations.error)}
          </p>
        </CardContent>
      </Card>
    );
  }

  const data = operations.data;
  const killSwitches = data?.operations?.kill_switches;
  const flags = data?.operations?.feature_flags ?? [];
  const banners = data?.operations?.banners;
  const drafts = (banners?.all ?? []).filter((item) => !item.published && !item.withdrawn_at);

  return (
    <section className="mt-5" aria-labelledby="ops-content-title">
      <div className="mb-3">
        <h2 id="ops-content-title" className="display-font text-lg font-semibold text-white">
          Operations and content
        </h2>
        <p className="mt-1 text-xs text-slate-500">
          Forecast kill switches, staged feature flags, status banners, and cache invalidation. Sensitive actions require a
          single-use password confirmation valid for {data?.step_up?.token_ttl_seconds ?? 300} seconds.
        </p>
      </div>

      {notice && (
        <p role="status" className="mb-3 rounded-lg border border-gain/30 bg-gain/5 p-3 text-xs text-gain">
          {notice}
        </p>
      )}
      {error && (
        <p role="alert" className="mb-3 rounded-lg border border-loss/30 bg-loss/5 p-3 text-xs text-loss">
          {error}
        </p>
      )}

      {challenge && (
        <Card className="mb-3 border-warning/40">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-warning">
              <KeyRound aria-hidden="true" size={14} /> Confirm: {challenge.label}
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2 sm:flex-row sm:items-end">
            <div className="flex-1">
              <Field label={`Password for ${challenge.action}`}>
                <input
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  className={inputClass}
                />
              </Field>
              <p className="mt-1 text-[10px] text-slate-500">
                Target: {challenge.target ?? "global"} · single use · re-confirmation is required for every action.
              </p>
            </div>
            <div className="flex gap-2">
              <Button disabled={confirmStepUp.isPending || password.length === 0} onClick={() => confirmStepUp.mutate()}>
                {confirmStepUp.isPending ? "Confirming…" : "Confirm"}
              </Button>
              <Button variant="ghost" onClick={() => { setChallenge(null); setPassword(""); }}>
                Cancel
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-3 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ShieldAlert aria-hidden="true" size={14} /> Forecast kill switches
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="mb-3 text-xs leading-5 text-slate-500">
              Pausing publication returns an explicit operator-paused response instead of a stale or unreviewed range.
              Maximum duration {killSwitches?.max_hours ?? 336} hours.
            </p>
            <div className="grid gap-2 sm:grid-cols-2">
              <Field label="Scope">
                <select value={scope} onChange={(event) => setScope(event.target.value)} className={inputClass}>
                  {(killSwitches?.scopes ?? ["global"]).map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label={scope === "global" ? "Target (not used for global)" : "Target"}>
                <input
                  value={target}
                  onChange={(event) => setTarget(event.target.value)}
                  disabled={scope === "global"}
                  placeholder={scope === "asset_class" ? (killSwitches?.asset_classes ?? []).join(" | ") : "e.g. RELIANCE"}
                  className={inputClass}
                />
              </Field>
              <Field label="Reason (recorded in the audit log)">
                <input value={switchReason} onChange={(event) => setSwitchReason(event.target.value)} className={inputClass} />
              </Field>
              <Field label="Expires in (hours)">
                <input
                  type="number"
                  min={1}
                  max={killSwitches?.max_hours ?? 336}
                  value={switchHours}
                  onChange={(event) => setSwitchHours(Number(event.target.value))}
                  className={inputClass}
                />
              </Field>
            </div>
            <Button
              className="mt-3"
              variant="danger"
              disabled={switchReason.trim().length === 0}
              onClick={() =>
                gated({
                  action: "forecast_kill_switch",
                  target: `${scope}:${scope === "global" ? "all" : target || "all"}`,
                  label: `Pause forecasts (${scope})`,
                  run: (token) =>
                    api("/api/v1/admin/operations/kill-switches", {
                      method: "POST",
                      headers: { "X-Step-Up-Token": token },
                      body: JSON.stringify({
                        scope,
                        target: scope === "global" ? null : target || null,
                        reason: switchReason,
                        expires_in_hours: switchHours,
                      }),
                    }),
                })
              }
            >
              Pause forecasts
            </Button>

            <ul className="mt-4 flex flex-col gap-2">
              {(killSwitches?.active ?? []).length === 0 && (
                <li className="text-xs text-slate-500">No active kill switch. Forecast publication is not paused.</li>
              )}
              {(killSwitches?.active ?? []).map((item) => (
                <li key={item.id} className="rounded-md border border-slate-800 p-2 text-xs">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-semibold text-slate-200">
                      {item.scope}: {item.target}
                    </span>
                    <Button
                      variant="ghost"
                      className="min-h-9 text-[11px]"
                      onClick={() =>
                        gated({
                          action: "forecast_kill_switch",
                          target: `revoke:${item.id}`,
                          label: `Resume forecasts (${item.scope}: ${item.target})`,
                          run: (token) =>
                            api(`/api/v1/admin/operations/kill-switches/${item.id}`, {
                              method: "DELETE",
                              headers: { "X-Step-Up-Token": token },
                              body: JSON.stringify({ reason: "Resumed from admin operations panel" }),
                            }),
                        })
                      }
                    >
                      Resume
                    </Button>
                  </div>
                  <p className="mt-1 text-slate-500">
                    {item.reason} · expires {new Date(item.expires_at).toLocaleString()} · created by {item.created_by ?? "unknown"}
                  </p>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ToggleLeft aria-hidden="true" size={14} /> Feature flags
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="mb-3 text-xs leading-5 text-slate-500">
              Staged rollout is percentage-based and deterministic per account, so a flagged capability reaches the same
              users on every request until you change it.
            </p>
            <Field label="Reason for the next change">
              <input value={flagReason} onChange={(event) => setFlagReason(event.target.value)} className={inputClass} />
            </Field>
            <ul className="mt-3 flex flex-col gap-2">
              {flags.map((flag) => {
                const rollout = rollouts[flag.name] ?? flag.rollout_percent;
                return (
                  <li key={flag.name} className="rounded-md border border-slate-800 p-2 text-xs">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-mono text-slate-200">{flag.name}</span>
                      <span className={flag.enabled ? "text-gain" : "text-slate-500"}>
                        {flag.enabled ? `enabled · ${flag.rollout_percent}%` : "disabled"}
                      </span>
                    </div>
                    <p className="mt-1 text-slate-500">{flag.description}</p>
                    {flag.auto_rolled_back_at && (
                      <p role="alert" className="mt-1 text-warning">
                        Auto rolled back at {new Date(flag.auto_rolled_back_at).toLocaleString()}
                      </p>
                    )}
                    <div className="mt-2 flex flex-wrap items-end gap-2">
                      <label className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-slate-500">
                        Rollout %
                        <input
                          type="number"
                          min={0}
                          max={100}
                          value={rollout}
                          onChange={(event) => setRollouts({ ...rollouts, [flag.name]: Number(event.target.value) })}
                          className="h-9 w-20 rounded-md border border-slate-800 bg-terminal-950 px-2 text-xs text-slate-200"
                        />
                      </label>
                      <Button
                        variant="ghost"
                        className="min-h-9 text-[11px]"
                        disabled={setFlag.isPending || flagReason.trim().length === 0}
                        onClick={() => setFlag.mutate({ name: flag.name, enabled: true, rollout })}
                      >
                        Enable
                      </Button>
                      <Button
                        variant="ghost"
                        className="min-h-9 text-[11px]"
                        disabled={setFlag.isPending || flagReason.trim().length === 0}
                        onClick={() => setFlag.mutate({ name: flag.name, enabled: false, rollout: 0 })}
                      >
                        Disable
                      </Button>
                    </div>
                  </li>
                );
              })}
              {flags.length === 0 && <li className="text-xs text-slate-500">No flags reported by this deployment.</li>}
            </ul>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Megaphone aria-hidden="true" size={14} /> Status banners
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="mb-3 text-xs leading-5 text-slate-500">
              Draft, preview, then publish. Publication requires step-up confirmation; withdrawal never does, so an
              incorrect banner can always be removed immediately.
            </p>
            <div className="grid gap-2 sm:grid-cols-2">
              <Field label="Level">
                <select value={level} onChange={(event) => setLevel(event.target.value)} className={inputClass}>
                  {(banners?.levels ?? ["info", "maintenance", "degraded", "outage"]).map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label={`Duration (hours, max ${banners?.max_hours ?? 168})`}>
                <input
                  type="number"
                  min={1}
                  max={banners?.max_hours ?? 168}
                  value={bannerHours}
                  onChange={(event) => setBannerHours(Number(event.target.value))}
                  className={inputClass}
                />
              </Field>
            </div>
            <div className="mt-2 grid gap-2">
              <Field label="Headline">
                <input value={headline} onChange={(event) => setHeadline(event.target.value)} className={inputClass} />
              </Field>
              <Field label="Body">
                <textarea
                  value={body}
                  onChange={(event) => setBody(event.target.value)}
                  rows={3}
                  className="w-full rounded-md border border-slate-800 bg-terminal-950 p-3 text-xs text-slate-200 outline-none focus:border-accent/50"
                />
              </Field>
            </div>
            <Button
              className="mt-3"
              variant="ghost"
              disabled={draftBanner.isPending || headline.trim().length === 0 || body.trim().length === 0}
              onClick={() => draftBanner.mutate()}
            >
              {draftBanner.isPending ? "Drafting…" : "Save draft"}
            </Button>

            <h3 className="mt-4 text-[10px] uppercase tracking-wider text-slate-500">Drafts awaiting publication</h3>
            <ul className="mt-2 flex flex-col gap-2">
              {drafts.length === 0 && <li className="text-xs text-slate-500">No unpublished drafts.</li>}
              {drafts.map((item) => (
                <li key={item.id} className="rounded-md border border-slate-800 p-2 text-xs">
                  <p className="font-semibold text-slate-200">
                    [{item.level}] {item.headline}
                  </p>
                  <p className="mt-1 text-slate-500">{item.body}</p>
                  <label className="mt-2 flex items-center gap-2 text-[10px] uppercase tracking-wider text-slate-500">
                    <input
                      type="checkbox"
                      checked={previewConfirmed}
                      onChange={(event) => setPreviewConfirmed(event.target.checked)}
                    />
                    I have read this preview
                  </label>
                  <Button
                    className="mt-2 min-h-9 text-[11px]"
                    disabled={!previewConfirmed}
                    onClick={() =>
                      gated({
                        action: "status_banner_publish",
                        target: `banner:${item.id}`,
                        label: `Publish banner ${item.id}`,
                        run: (token) =>
                          api(`/api/v1/admin/operations/banners/${item.id}/publish`, {
                            method: "POST",
                            headers: { "X-Step-Up-Token": token },
                            body: JSON.stringify({ confirmed_preview: true }),
                          }),
                      })
                    }
                  >
                    Publish
                  </Button>
                </li>
              ))}
            </ul>

            <h3 className="mt-4 text-[10px] uppercase tracking-wider text-slate-500">Live now</h3>
            <ul className="mt-2 flex flex-col gap-2">
              {(banners?.active ?? []).length === 0 && <li className="text-xs text-slate-500">No banner is live.</li>}
              {(banners?.active ?? []).map((item) => (
                <li key={item.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-warning/30 p-2 text-xs">
                  <span className="text-slate-200">
                    [{item.level}] {item.headline}
                  </span>
                  <Button variant="ghost" className="min-h-9 text-[11px]" onClick={() => withdrawBanner.mutate(item.id)}>
                    Withdraw
                  </Button>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Trash2 aria-hidden="true" size={14} /> Cache invalidation
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="mb-3 text-xs leading-5 text-slate-500">
              Clearing a forecast cache forces recomputation on the next request. It cannot delete market history or user
              data.
            </p>
            <div className="grid gap-2 sm:grid-cols-2">
              <Field label="Cache">
                <select value={cache} onChange={(event) => setCache(event.target.value)} className={inputClass}>
                  {(data?.invalidatable_caches ?? ["forecast_cache"]).map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Reason">
                <input value={cacheReason} onChange={(event) => setCacheReason(event.target.value)} className={inputClass} />
              </Field>
            </div>
            <Button
              className="mt-3"
              variant="ghost"
              disabled={cacheReason.trim().length === 0}
              onClick={() =>
                gated({
                  action: "cache_invalidation",
                  target: cache,
                  label: `Invalidate ${cache}`,
                  run: (token) =>
                    api("/api/v1/admin/operations/cache/invalidate", {
                      method: "POST",
                      headers: { "X-Step-Up-Token": token },
                      body: JSON.stringify({ cache, reason: cacheReason }),
                    }),
                })
              }
            >
              Invalidate cache
            </Button>
            {data?.low_history_model && (
              <div className="mt-4">
                <h3 className="text-[10px] uppercase tracking-wider text-slate-500">Pooled low-history model</h3>
                <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-5 text-slate-400">
                  {JSON.stringify(data.low_history_model, null, 2)}
                </pre>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </section>
  );
}

export default AdminOperationsPanel;
