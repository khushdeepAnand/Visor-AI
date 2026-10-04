"use client";

import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { History, LockKeyhole, Sparkles } from "lucide-react";
import { ApiError, api } from "@/lib/api";
import { useSessionReady } from "@/lib/auth";
import { Button } from "@/components/ui/button";

type Scenario={key:string;title:string;narrative:string;index:string;entry_date:string;entry_close:number;prev_close:number;choices:string[];source:string};
type Reveal={key:string;title:string;outcome_date:string;outcome_close:number;source:string;choice:{choice:string;return_pct:number};all_choice_returns_pct:Record<string,number>};

export function HistoricalReplay(){
  const sessionReady=useSessionReady();
  const scenarios=useQuery({queryKey:["historical-replay"],queryFn:()=>api<{items:Scenario[]}>("/api/v1/paper/challenges/historical"),retry:false,enabled:sessionReady});
  const [selected,setSelected]=useState<Record<string,string>>({});
  const [revealed,setRevealed]=useState<Record<string,Reveal>>({});
  const mutation=useMutation({mutationFn:({key,choice}:{key:string;choice:string})=>api<Reveal>(`/api/v1/paper/challenges/historical/${encodeURIComponent(key)}/reveal`,{method:"POST",body:JSON.stringify({choice})}),onSuccess:(result)=>setRevealed(current=>({...current,[result.key]:result}))});
  if(!sessionReady)return <div className="rounded-xl border border-slate-800 bg-terminal-900/70 p-4 text-xs text-slate-500">Sign in to play sourced historical replay challenges.</div>;
  if(scenarios.error)return <div className="rounded-xl border border-slate-800 bg-terminal-900/70 p-4 text-xs text-slate-500">Replay challenges are unavailable right now. {(scenarios.error as ApiError).supportId?`Support ID ${(scenarios.error as ApiError).supportId}.`:""}</div>;
  return <div className="space-y-3">{scenarios.data?.items?.map(item=>{const reveal=revealed[item.key];const choice=selected[item.key];return <div key={item.key} className="panel-glow rounded-xl border border-slate-800/80 bg-terminal-900/75 p-4">
    <div className="flex items-start justify-between gap-3"><div><div className="flex items-center gap-2 text-[10px] uppercase tracking-[.16em] text-secondary"><History size={13}/>{item.entry_date} · {item.index}</div><h3 className="display-font mt-1 text-base font-semibold text-slate-100">{item.title}</h3></div>{reveal?<span className="rounded-full border border-gain/30 bg-gain/5 px-2 py-1 text-[9px] text-gain">REVEALED</span>:<span className="flex items-center gap-1 rounded-full border border-slate-700 px-2 py-1 text-[9px] text-slate-500"><LockKeyhole size={10}/>OUTCOME LOCKED</span>}</div>
    <p className="mt-2 text-xs leading-5 text-slate-500">{item.narrative}</p>
    {!reveal&&<><div className="mt-3 flex flex-wrap gap-2">{item.choices.map(c=><button key={c} onClick={()=>setSelected(x=>({...x,[item.key]:c}))} className={`interactive-surface rounded-lg border px-3 py-2 text-[10px] font-semibold ${choice===c?"border-accent/40 bg-accent/10 text-accent":"border-slate-800 text-slate-400"}`}>{c.replaceAll("_"," ")}</button>)}</div><Button disabled={!choice||mutation.isPending} onClick={()=>choice&&mutation.mutate({key:item.key,choice})} className="mt-3 gap-2"><Sparkles size={13}/>{mutation.isPending?"Replaying…":"Lock decision & reveal"}</Button></>}
    {reveal&&<div className="reveal-card mt-4 rounded-xl border border-secondary/25 bg-gradient-to-br from-secondary/10 via-transparent to-accent/5 p-4"><div className="text-[10px] uppercase tracking-[.16em] text-secondary">Next-session outcome</div><div className="mt-1 flex flex-wrap items-baseline gap-3"><span className="tabular text-2xl font-semibold text-white">{reveal.outcome_close.toLocaleString("en-IN",{maximumFractionDigits:2})}</span><span className={`tabular text-sm font-semibold ${reveal.choice.return_pct>=0?"signal-up":"signal-down"}`}>{reveal.choice.return_pct>=0?"▲":"▼"} {reveal.choice.return_pct.toFixed(2)}% on {reveal.choice.choice}</span></div><div className="mt-3 grid gap-2 sm:grid-cols-2">{Object.entries(reveal.all_choice_returns_pct).map(([name,value])=><div key={name} className={`rounded-lg border p-2 text-xs ${name===reveal.choice.choice?"border-accent/30 bg-accent/5":"border-slate-800"}`}><div className="text-[9px] uppercase text-slate-600">{name.replaceAll("_"," ")}</div><div className={`tabular mt-1 ${value>=0?"signal-up":"signal-down"}`}>{value>=0?"▲":"▼"} {value.toFixed(2)}%</div></div>)}</div><p className="mt-3 text-[10px] leading-4 text-slate-600">Source: {reveal.source}</p></div>}
  </div>})}</div>;
}
