import json

from services import alert_transport


class _Response:
    status_code = 202

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_alert_transport_is_disabled_without_operator_endpoint(monkeypatch):
    monkeypatch.delenv("STOCKPILOT_ALERT_WEBHOOK_URL", raising=False)
    assert alert_transport.dispatch_operational_alert("test", "message") is False


def test_alert_transport_posts_configured_payload(monkeypatch):
    captured = {}

    def fake_post(url, *, json, headers, timeout, allow_redirects):
        captured["url"] = url
        captured["timeout"] = timeout
        captured["payload"] = json
        assert allow_redirects is False
        return _Response()

    monkeypatch.setenv("STOCKPILOT_ALERT_WEBHOOK_URL", "https://alerts.invalid/hook")
    monkeypatch.setattr(alert_transport.requests, "post", fake_post)
    assert alert_transport.dispatch_operational_alert("coverage_divergence", "bad coverage", details={"delta": 0.1})
    assert captured["url"].endswith("/hook")
    assert captured["payload"]["event"] == "coverage_divergence"
    assert captured["payload"]["details"]["delta"] == 0.1
