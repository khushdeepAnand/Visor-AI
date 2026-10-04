import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("next/navigation", () => ({
  usePathname: () => "/research",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("@/components/TerminalShell", () => ({
  TerminalShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
  default: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ user: { id: 1, email: "user@example.com", is_admin: false }, loading: false }),
}));

const facts = [
  { label: "last close", value: "3100.75", source: "stored daily close for RELIANCE", as_of: "2026-09-19" },
  { label: "sessions in stored history", value: "120", source: "stored daily history for RELIANCE", as_of: "2026-09-19" },
];

const refusalFixture = {
  question: "What does the stored history show?",
  symbol: "RELIANCE",
  configured: false,
  mode: "refused",
  refused: true,
  refusal_code: "provider_not_configured",
  refusal_message: "No OpenAI-compatible research model is configured for this deployment.",
  answer: "",
  facts,
  disclosure: "It is not personalized investment advice.",
  schema_version: "research-assistant-v1",
};

const answerFixture = {
  question: "What is the last close?",
  symbol: "RELIANCE",
  configured: true,
  mode: "answer",
  refused: false,
  refusal_code: null,
  refusal_message: null,
  answer: "The last close was 3100.75 in the stored history.",
  facts,
  disclosure: "It is not personalized investment advice.",
  schema_version: "research-assistant-v1",
};

function makeApiHandler() {
  return vi.fn(async (_path: string, _init?: RequestInit) => refusalFixture);
}

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: makeApiHandler() };
});

import { api } from "@/lib/api";
import ResearchPage from "@/app/research/page";

const apiMock = vi.mocked(api);

async function renderAndAsk(question: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <ResearchPage />
    </QueryClientProvider>,
  );
  await userEvent.type(screen.getByLabelText("question"), question);
  return view;
}

beforeEach(() => {
  apiMock.mockReset();
  apiMock.mockImplementation(async (_path: string, _init?: RequestInit) => refusalFixture);
});

describe("research assistant page", () => {
  it("renders the grounded fact table header and disclosure", async () => {
    const view = await renderAndAsk("What does the stored history show?");
    await userEvent.click(screen.getByRole("button", { name: /grounded only/i }));
    expect(apiMock).toHaveBeenCalledWith("/api/v1/research/assistant", expect.objectContaining({ method: "POST" }));
    await waitFor(() => expect(screen.getByText(/Refused/i)).toBeVisible());
    expect(screen.getByText(/No OpenAI-compatible research model is configured/i)).toBeVisible();
    expect(screen.getAllByText("3100.75")[0]).toBeVisible();
    expect(screen.getByText(/Sourced facts for RELIANCE/i)).toBeVisible();
    expect(screen.getByText(/not personalized investment advice/i)).toBeVisible();
    expect(view.container).not.toHaveTextContent("undefined");
  });

  it("posts the typed symbol and question", async () => {
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ResearchPage />
      </QueryClientProvider>,
    );
    const symbol = screen.getByLabelText("symbol");
    await userEvent.clear(symbol);
    await userEvent.type(symbol, "TCS");
    await userEvent.type(screen.getByLabelText("question"), "What is the last close?");
    await userEvent.click(screen.getByRole("button", { name: /grounded only/i }));
    await waitFor(() => expect(apiMock).toHaveBeenCalled());
    const body = JSON.parse(String((apiMock.mock.calls[0][1] as RequestInit).body));
    expect(body).toEqual({ symbol: "TCS", question: "What is the last close?" });
  });

  it("shows a grounded answer when the backend does not refuse", async () => {
    apiMock.mockImplementation(async (_path: string, _init?: RequestInit) => answerFixture);
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ResearchPage />
      </QueryClientProvider>,
    );
    await userEvent.type(screen.getByLabelText("question"), "What is the last close?");
    await userEvent.click(screen.getByRole("button", { name: /grounded only/i }));
    await waitFor(() => expect(screen.getByText(/The last close was 3100.75/i)).toBeVisible());
    expect(screen.queryByText(/Refused/i)).not.toBeInTheDocument();
  });

  it("disables submit until there is a real question", async () => {
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ResearchPage />
      </QueryClientProvider>,
    );
    expect(screen.getByRole("button", { name: /grounded only/i })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("question"), "Price?");
    await waitFor(() => expect(screen.getByRole("button", { name: /grounded only/i })).toBeEnabled());
  });
});