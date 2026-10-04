"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, MinusCircle, RefreshCw, ShieldCheck, ShieldQuestion } from "lucide-react";
import { useRouter } from "next/navigation";
import { TerminalShell } from "@/components/TerminalShell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { api, formatApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";

type SecurityStatus = "PASS" | "WARNING" | "FAIL";

type SecurityCheck = {
  key: string;
  category: string;
  status: SecurityStatus;
  reason: string;
};

type SecurityReport = {
  checks: SecurityCheck[];
  generated_at: string;
  note: string;
};

const STATUS_STYLE: Record<SecurityStatus, { label: string; className: string; Icon: typeof CheckCircle2 }> = {
  PASS: { label: "Passing", className: "border-gain/30 bg-gain/10 text-gain", Icon: CheckCircle2 },
  WARNING: { label: "Needs review", className: "border-warning/30 bg-warning/10 text-warning", Icon: ShieldQuestion },
  FAIL: { label: "Action required", className: "border-loss/30 bg-loss/10 text-loss", Icon: AlertTriangle },
};

function SecurityWorkspace() {
  const [search, setSearch] = useState("");
  const report = useQuery({ queryKey: ["admin-security"], queryFn: () => api<SecurityReport>("/api/v1/admin/security"), retry: 1 });
  const checks = report.data?.checks ?? [];
  const filtered = search
    ? checks.filter((check) => `${check.category} ${check.key} ${check.reason}`.toLowerCase().includes(search.toLowerCase()))
    : checks;
  const counts = checks.reduce<Record<SecurityStatus, number>>(
    (acc, check) => ({ ...acc, [check.status]: acc[check.status] + 1 }),
    { PASS: 0, WARNING: 0, FAIL: 0 },
  );

  return (
    <TerminalShell>
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-accent">
            <ShieldCheck aria-hidden="true" size={17} />
            <span className="text-[10px] uppercase tracking-[.2em]">Administration</span>
          </div>
          <h1 className="mt-1 text-2xl font-semibold text-white">Security Center</h1>
          <p className="mt-1 max-w-2xl text-sm text-slate-500">
            A read-only posture scorecard built from live probes of this running deployment. Every line is an observed
            result — checks that cannot be run here say so instead of reporting peace of mind.
          </p>
        </div>
        <Button variant="ghost" onClick={() => report.refetch()} disabled={report.isFetching} className="gap-2">
          <RefreshCw aria-hidden="true" size={14} className={report.isFetching ? "animate-spin" : ""} />
          Re-run probes
        </Button>
      </div>

      {report.isError && (
        <div role="alert" className="mb-4 rounded-lg border border-loss/30 bg-loss/5 p-3 text-xs text-loss">
          <AlertTriangle aria-hidden="true" className="mr-2 inline" size={14} />
          {formatApiError(report.error)}
        </div>
      )}
      {report.isSuccess && report.data?.generated_at && (
        <p className="mb-4 text-[11px] text-slate-500">
          Generated at {new Date(report.data.generated_at).toLocaleString()}.{" "}
          <span className="text-slate-400">{report.data.note}</span>
        </p>
      )}

      {(counts.PASS > 0 || counts.WARNING > 0 || counts.FAIL > 0) && (
        <div className="mb-4 grid gap-3 sm:grid-cols-3">
          {(["PASS", "WARNING", "FAIL"] as const).map((status) => {
            const { Icon, className } = STATUS_STYLE[status];
            return (
              <Card key={status}>
                <CardHeader className="items-start gap-2">
                  <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-1 text-[9px] font-semibold uppercase ${className}`}>
                    <Icon aria-hidden="true" size={11} />
                    {STATUS_STYLE[status].label}
                  </span>
                </CardHeader>
                <CardContent>
                  <div className="tabular text-lg text-white">{counts[status]}</div>
                  <div className="mt-1 text-[10px] text-slate-500">check{counts[status] === 1 ? "" : "s"}</div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      <label className="relative mb-3 block max-w-md">
        <span className="sr-only">Filter checks</span>
        <input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Filter checks"
          className="h-11 w-full rounded-md border border-slate-800 bg-terminal-900 px-3 text-xs text-slate-300 outline-none focus:border-accent/50"
        />
      </label>

      {report.isLoading && (
        <div className="grid min-h-[40vh] place-items-center text-slate-500" role="status">
          Running live probes
        </div>
      )}
      {report.isSuccess && filtered.length === 0 && (
        <Card>
          <CardContent className="p-6 text-sm text-slate-500">No checks match this filter.</CardContent>
        </Card>
      )}
      {filtered.length > 0 && (
        <div className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-3">
          {filtered.map((check) => {
            const { Icon, className } = STATUS_STYLE[check.status];
            const emphasis = check.status === "FAIL" ? "border-loss/30" : "border-slate-800";
            return (
              <Card key={check.key} className={emphasis}>
                <CardHeader className="items-start gap-2">
                  <div className="flex w-full items-start justify-between gap-2">
                    <div>
                      <CardTitle className="text-sm text-white">{check.category}</CardTitle>
                      <p className="mt-0.5 font-mono text-[10px] uppercase tracking-wider text-slate-500">{check.key}</p>
                    </div>
                    <span className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-1 text-[9px] font-semibold uppercase ${className}`}>
                      <Icon aria-hidden="true" size={11} />
                      {check.status}
                    </span>
                  </div>
                </CardHeader>
                <CardContent>
                  <p className="text-xs leading-5 text-slate-400">{check.reason}</p>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </TerminalShell>
  );
}

export default function SecurityPage() {
  const { status, isAdmin } = useAuth();
  const router = useRouter();
  useEffect(() => {
    if (status === "unauthenticated") router.replace("/login?next=/admin/security");
  }, [status, router]);
  if (status === "loading") return <TerminalShell><div className="grid min-h-[60vh] place-items-center text-slate-500" role="status">Checking permissions</div></TerminalShell>;
  if (!isAdmin) return <TerminalShell><div className="grid min-h-[60vh] place-items-center text-center"><div><MinusCircle aria-hidden="true" className="mx-auto mb-3 text-warning" size={28} /><h1 className="text-lg font-semibold text-white">Access restricted</h1><p className="mt-1 text-sm text-slate-500">This workspace is limited to configured administrators.</p></div></div></TerminalShell>;
  return <SecurityWorkspace />;
}