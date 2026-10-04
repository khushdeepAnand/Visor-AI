import { beforeEach, describe, expect, it, vi } from "vitest";
import { ROUTE_COUNT, ROUTE_KEYS } from "@/lib/generated/routes";
import { route, typedApi } from "@/lib/typedApi";
import { ApiError } from "@/lib/api";

describe("generated OpenAPI route client", () => {
  it("exposes a non-trivial route table", () => {
    expect(ROUTE_KEYS.length).toBe(ROUTE_COUNT);
    expect(ROUTE_COUNT).toBeGreaterThan(100);
    expect(ROUTE_KEYS).toContain("post /api/v1/auth/login");
    expect(ROUTE_KEYS).toContain("get /api/v1/health");
    expect(ROUTE_KEYS).toContain("post /api/v1/auth/webauthn/mfa/verify");
    expect(new Set(ROUTE_KEYS).size).toBe(ROUTE_KEYS.length);
  });

  it("every route key has the form 'method /path'", () => {
    for (const key of ROUTE_KEYS) {
      expect(key).toMatch(/^(get|post|put|patch|delete) \//);
    }
  });
});

describe("typedApi", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    global.fetch = vi.fn();
  });

  it("serializes path params and query strings", async () => {
    const fetchMock = global.fetch as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });

    await typedApi("get /api/v1/predictions/quality", {
      query: { symbol: "RELIANCE" } as never,
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/predictions/quality?symbol=RELIANCE");
    expect(init.credentials).toBe("include");
    expect(init.cache).toBe("no-store");
  });

  it("sends typed JSON bodies with the route's method", async () => {
    const fetchMock = global.fetch as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ mfa_required: true }) });

    const result = await route("post /api/v1/auth/login", {
      body: { email: "a@b.c", password: "secret", next: "/portfolio" },
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/auth/login");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ email: "a@b.c", password: "secret", next: "/portfolio" });
    expect(result).toEqual({ mfa_required: true });
  });

  it("throws ApiError for failed responses", async () => {
    const fetchMock = global.fetch as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue({
      ok: false,
      status: 401,
      statusText: "Unauthorized",
      json: async () => ({ detail: "Invalid credentials" }),
    });

    await expect(
      typedApi("post /api/v1/auth/login", { body: { email: "x", password: "y" } }),
    ).rejects.toBeInstanceOf(ApiError);
  });

  it("raises a clear error when a path parameter is missing", async () => {
    await expect(
      typedApi("delete /api/v1/auth/webauthn/credentials/{credential_pk}" as never, {} as never),
    ).rejects.toThrow(/Missing path parameter/);
  });
});
