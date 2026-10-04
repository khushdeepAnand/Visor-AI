"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, RefreshCw, ShieldCheck, TriangleAlert, Wrench } from "lucide-react";
import { useState } from "react";
import { TerminalShell } from "@/components/TerminalShell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { api, formatApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";

/**
 * Setup Doctor — the screen that answers "why is sign-in / market data not
 * working" with variable names instead of guesses.
 *
 * Everything rendered here comes from GET /api/v1/admin/setup and
 * GET /api/v1/admin/review. No secret value is returned by those endpoints, so
 * nothing sensitive can leak onto this page; only presence, derived token
 * expiry, and the fix text.
 */

type Check = {
  name: string;
  status: "ok" | "degraded" | "action_required";
  summary: string;
  detail: string;
  fix: string | null;
  missing: string[];
  facts: Record<string, unknown>;
  blocks: string[];
};

type SetupResponse = {
  setup: {
    status: "ok" | "degraded" | "action_required";
    checks: Check[];
    action_required: string[];
    degraded: string[];
    blocked_capabilities: string[];
    next_step: string | null;
  };
  providers?: unknown;
  database?: Record<string, unknown>;
  version?: string;
};

type ReviewResponse = {
  version?: string;
  configuration?: { status: string; blocked_capabilities: string[]; next_step: string | null };
  providers?: unknown;
  feature_flags?: unknown;
  counts?: Record<string, number>;
  errors?: Record<string, unknown>;
  forecast_cache?: unknown;
  calibration?: Record<string, unknown>;
  note?: string;
};

const STATUS_STYLES: Record<string, { border: string; text: string; label: string }> = {
  ok: { border: "border-gain/40", text: "text-gain", label: "Ready" },
  degraded: { border: "border-warning/40", text: "text-warning", label: "Warning" },
  action_required: { border: "border-loss/40", text: "text-loss", label: "Action required" },
};

function StatusPill({ status }: { status: string }) {
  const style = STATUS_STYLES[status] ?? STATUS_STYLES.degraded;
  const Icon = status === "ok" ? CheckCircle2 : status === "degraded" ? TriangleAlert : AlertTriangle;
  return (
    <span className={`inline-flex shrink-0 items-center gap-1 border px-2 py-1 text-[9px] font-semibold uppercase tracking-[0.12em] ${style.border} ${style.text}`}>
      <Icon aria-hidden="true" size={11} /> {style.label}
    </span>
  );
}

