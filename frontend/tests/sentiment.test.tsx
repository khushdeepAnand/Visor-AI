import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError, api } from "@/lib/api";
import SentimentPage from "@/app/sentiment/page";

vi.mock("next/navigation", () => ({
  usePathname: () => "/sentiment",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/components/TerminalShell", () => ({
  TerminalShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
  default: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ user: { id: 1, email: "user@example.com", is_admin: false }, loading: false }),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: vi.fn() };
});

const trendPayload = {
  symbol: "RELIANCE",
  window_days: 30,
  snapshot_count: 2,
  series: [
    {
      snapshot_at: "2026-09-16T09:30:00+05:30",
      avg_score: 0.31,
      label: "positive",
      headline_count: 4,
      scored_count: 4,
      source: "inline",
    },
    {
      snapshot_at: "2026-09-17T09:30:00+05:30",
      avg_score: -0.18,
      label: "negative",
      headline_count: 3,
      scored_count: 3,
      source: "news-catalogue",
    },
  ],
  is_stale: false,
};

const stalePayload = {
  ...trendPayload,
  snapshot_count: 1,
  is_stale: true,
  series: [
    {
      snapshot_at: "2026-08-01T09:30:00+05:30",
      avg_score: 0.05,
      label: "neutral",
      headline_count: 2,
      scored_count: 1,
      source: "inline",
    },
  ],
};

const emptyPayload = { symbol: "RELIANCE", window_days: 30, snapshot_count: 0, is_stale: false, series: [] };

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SentimentPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(api).mockReset();
});

describe("sentiment trend page (K4)", () => {
  it("renders the operator-flag disabled banner when the endpoint is gated", async () => {
    vi.mocked(api).mockRejectedValue(new ApiError("Feature is disabled", 503, "feature_disabled", null, false));
    renderPage();
    expect(await screen.findByRole("status")).toHaveTextContent(/sentiment trend is currently turned off by an operator feature flag/i);
  });

  it("renders trend snapshots newest-to-oldest with score labels and count columns", async () => {
    vi.mocked(api).mockResolvedValue(trendPayload as never);
    renderPage();
    const negative = await screen.findByText("negative");
    const positive = screen.getByText("positive");
    expect(negative.compareDocumentPosition(positive) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText("0.31")).toBeInTheDocument();
    expect(screen.getByText("Headlines")).toBeInTheDocument();
  });

  it("POSTs a capture on Capture now and refetches the trend", async () => {
    vi.mocked(api).mockResolvedValue(trendPayload as never);
    renderPage();
    await screen.findByText("negative");
    await userEvent.click(screen.getByRole("button", { name: /capture now/i }));
    await waitFor(() => {
      expect(vi.mocked(api).mock.calls.some((call) => call[0] === `/api/v1/sentiment/${encodeURIComponent("RELIANCE")}?days=30`)).toBe(true);
    });
    await waitFor(() => {
      expect(api).toHaveBeenCalledWith(`/api/v1/sentiment/${encodeURIComponent("RELIANCE")}?days=30`);
    });
    await waitFor(() => {
      const refetchUrl = `/api/v1/sentiment/${encodeURIComponent("RELIANCE")}?days=30`;
      expect(vi.mocked(api).mock.calls.some((c) => c[0] === refetchUrl)).toBe(true);
    });
  });

  it("marks a stale trend series with a status badge", async () => {
    vi.mocked(api).mockResolvedValue(stalePayload as never);
    renderPage();
    expect(await screen.findByText(/last snapshot is stale/i)).toBeInTheDocument();
  });

  it("renders an empty state when no snapshots exist", async () => {
    vi.mocked(api).mockResolvedValue(emptyPayload as never);
    renderPage();
    expect(await screen.findByText(/no sentiment snapshots for reliance/i)).toBeInTheDocument();
  });
});
