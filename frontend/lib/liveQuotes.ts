"use client";

import { useEffect, useRef, useState } from "react";
import { wsBase } from "@/lib/api";

export type LiveScreenQuote = {
  symbol: string;
  price?: number;
  change?: number | null;
  change_pct?: number | null;
  source?: string | null;
  stream?: string;
  is_stale?: boolean;
  timestamp?: string;
  received_at?: number;
  connection_status?: "connected" | "disconnected";
};

type QuoteFrame = {
  symbol?: string;
  price?: number;
  change?: number | null;
  change_pct?: number | null;
  source?: string | null;
  stream?: string;
  is_stale?: boolean;
  timestamp?: string;
};

export function applyLiveTick(existing: LiveScreenQuote | undefined, frame: QuoteFrame): LiveScreenQuote {
  return {
    symbol: frame.symbol ?? existing?.symbol ?? "",
    price: frame.price ?? existing?.price,
    change: frame.change ?? existing?.change ?? null,
    change_pct: frame.change_pct ?? existing?.change_pct ?? null,
    source: frame.source ?? existing?.source ?? null,
    stream: frame.stream ?? existing?.stream ?? "rest_poll",
    is_stale: frame.is_stale ?? existing?.is_stale ?? false,
    timestamp: frame.timestamp ?? existing?.timestamp,
  };
}

export function parseQuoteFrame(raw: string): QuoteFrame | null {
  try {
    const parsed = JSON.parse(raw) as QuoteFrame;
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return null;
    return parsed;
  } catch {
    return null;
  }
}

type SocketLike = {
  onmessage: ((event: MessageEvent) => void) | null;
  onclose?: ((event: CloseEvent) => void) | null;
  onerror?: ((event: Event) => void) | null;
  close: () => void;
};

export function makeQuoteSocket(symbol: string): SocketLike {
  return new WebSocket(`${wsBase()}/ws/quotes/${encodeURIComponent(symbol)}?interval_seconds=1&timeframe=1D`);
}

/**
 * One socket per matched symbol feeding a symbol -> latest quote map. Opening
 * one socket per symbol matches the hub's per-symbol fan-out contract and lets
 * the operator kill the whole surface with the `screener_live_quotes` flag.
 */
export function useScreenerLiveQuotes(
  symbols: string[],
  enabled: boolean,
  makeSocket: (symbol: string) => SocketLike = makeQuoteSocket,
  staleAfterMs = 10_000,
) {
  const [live, setLive] = useState<Record<string, LiveScreenQuote>>({});
  const sockets = useRef<Map<string, SocketLike>>(new Map());
  const universeKey = symbols.map((value) => String(value).trim().toUpperCase()).filter(Boolean).sort().join(",");

  useEffect(() => {
    if (!enabled) {
      sockets.current.forEach((socket) => socket.close());
      sockets.current.clear();
      setLive({});
      return;
    }
    const requested = new Set(universeKey ? universeKey.split(",") : []);
    setLive((current) => Object.fromEntries(Object.entries(current).filter(([symbol]) => requested.has(symbol))));
    sockets.current.forEach((socket, symbol) => {
      if (!requested.has(symbol)) {
        socket.close();
        sockets.current.delete(symbol);
      }
    });
    let dead = false;
    requested.forEach((symbol) => {
      if (sockets.current.has(symbol)) return;
      let socket: SocketLike;
      try {
        socket = makeSocket(symbol);
      } catch {
        setLive((current) => ({
          ...current,
          [symbol]: { ...current[symbol], symbol, is_stale: true, connection_status: "disconnected" },
        }));
        return;
      }
      socket.onmessage = (event) => {
        if (dead) return;
        const frame = parseQuoteFrame(String(event.data));
        if (!frame) return;
        setLive((current) => ({
          ...current,
          [symbol]: {
            ...applyLiveTick(current[symbol], { ...frame, symbol }),
            received_at: Date.now(),
            connection_status: "connected",
          },
        }));
      };
      const markDisconnected = () => {
        if (dead) return;
        setLive((current) => ({
          ...current,
          [symbol]: { ...current[symbol], symbol, is_stale: true, connection_status: "disconnected" },
        }));
      };
      socket.onclose = markDisconnected;
      socket.onerror = markDisconnected;
      sockets.current.set(symbol, socket);
    });
    const staleTimer = window.setInterval(() => {
      const now = Date.now();
      setLive((current) => {
        let changed = false;
        const next = { ...current };
        requested.forEach((symbol) => {
          const quote = current[symbol];
          if (quote?.received_at && !quote.is_stale && now - quote.received_at > staleAfterMs) {
            next[symbol] = { ...quote, is_stale: true };
            changed = true;
          }
        });
        return changed ? next : current;
      });
    }, Math.max(250, Math.min(1000, staleAfterMs)));
    return () => {
      dead = true;
      window.clearInterval(staleTimer);
      sockets.current.forEach((socket) => socket.close());
      sockets.current.clear();
    };
  }, [universeKey, enabled, makeSocket, staleAfterMs]);

  return live;
}
