import { act, render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import { MarketProvider } from "@/components/MarketContext";
import { ThemeProvider } from "@/components/ThemeProvider";
import { AuthProvider } from "@/lib/auth";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  useParams: () => ({ symbol: "RELIANCE" }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/",
}));

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...props
  }: Omit<React.AnchorHTMLAttributes<HTMLAnchorElement>, "href"> & {
    href: string | { pathname?: string };
  }) => (
    <a href={typeof href === "string" ? href : href.pathname || ""} {...props}>
      {children}
    </a>
  ),
}));

// api() calls hit a real backend at NEXT_PUBLIC_API_BASE, which doesn't exist
// in this test environment — every page must still *render* without throwing
// while its queries are pending/erroring, exactly like a real network hiccup
// in production. This mock keeps every call pending forever so we're testing
// the loading-state render path, which is the one most likely to hit a null
// dereference on `.data` before it exists.
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: vi.fn(() => new Promise(() => {})), requestForecast: vi.fn(() => new Promise(() => {})) };
});

const modules = import.meta.glob("../app/**/page.tsx", { eager: true }) as Record<
  string,
  { default: React.ComponentType }
>;

async function renderPage(Page: React.ComponentType) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let view: ReturnType<typeof render>;
  await act(async () => {
    view = render(
      <QueryClientProvider client={client}>
        <ThemeProvider><AuthProvider><MarketProvider><Page /></MarketProvider></AuthProvider></ThemeProvider>
      </QueryClientProvider>,
    );
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  return view!;
}

describe("every page renders without crashing", () => {
  for (const [path, mod] of Object.entries(modules)) {
    const Page = mod.default;
    it(`${path} mounts cleanly while queries are pending`, async () => {
      const view = await renderPage(Page);
      view.unmount();
    });
  }
});

describe("every page survives an API error", () => {
  for (const [path, mod] of Object.entries(modules)) {
    const Page = mod.default;
    it(`${path} does not crash when every query rejects`, async () => {
      const { api } = await import("@/lib/api");
      vi.mocked(api).mockRejectedValue(new Error("network unreachable in test"));
      const view = await renderPage(Page);
      view.unmount();
    });
  }
});

describe("every page survives an empty/shape-mismatched API response", () => {
  for (const [path, mod] of Object.entries(modules)) {
    const Page = mod.default;
    it(`${path} does not crash on an empty object response`, async () => {
      const { api } = await import("@/lib/api");
      // Every page expects some shape back; an empty object is the classic
      // "backend returned something unexpected" case that trips up code
      // written as `data.items.map(...)` instead of `data?.items?.map(...)`.
      vi.mocked(api).mockResolvedValue({} as never);
      const view = await renderPage(Page);
      view.unmount();
    });
  }
});
