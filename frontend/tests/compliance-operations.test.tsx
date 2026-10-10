import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { ComplianceOperations } from "@/components/ComplianceOperations";
import { api } from "@/lib/api";

vi.mock("@/lib/api", async () => ({ ...await vi.importActual("@/lib/api"), api: vi.fn() }));

it("support can read acknowledgment status but cannot edit the checklist", async () => {
  vi.mocked(api).mockResolvedValue({ version: "current", checklist: [{ id: "external_penetration_test", label: "Independent test", status: "open", note: "" }], accounts: [{ user_id: 1, email: "user@example.test", required: true, acknowledged_at: null }] } as never);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><ComplianceOperations editable={false} /></QueryClientProvider>);
  expect(await screen.findByText("user@example.test")).toBeInTheDocument();
  expect(screen.getByText("Review required")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Save review" })).not.toBeInTheDocument();
});
