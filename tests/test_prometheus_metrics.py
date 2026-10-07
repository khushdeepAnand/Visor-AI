from middleware.observability import ApiMetrics


def test_prometheus_exposes_real_counts_and_latency_without_personal_paths():
    metrics = ApiMetrics()
    metrics.observe("/account/private@example.com", 200, 25)
    metrics.observe("/predict/SECRET", 503, 75)
    text = metrics.prometheus()
    assert "stockpilot_http_requests_total 2" in text
    assert "stockpilot_http_errors_total 1" in text
    assert "stockpilot_http_request_duration_seconds_sum 0.1" in text
    assert "private@example.com" not in text and "SECRET" not in text
