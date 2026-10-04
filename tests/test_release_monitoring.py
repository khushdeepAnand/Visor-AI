from scripts.promotion_gate import validate_report
from scripts.reconcile_challenger import reconcile

def test_gate_rejects_inverted_quantiles():
    ok, reasons = validate_report({"coverage": .8, "quantile_order_valid": False, "sample_count": 100})
    assert not ok and any("quantile" in x for x in reasons)

def test_reconciliation_alerts_on_coverage_divergence():
    ok, msg = reconcile({"coverage": .8, "quantile_order_valid": True}, {"coverage": .9, "quantile_order_valid": True})
    assert not ok and "divergence" in msg

def test_gate_missing_report_fields_fails_closed():
    ok, _ = validate_report({})
    assert not ok
