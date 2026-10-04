"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Bot, Database, Sparkles } from "lucide-react";
import { TerminalShell } from "@/components/TerminalShell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { api, formatApiError } from "@/lib/api";

/**
 * Research Assistant (K5).
 *
 * Render-only surface over `POST /api/v1/research/assistant`. All grounding,
 * numeric verification and advice refusal already happen server-side in
 * `services/research_assistant.py`; this page must only display what the API
 * returned, including its sourced fact table and disclosure, whether the
 * answer is a grounded reply or an explicit refusal.
 */

type GroundedFact = { label: string; value: string; source: string; as_of: string };

type AssistantResponse = {
  question: string;
  symbol: string;
  configured: boolean;
  mode: "answer" | "refused";
  refused: boolean;
  refusal_code: string | null;
  refusal_message: string | null;
  answer: string;
  facts: GroundedFact[];
  disclosure: string;
  schema_version: string;
};

export default function ResearchPage() {
  const [symbol, setSymbol] = useState("RELIANCE");
  const [question, setQuestion] = useState("");

  const ask = useMutation({
    mutationFn: (body: { symbol: string; question: string }) =>
      api<AssistantResponse>("/api/v1/research/assistant", {
        method: "POST",
        body: JSON.stringify(body),
      }),
  });

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const cleaned = symbol.trim().toUpperCase();
    if (!cleaned || question.trim().length < 3) return;
    ask.mutate({ symbol: cleaned, question: question.trim() });
  }

  const last = ask.data;

  return (
    <TerminalShell>
      <div className="space-y-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold tracking-tight">Research Assistant</h1>
            <p className="mt-1 max-w-2xl text-xs text-slate-500">
              Ask a plain-language question about one symbol. The assistant may only answer with figures it
              can attribute to this application&apos;s ingested history, and refuses advice-shaped questions
              by design.
            </p>
          </div>
          <Sparkles className="h-5 w-5 shrink-0 text-slate-400" aria-hidden />
        </div>

        <form onSubmit={onSubmit} className="space-y-3">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
            <label className="block text-xs">
              <span className="text-slate-500">Symbol</span>
              <input
                value={symbol}
                onChange={(event) => setSymbol(event.target.value)}
                className="mt-1 block h-9 w-full rounded bg-slate-950 px-3 text-sm text-slate-200 outline-none ring-1 ring-slate-800 focus:ring-slate-600 sm:w-44"
                placeholder="RELIANCE"
                aria-label="symbol"
              />
            </label>
            <label className="block flex-1 text-xs">
              <span className="text-slate-500">Question</span>
              <textarea
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                className="mt-1 block min-h-16 w-full rounded bg-slate-950 px-3 py-2 text-sm text-slate-200 outline-none ring-1 ring-slate-800 focus:ring-slate-600"
                placeholder="What does the stored history show about recent price movement?"
                aria-label="question"
              />
            </label>
          </div>
          <Button
            type="submit"
            disabled={ask.isPending || symbol.trim().length === 0 || question.trim().length < 3}
            className="inline-flex items-center gap-2"
          >
            <Bot className="h-4 w-4" aria-hidden />
            {ask.isPending ? "Asking…" : "Ask (grounded only)"}
          </Button>
        </form>

        {(ask.isError || ask.data) && (
          <div className="space-y-3">
            {ask.isError && (
              <div role="status" className="rounded border border-slate-800 bg-slate-950 p-3 text-xs text-amber-300">
                {formatApiError(ask.error)}
              </div>
            )}

            {last && (
              <>
                {last.refused ? (
                  <div role="status" className="rounded border border-amber-900/50 bg-amber-950/20 p-4">
                    <p className="text-[10px] uppercase tracking-wider text-amber-400">Refused</p>
                    <p className="mt-1 text-sm text-slate-200">{last.refusal_message}</p>
                    {last.refusal_code && (
                      <p className="mt-2 text-[10px] uppercase tracking-wider text-slate-500">code: {last.refusal_code}</p>
                    )}
                  </div>
                ) : (
                  <Card>
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm">Answer</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <p className="whitespace-pre-wrap text-sm text-slate-200">{last.answer}</p>
                    </CardContent>
                  </Card>
                )}

                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="flex items-center gap-2 text-sm">
                      <Database className="h-4 w-4 text-slate-500" aria-hidden />
                      Sourced facts for {last.symbol}
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    {last.facts.length === 0 ? (
                      <p className="text-xs text-slate-500">No ingested facts are available for this symbol yet.</p>
                    ) : (
                      <ul className="divide-y divide-slate-800">
                        {last.facts.map((fact) => (
                          <li key={fact.label} className="flex flex-col gap-0.5 py-2 sm:flex-row sm:items-baseline sm:gap-3">
                            <span className="w-56 shrink-0 text-xs text-slate-400">{fact.label}</span>
                            <span className="text-sm text-slate-100">{fact.value}</span>
                            <span className="text-[10px] text-slate-500 sm:ml-auto sm:text-right">{fact.source}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </CardContent>
                </Card>

                <p className="text-[10px] leading-relaxed text-slate-500">{last.disclosure}</p>
              </>
            )}
          </div>
        )}
      </div>
    </TerminalShell>
  );
}