import { ApiError, requestForecast } from "@/lib/api";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, statusText: status === 200 ? "OK" : "Not Found", headers: { "Content-Type": "application/json" } });
}

const forecast = {
  symbol: "RELIANCE", title: "Research range", model_label: "interval model",
  research_range: { low: 90, median_reference: 100, high: 110, confidence_level: .8 },
  reference_price: 100, confidence: { level: "moderate", summary: "Supported." },
  uncertainty: { range_width: 20, range_width_pct: 20, band: "wide", summary: "Wide." },
  scenarios: [], disclaimer: "Research only.",
};

describe("requestForecast", () => {
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it("polls a healthy POST forecast job beyond 25 seconds", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ job_id: "job-7", status: "queued", retry_after_ms: 10000 }))
      .mockResolvedValueOnce(jsonResponse({ job_id: "job-7", status: "running", retry_after_ms: 10000 }))
      .mockResolvedValueOnce(jsonResponse({ job_id: "job-7", status: "running", retry_after_ms: 10000 }))
      .mockResolvedValueOnce(jsonResponse({ job_id: "job-7", status: "completed", result: forecast }));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    const resultPromise = requestForecast("RELIANCE", "1y", "1D", .8, controller.signal);
    await vi.advanceTimersByTimeAsync(30_000);
    await expect(resultPromise).resolves.toMatchObject({ symbol: "RELIANCE" });
    expect(controller.signal.aborted).toBe(false);
    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/v1/forecast-jobs", expect.objectContaining({ method: "POST" }));
    expect(fetchMock).toHaveBeenLastCalledWith("/api/v1/forecast-jobs/job-7", expect.any(Object));
  });

  it("falls back to the legacy GET endpoint when the job contract is unavailable", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ detail: "Not Found" }, 404))
      .mockResolvedValueOnce(jsonResponse(forecast));
    vi.stubGlobal("fetch", fetchMock);
    await expect(requestForecast("RELIANCE", "1y", "1D")).resolves.toMatchObject({ symbol: "RELIANCE" });
    expect(fetchMock.mock.calls[1][0]).toContain("/api/v1/predict/RELIANCE");
  });

  it("does not hide real forecast-job failures behind the legacy endpoint", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "Worker unavailable" }, 503)));
    await expect(requestForecast("RELIANCE", "1y", "1D")).rejects.toBeInstanceOf(ApiError);
  });
});
