"use client";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
type Status={is_open:boolean;reason:string;regular_session:string;timestamp:string};
type StreamHealth={provider?:string|null;adapter_configured:boolean;native_stream_active:boolean;rest_fallback:boolean;recent_native_symbols?:string[];leadership?:{active?:boolean;owned_symbols?:string[]}};
export function MarketStatus(){
 const market=useQuery({queryKey:["market-status"],queryFn:()=>api<Status>("/api/v1/market/status"),refetchInterval:30000});
 const stream=useQuery({queryKey:["stream-health"],queryFn:()=>api<StreamHealth>("/api/v1/market/stream-health"),refetchInterval:5000});
 const x=market.data,s=stream.data;const streamState=stream.isError?"stale":s?.native_stream_active?"live":s?.rest_fallback?"rest":"stale";
 const streamLabel=stream.isError?"stream stale":s?.native_stream_active?`${s.provider||"broker"} native`:s?.adapter_configured?"REST fallback":"REST polling";
 return <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400" aria-live="polite">
   <span className="inline-flex items-center gap-2"><span className={`size-2 rounded-full ${market.isError?"bg-loss":x?.is_open?"bg-gain":"bg-slate-600"}`}/><span className="font-medium">{market.isError?"NSE STATUS STALE":x?.is_open?"NSE LIVE":x?.reason||"NSE status"}</span></span>
   <span className="inline-flex items-center gap-2 rounded-full border border-slate-800/80 bg-terminal-900/70 px-2 py-1"><span className={`connection-dot ${streamState}`}/><span className={streamState==="live"?"text-gain":streamState==="rest"?"text-warning":"text-loss"}>{streamLabel}</span></span>
   <span className="hidden text-slate-600 xl:inline">{x?.regular_session||"09:15–15:30 IST"}</span>
 </div>
}
