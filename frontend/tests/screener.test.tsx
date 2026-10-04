import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({
  usePathname: () => "/screener",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("@/components/TerminalShell", () => ({
  TerminalShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
  default: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ user: { id: 1, email: "user@example.com", is_admin: false }, loading: false }),
}));

const fields = [
  { name: "close_vs_sma200_pct", label: "Close vs 200-day average (%)", sessions_required: 200 },
  { name: "rsi_14", label: "RSI (14)", sessions_required: 15 },
];

const saved = [{ id: 7, name: "Above the 200-day average" }];

async function handleApi(path: string) {
  if (path.includes("/screener/fields")) return fields;
  if (path === "/api/v1/screener/saved") return { screens: saved };
  return {
    generated_at: "2026-09-25T00:00:00Z",
    filters: [],
    sort: { field: "symbol", descending: true },
    matches: [],
    truncated: false,
    coverage: { requested: 0, evaluated: 0, matched: 0, excluded: [] },
    evidence: { basis: "Realized OHLCV history.", min_sessions_required: 1 },
    disclosures: ["Not investment advice."],
  };
}

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: apiMock,
  };
});

import ScreenerPage from "@/app/screener/page";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ScreenerPage />
    </QueryClientProvider>,
  );
}

describe("screener page", () => {
  beforeEach(() => {
    apiMock.mockReset();
    apiMock.mockImplementation(handleApi);
  });

  it("loads the field catalogue into the first filter row", async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByLabelText("Field for filter 1")).toBeInTheDocument(),
    );
    expect(screen.getByLabelText("Comparator for filter 1")).toBeInTheDocument();
    expect(screen.getByLabelText("Value for filter 1")).toBeInTheDocument();
  });

  it("offers starter screens so an empty state is never a dead end", async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Oversold with volume/i })).toBeInTheDocument(),
    );
  });

  it("lists saved screens returned by the backend", async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Delete Above the 200-day average/i })).toBeInTheDocument(),
    );
  });

  it("exposes the universe and sort controls", async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByLabelText("Symbols, comma separated")).toBeInTheDocument(),
    );
    expect(screen.getByLabelText("Sort field")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Run screen/i })).toBeInTheDocument();
  });

  it("serializes ordinary filters with the backend op contract", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByLabelText("Field for filter 1");

    await user.click(screen.getByRole("button", { name: /Run screen/i }));

    await waitFor(() => {
      const call = apiMock.mock.calls.find(([path]) => path === "/api/v1/screener/run");
      expect(call).toBeDefined();
      const body = JSON.parse(String(call?.[1]?.body));
      expect(body.filters).toEqual([{ field: "rsi_14", op: "lt", value: 40 }]);
    });
  });

  it("serializes between filters with low and high bounds", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByLabelText("Comparator for filter 1");
    await user.selectOptions(screen.getByLabelText("Comparator for filter 1"), "between");
    await user.clear(screen.getByLabelText("Value for filter 1"));
    await user.type(screen.getByLabelText("Value for filter 1"), "30");
    await user.type(screen.getByLabelText("Upper value for filter 1"), "50");

    await user.click(screen.getByRole("button", { name: /Run screen/i }));

    await waitFor(() => {
      const call = apiMock.mock.calls.find(([path]) => path === "/api/v1/screener/run");
      expect(call).toBeDefined();
      const body = JSON.parse(String(call?.[1]?.body));
      expect(body.filters).toEqual([{ field: "rsi_14", op: "between", low: 30, high: 50 }]);
    });
  });
});
