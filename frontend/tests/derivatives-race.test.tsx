import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Derivatives from "@/app/derivatives/page";
import { api } from "@/lib/api";

const market = vi.hoisted(() => ({
  selectedSymbol: "NIFTY 50",
  setSelectedSymbol: vi.fn(),
  timeframe: "1D",
  dispatchSurface: vi.fn(),
}));

vi.mock("@/components/TerminalShell", () => ({
  TerminalShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}));

vi.mock("@/components/MarketContext", () => ({ useMarket: () => market }));
vi.mock("@/components/OptionChainTable", () => ({
  OptionChainTable: ({ rows }: { rows: unknown[] }) => <div data-testid="chain-rows">{rows.length}</div>,
}));
vi.mock("@/components/PayoffChart", () => ({ PayoffChart: () => <div /> }));
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: vi.fn() };
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

describe("derivatives option-chain request ownership", () => {
  beforeEach(() => {
    market.selectedSymbol = "NIFTY 50";
    market.setSelectedSymbol.mockReset();
    market.dispatchSurface.mockReset();
    vi.mocked(api).mockReset();
  });

  it("does not let an older symbol's expiry response replace the current symbol", async () => {
    const nifty = deferred<{ items: string[] }>();
    const bank = deferred<{ items: string[] }>();
    vi.mocked(api).mockImplementation((path) => {
      const value = String(path);
      if (value.includes("expiries/NIFTY%2050")) return nifty.promise as never;
      if (value.includes("expiries/NIFTY%20BANK")) return bank.promise as never;
      if (value.includes("live-chain")) return Promise.resolve({
        underlying: "NIFTY BANK", expiry: "2026-10-29", source: "test",
        is_live: true, is_stale: false, rows: [],
      }) as never;
      throw new Error(`Unexpected API call: ${value}`);
    });

    const view = render(<Derivatives />);
    market.selectedSymbol = "NIFTY BANK";
    view.rerender(<Derivatives />);

    await act(async () => bank.resolve({ items: ["2026-10-29"] }));
    expect(await screen.findByRole("option", { name: "2026-10-29" })).toBeInTheDocument();

    await act(async () => nifty.resolve({ items: ["2026-10-22"] }));
    await waitFor(() => expect(screen.queryByRole("option", { name: "2026-10-22" })).not.toBeInTheDocument());
    expect(screen.getByRole("option", { name: "2026-10-29" })).toBeInTheDocument();
  });
});
