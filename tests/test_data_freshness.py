import pandas as pd

from utils.data_freshness import format_data_freshness, get_data_freshness


def test_data_freshness_uses_fetch_metadata_and_stale_label():
    data = pd.DataFrame({"Close": [100.0]}, index=pd.to_datetime(["2026-01-05"]))
    data.attrs["fetched_at"] = "2026-01-05T12:30:00+00:00"
    data.attrs["is_stale"] = True
    data.attrs["fetch_warning"] = "Using cached data."

    metadata = get_data_freshness(data)

    assert metadata["is_stale"] is True
    assert metadata["warning"] == "Using cached data."
    assert "05 Jan 2026, 12:30 UTC" in format_data_freshness(data, "Price data")
    assert "stale fallback" in format_data_freshness(data, "Price data")
