from forecasting.calibration_monitor import group_interval_quality, summarize_interval_quality


def test_no_settled_forecasts_is_explicitly_insufficient():
    result = summarize_interval_quality([])
    assert result["state"] == "insufficient_data"
    assert result["empirical_coverage"] is None


def test_under_and_over_coverage_are_flagged():
    under = summarize_interval_quality([{"coverage_hit": 1 if i < 5 else 0, "winkler_score": 10+i, "confidence_level": .8} for i in range(10)])
    assert under["state"] == "under_coverage"
    over = summarize_interval_quality([{"coverage_hit": 1, "winkler_score": 10, "confidence_level": .8} for _ in range(10)])
    assert over["state"] == "over_coverage"


def test_quality_is_grouped_by_window_and_timeframe():
    rows=[
        {"coverage_hit":1,"winkler_score":10,"confidence_level":.8,"training_window":"1mo","timeframe":"5m"},
        {"coverage_hit":0,"winkler_score":20,"confidence_level":.8,"training_window":"1y","timeframe":"1h"},
    ]
    result=group_interval_quality(rows)
    assert result["overall"]["settled_forecasts"] == 2
    assert len(result["by_window_timeframe"]) == 2
