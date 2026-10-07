import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, it, vi } from "vitest";
import Account from "@/app/account/page";
import { api } from "@/lib/api";

const testState = vi.hoisted(() => ({
  replace: vi.fn(),
  refreshRouter: vi.fn(),
  refreshSession: vi.fn(),
  signOut: vi.fn(),
  auth: {
    status: "authenticated" as "loading" | "authenticated" | "unauthenticated" | "error",
    user: {
      id: 7, name: "Asha Mehta", email: "asha@example.com", role: "admin",
      auth_provider: "password+google", connected_providers: ["google"],
    } as null | { id: number; name: string; email: string; role: string; auth_provider: string; connected_providers: string[] },
    isAdmin: true, isSignedOut: false, error: null,
  },
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/account",
  useRouter: () => ({ replace: testState.replace, refresh: testState.refreshRouter }),
}));

vi.mock("@/components/TerminalShell", () => ({
  TerminalShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock("@tanstack/react-query", async () => {
  const actual = await vi.importActual<typeof import("@tanstack/react-query")>("@tanstack/react-query");
  return {
    ...actual,
    useQuery: ({ queryKey }: { queryKey: string[] }) => ({ data:
      queryKey[0] === "passkeys" ? { available: true, credentials: [] } :
      queryKey[0] === "mfa-status" ? { enabled: false, totp_enabled: false, recovery_codes_remaining: 0 } :
      queryKey[0] === "sessions" ? { items: [] } : { google: { configured: true } }, isLoading: false, isError: false }),
    useMutation: (opts: any) => ({ mutate: async () => { await opts.mutationFn(); }, isPending: false }),
    useQueryClient: () => ({ invalidateQueries: vi.fn(), removeQueries: vi.fn(), clear: vi.fn(), setQueryData: vi.fn() }),
  };
});

vi.mock("@/lib/auth", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth")>("@/lib/auth");
  return {
    ...actual,
    useAuth: () => ({
      ...testState.auth,
      refresh: testState.refreshSession,
      signOut: testState.signOut,
    }),
  };
});

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: vi.fn().mockResolvedValue({ google: { configured: true } }), formatApiError: vi.fn((e) => e?.message || "error") };
});

describe("account page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    testState.auth.status = "authenticated";
    testState.auth.isAdmin = true;
    testState.auth.user = {
      id: 7, name: "Asha Mehta", email: "asha@example.com", role: "admin",
      auth_provider: "password+google", connected_providers: ["google"],
    };
    testState.signOut.mockResolvedValue(undefined);
  });

  it("shows identity, role, connected providers, and scope", () => {
    render(<Account />);
    expect(screen.getByText("Asha Mehta")).toBeInTheDocument();
    expect(screen.getByText("asha@example.com")).toBeInTheDocument();
    expect(screen.getByText("admin")).toBeInTheDocument();
    expect(screen.getByText("password+google")).toBeInTheDocument();
    expect(screen.getByText("connected")).toBeInTheDocument();
    expect(screen.getByText("Research & paper-only scope")).toBeInTheDocument();
    expect(screen.getByText("Two-factor authentication")).toBeInTheDocument();
    expect(screen.getByText(/no IP address or device fingerprint is stored/i)).toBeInTheDocument();
  });

  it("redirects a signed-out visitor to login", async () => {
    testState.auth.status = "unauthenticated";
    testState.auth.user = null;
    render(<Account />);
    await waitFor(() => expect(testState.replace).toHaveBeenCalledWith("/login?next=/account"));
  });

  it("signs out and returns to login", async () => {
    const user = userEvent.setup();
    render(<Account />);
    await user.click(screen.getByRole("button", { name: /sign out/i }));
    await waitFor(() => expect(testState.signOut).toHaveBeenCalled());
  });

  it("requires the irreversible confirmation before deleting the account", async () => {
    const user = userEvent.setup();
    render(<Account />);
    const button = screen.getByRole("button", { name: /permanently delete account/i });
    expect(button).toBeDisabled();
    await user.type(screen.getByLabelText(/type delete to confirm/i), "DELETE");
    expect(button).toBeEnabled();
    await user.click(button);
    await waitFor(() => expect(api).toHaveBeenCalledWith(
      "/api/v1/auth/account",
      expect.objectContaining({ method: "DELETE" }),
    ));
  });
});
