import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { applyLiveTick, parseQuoteFrame, useScreenerLiveQuotes } from "@/lib/liveQuotes";

describe("applyLiveTick", () => {
  it("applies a fresh price and keeps unknown fields from the previous quote", () => {
    const previous = { symbol: "RELIANCE", price: 2800, change_pct: 1.2, stream: "rest_poll" };
    const next = applyLiveTick(previous, { price: 2810.5, change_pct: 1.5, stream: "native" });
    expect(next).toEqual({ symbol: "RELIANCE", price: 2810.5, change_pct: 1.5, stream: "native", change: null, source: null, is_stale: false });
  });

  it("falls back to the existing price when a frame carries a null price", () => {
    const next = applyLiveTick({ symbol: "TCS", price: 3900, stream: "rest_poll" }, { price: undefined, stream: "rest_poll", is_stale: true });
    expect(next.price).toBe(3900);
    expect(next.stream).toBe("rest_poll");
    expect(next.is_stale).toBe(true);
  });
});

describe("parseQuoteFrame", () => {
  it("parses a hub frame", () => {
    const frame = parseQuoteFrame(JSON.stringify({ symbol: "RELIANCE", price: 100, change: 1, change_pct: 1.01, stream: "rest_poll" }));
    expect(frame?.price).toBe(100);
    expect(frame?.change_pct).toBe(1.01);
  });

  it("returns null for malformed payloads", () => {
    expect(parseQuoteFrame("not json")).toBeNull();
    expect(parseQuoteFrame("[1,2]")).toBeNull();
  });
});

describe("useScreenerLiveQuotes", () => {
  it("opens one socket per symbol and applies incoming ticks", () => {
    const sockets = new Map<string, { onmessage: ((event: { data: string }) => void) | null; close: () => void }>();
    const closed: string[] = [];
    const makeSocket = vi.fn((symbol: string) => {
      const socket = {
        onmessage: null as ((event: { data: string }) => void) | null,
        close: () => closed.push(symbol),
      };
      sockets.set(symbol, socket);
      return socket;
    });

    const { result, unmount } = renderHook(() => useScreenerLiveQuotes(["RELIANCE", "TCS"], true, makeSocket));

    expect(sockets.size).toBe(2);
    expect(makeSocket).toHaveBeenCalledWith("RELIANCE");
    expect(makeSocket).toHaveBeenCalledWith("TCS");

    const frame = JSON.stringify({ symbol: "RELIANCE", price: 2810.5, change: 12.5, change_pct: 0.45, stream: "rest_poll" });
    act(() => {
      sockets.get("RELIANCE")!.onmessage!({ data: frame });
    });

    expect(result.current.RELIANCE).toMatchObject({ price: 2810.5, change_pct: 0.45, stream: "rest_poll" });
    expect(result.current.TCS).toBeUndefined();

    act(() => unmount());
    expect(closed.slice().sort()).toEqual(["RELIANCE", "TCS"]);
  });

  it("closes all sockets and clears quotes when disabled", () => {
    const sockets = new Map<string, { onmessage: ((event: { data: string }) => void) | null; close: () => void }>();
    const closed: string[] = [];
    const makeSocket = vi.fn((symbol: string) => {
      const socket = { onmessage: null as ((event: { data: string }) => void) | null, close: () => closed.push(symbol) };
      sockets.set(symbol, socket);
      return socket;
    });

    const { rerender, result } = renderHook(({ enabled }) => useScreenerLiveQuotes(["RELIANCE"], enabled, makeSocket), {
      initialProps: { enabled: true },
    });

    expect(sockets.size).toBe(1);

    act(() => {
      sockets.get("RELIANCE")!.onmessage!({ data: JSON.stringify({ price: 100 }) });
    });
    expect(result.current.RELIANCE?.price).toBe(100);

    act(() => rerender({ enabled: false }));
    expect(closed).toEqual(["RELIANCE"]);
    expect(result.current).toEqual({});
  });

  it("marks the last quote stale when its socket disconnects", () => {
    let socket: {
      onmessage: ((event: { data: string }) => void) | null;
      onclose: ((event: CloseEvent) => void) | null;
      onerror: ((event: Event) => void) | null;
      close: () => void;
    };
    const makeSocket = vi.fn(() => {
      socket = { onmessage: null, onclose: null, onerror: null, close: vi.fn() };
      return socket;
    });
    const { result } = renderHook(() => useScreenerLiveQuotes(["RELIANCE"], true, makeSocket));

    act(() => socket!.onmessage!({ data: JSON.stringify({ price: 100, is_stale: false }) }));
    expect(result.current.RELIANCE).toMatchObject({ price: 100, is_stale: false, connection_status: "connected" });

    act(() => socket!.onclose!(new CloseEvent("close")));
    expect(result.current.RELIANCE).toMatchObject({ price: 100, is_stale: true, connection_status: "disconnected" });
  });

  it("marks a silent stream stale after the freshness threshold", () => {
    vi.useFakeTimers();
    try {
      let socket: { onmessage: ((event: { data: string }) => void) | null; close: () => void };
      const makeSocket = vi.fn(() => {
        socket = { onmessage: null, close: vi.fn() };
        return socket;
      });
      const { result, unmount } = renderHook(() => useScreenerLiveQuotes(["TCS"], true, makeSocket, 1_000));
      act(() => socket!.onmessage!({ data: JSON.stringify({ price: 3900, is_stale: false }) }));
      expect(result.current.TCS?.is_stale).toBe(false);

      act(() => vi.advanceTimersByTime(2_000));
      expect(result.current.TCS?.is_stale).toBe(true);
      unmount();
    } finally {
      vi.useRealTimers();
    }
  });
});
