"use client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { MarketProvider } from "@/components/MarketContext";
import { AuthProvider } from "@/lib/auth";
import { ApiError } from "@/lib/api";
import { PwaRegistration } from "@/components/PwaRegistration";
export function Providers({children}:{children:React.ReactNode}) {
  const [client] = useState(()=>new QueryClient({defaultOptions:{queries:{staleTime:2500,retry:(attempt,error)=>{
    // Never retry a request the server has definitively rejected: repeating a
    // 401/403/404/422 cannot succeed and only delays the correct UI state.
    if (error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 429) return false;
    return attempt < 1;
  }}}}));
  return <QueryClientProvider client={client}><AuthProvider><MarketProvider><PwaRegistration />{children}</MarketProvider></AuthProvider></QueryClientProvider>;
}