function CheckRow({ check }: { check: Check }) {
  return (
    <li className="interactive-surface border border-slate-700 bg-terminal-900 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="ledger-label">{check.name.replace(/_/g, " ")}</p>
          <p className="mt-1 text-sm font-medium text-slate-200">{check.summary}</p>
        </div>
        <StatusPill status={check.status} />
      </div>
      {check.status !== "ok" ? (
        <div className="mt-2 space-y-2 text-[12px] leading-5 text-slate-300">
          <p>{check.detail}</p>
          {check.missing.length > 0 ? (
            <p className="tabular text-[11px] text-loss">
              Missing: {check.missing.join(", ")}
            </p>
          ) : null}
          {check.fix ? (
            <p className="border-l-2 border-accent pl-2 text-[12px] text-slate-300">
              <b className="font-semibold text-slate-200">Fix: </b>
              {check.fix}
            </p>
          ) : null}
          {check.blocks.length > 0 ? (
            <p className="ledger-label">Blocks: {check.blocks.join(", ").replace(/_/g, " ")}</p>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function JsonPanel({ title, description, value }: { title: string; description: string; value?: unknown }) {
  const [open, setOpen] = useState(false);
  const reported = value !== undefined && value !== null;
  return (
    <Card>
      <CardHeader className="items-start gap-3">
        <div>
          <CardTitle>{title}</CardTitle>
          <p className="mt-1 text-[11px] leading-4 text-slate-500">{description}</p>
        </div>
        <Button variant="ghost" onClick={() => setOpen((previous) => !previous)} disabled={!reported}>
          {open ? "Hide" : reported ? "Inspect" : "Not reported"}
        </Button>
      </CardHeader>
      {open && reported ? (
        <CardContent>
          <pre className="tabular max-h-80 overflow-auto border border-slate-700 bg-terminal-850 p-3 text-[11px] leading-5 text-slate-300">
            {JSON.stringify(value, null, 2)}
          </pre>
        </CardContent>
      ) : null}
    </Card>
  );
}

export default function AdminSetupPage() {
  const { isAdmin, status: authStatus } = useAuth();
  const enabled = authStatus === "authenticated" && isAdmin;

  const setup = useQuery({
    queryKey: ["admin", "setup"],
    queryFn: () => api<SetupResponse>("/api/v1/admin/setup"),
    enabled,
    refetchOnWindowFocus: false,
  });
  const review = useQuery({
    queryKey: ["admin", "review"],
    queryFn: () => api<ReviewResponse>("/api/v1/admin/review"),
    enabled,
    refetchOnWindowFocus: false,
  });

  if (!enabled) {
    return (
      <TerminalShell>
        <div className="mx-auto max-w-3xl p-4">
          <Card>
            <CardHeader>
              <CardTitle>Administrator access required</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-slate-300">
                Sign in with an account listed in the administrator allowlist to view configuration diagnostics.
              </p>
            </CardContent>
          </Card>
        </div>
      </TerminalShell>
    );
  }

  const report = setup.data?.setup;
  const checks = report?.checks ?? [];
  const failing = checks.filter((check) => check.status === "action_required");
  const warning = checks.filter((check) => check.status === "degraded");
  const passing = checks.filter((check) => check.status === "ok");

  return (
    <TerminalShell>
      <div className="mx-auto max-w-5xl space-y-4 p-4">
        <header className="flex flex-wrap items-end justify-between gap-3 border-b border-slate-700 pb-3">
          <div>
            <p className="ledger-label">Admin / Diagnostics</p>
            <h1 className="display-font mt-1 text-2xl text-slate-100">Setup Doctor</h1>
            <p className="mt-1 max-w-2xl text-[12px] leading-5 text-slate-400">
              Google sign-in and Upstox market data fail closed when their credentials are absent. This page names the
              exact environment variables that are missing, what each one blocks, and how to fix it. Secret values are
              never returned by the API.
            </p>
          </div>
          <div className="flex items-center gap-2">
            {report ? <StatusPill status={report.status} /> : null}
            <Button
              onClick={() => {
                void setup.refetch();
                void review.refetch();
              }}
              disabled={setup.isFetching}
            >
              <RefreshCw aria-hidden="true" size={14} /> Re-run checks
            </Button>
          </div>
        </header>

        {setup.isError ? (
          <Card>
            <CardHeader>
              <CardTitle>Diagnostics unavailable</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-loss">{formatApiError(setup.error)}</p>
            </CardContent>
          </Card>
        ) : null}

        {setup.isLoading ? <div className="skeleton h-28 w-full" /> : null}

        {report ? (
          <Card>
            <CardHeader className="items-start gap-3">
              <div>
                <CardTitle>What to do next</CardTitle>
                <p className="mt-1 text-[11px] leading-4 text-slate-500">
                  Configuration blockers are listed first because every downstream surface fails while one is open.
                </p>
              </div>
              <Wrench aria-hidden="true" size={16} className="text-slate-400" />
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-sm text-slate-200">
                {report.next_step ?? "Every check passed. No configuration action is outstanding."}
              </p>
              <dl className="grid gap-3 sm:grid-cols-3">
                <div className="border border-slate-700 bg-terminal-850 p-3">
                  <dt className="ledger-label">Blocking</dt>
                  <dd className="tabular mt-1 text-xl text-loss">{failing.length}</dd>
                </div>
                <div className="border border-slate-700 bg-terminal-850 p-3">
                  <dt className="ledger-label">Warnings</dt>
                  <dd className="tabular mt-1 text-xl text-warning">{warning.length}</dd>
                </div>
                <div className="border border-slate-700 bg-terminal-850 p-3">
                  <dt className="ledger-label">Passing</dt>
                  <dd className="tabular mt-1 text-xl text-gain">{passing.length}</dd>
                </div>
              </dl>
              {report.blocked_capabilities.length > 0 ? (
                <p className="ledger-label">
                  Currently blocked: {report.blocked_capabilities.join(", ").replace(/_/g, " ")}
                </p>
              ) : null}
            </CardContent>
          </Card>
        ) : null}

        {checks.length > 0 ? (
          <Card>
            <CardHeader>
              <CardTitle>Configuration checks</CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="space-y-2">
                {[...failing, ...warning, ...passing].map((check) => (
                  <CheckRow key={check.name} check={check} />
                ))}
              </ul>
            </CardContent>
          </Card>
        ) : null}

        <div className="grid gap-3 lg:grid-cols-2">
          <JsonPanel
            title="Provider readiness"
            description="Per-provider readiness as reported by the market data manager."
            value={setup.data?.providers}
          />
          <JsonPanel
            title="Feature flags"
            description="Operator flags and rollout state. All new subsystems default to off."
            value={review.data?.feature_flags}
          />
          <JsonPanel
            title="Interval calibration method"
            description="What the range claim is based on, and what this system refuses to claim."
            value={review.data?.calibration}
          />
          <JsonPanel
            title="Failure groups"
            description="Grouped failure counts. No exception text or traceback is exposed."
            value={review.data?.errors}
          />
        </div>

        <p className="flex items-start gap-2 border border-slate-700 bg-terminal-900 p-3 text-[11px] leading-5 text-slate-400">
          <ShieldCheck aria-hidden="true" size={14} className="mt-0.5 shrink-0" />
          <span>
            This page reads configuration state only. It cannot write credentials, enable a feature flag, or place an
            order. Edit your .env file and restart the API to change anything reported here.
          </span>
        </p>
      </div>
    </TerminalShell>
  );
}
