import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { vi } from "vitest";
import AdminPage from "@/app/admin/page";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const replace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/admin",
}));

vi.mock("@/components/TerminalShell", () => ({
  TerminalShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}));

vi.mock("@/lib/auth", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth")>("@/lib/auth");
  return { ...actual, useAuth: vi.fn() };
});

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: vi.fn() };
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><AdminPage /></QueryClientProvider>);
}

const adminAuth = {
  status: "authenticated" as const,
  user: { name: "Admin", email: "admin@example.com", role: "admin" },
  isAdmin: true,
  isSignedOut: false,
  error: null,
  refresh: vi.fn(),
  signOut: vi.fn(),
};

beforeEach(() => {
  replace.mockReset();
  vi.mocked(api).mockReset();
});

it("shows access restricted for non-admin", async () => {
  vi.mocked(useAuth).mockReturnValue({ ...adminAuth, user: { name: "User", role: "user" }, isAdmin: false });
  renderPage();
  expect(await screen.findByText("Access restricted")).toBeInTheDocument();
  expect(api).not.toHaveBeenCalled();
});

it("renders aggregate data and audit log for admins", async () => {
  vi.mocked(useAuth).mockReturnValue(adminAuth);
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path.includes("/admin/overview")) {
      return {
        version: "6.1.0", database_status: "healthy", instrument_count: 2400,
        counts: { users: 12 }, errors: {}, market: {},
        administrators: { max_admins: 3, configured: ["admin@example.com"], configuration_error: null },
        privileges: [], scheduler_enabled: false, settings: {}, requested_by: "admin@example.com",
      } as never;
    }
    if (path.includes("/admin/operations")) {
      return {
        operations: {
          kill_switches: { scopes: ["global", "symbol"], asset_classes: [], max_hours: 336, active: [] },
          feature_flags: [],
          banners: { levels: ["maintenance"], max_hours: 336, active: [], all: [] },
        },
        step_up: { actions: [], token_ttl_seconds: 300, single_use: true, max_failed_attempts: 5, failed_attempt_window_seconds: 900 },
        invalidatable_caches: [],
      } as never;
    }
    return { items: [{ id: 1, actor_email: "admin@example.com", action: "settings_update", outcome: "ok", detail: {}, created_at: "2026-09-03T10:00:00Z" }] } as never;
  });
  renderPage();
  expect(await screen.findByRole("heading", { name: "Admin Workspace" })).toBeInTheDocument();
  expect(await screen.findByRole("heading", { name: "Models" })).toBeInTheDocument();
  expect(await screen.findByRole("heading", { name: "Jobs" })).toBeInTheDocument();
  expect(await screen.findByRole("heading", { name: "Cache" })).toBeInTheDocument();
  expect((await screen.findAllByText(/2,400/)).length).toBeGreaterThan(0);
  expect((await screen.findAllByText(/12/)).length).toBeGreaterThan(0);
  expect(await screen.findByText("admin@example.com")).toBeInTheDocument();
});
