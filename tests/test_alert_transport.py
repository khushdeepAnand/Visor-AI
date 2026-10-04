import json

from services import alert_transport


class _Response:
    status = 202

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_alert_transport_is_disabled_without_operator_endpoint(monkeypatch):
    monkeypatch.delenv("STOCKPILOT_ALERT_WEBHOOK_URL", raising=False)
    assert alert_transport.dispatch_operational_alert("test", "message") is False


def test_alert_transport_posts_configured_payload(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _Response()

    monkeypatch.setenv("STOCKPILOT_ALERT_WEBHOOK_URL", "https://alerts.invalid/hook")
    monkeypatch.setattr(alert_transport, "urlopen", fake_urlopen)
    assert alert_transport.dispatch_operational_alert("coverage_divergence", "bad coverage", details={"delta": 0.1})
    assert captured["url"].endswith("/hook")
    assert captured["payload"]["event"] == "coverage_divergence"
    assert captured["payload"]["details"]["delta"] == 0.1
