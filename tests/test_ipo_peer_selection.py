import pandas as pd

from forecasting.pooled_cross_section import (
    blended_forecast_with_peer_prior,
    peer_transfer_prior,
    select_ipo_peers,
)


def test_short_history_uses_matched_peer_blend_with_real_values():
    master = [
        {"symbol": "NEWCO", "sector": "IT", "market_cap_bucket": "small", "listing_year": 2025},
        {"symbol": "PEER1", "sector": "IT", "market_cap_bucket": "small", "listing_year": 2025},
        {"symbol": "PEER2", "sector": "IT", "market_cap_bucket": "large", "listing_year": 2025},
        {"symbol": "UNRELATED", "sector": "BANK", "market_cap_bucket": "small", "listing_year": 2018},
    ]
    peers = select_ipo_peers("NEWCO", master, n=10)
    assert peers == ["PEER1", "PEER2"]

    def loader(symbol):
        base = 100.0 if symbol == "PEER1" else 110.0
        close = [base + i * (1.0 if symbol == "PEER1" else 0.5) for i in range(120)]
        return pd.DataFrame({"close": close}, index=pd.date_range("2025-01-01", periods=120))

    prior = peer_transfer_prior(peers, loader, horizon=1)
    blended = blended_forecast_with_peer_prior(
        {"expected_return": 0.01, "residual_scale": 0.02}, prior, sessions_available=4
    )
    assert prior["peer_count"] == 2
    assert prior["mean_return"] != 0.0
    assert blended["expected_return"] != 0.01
    assert blended["peer_count"] == 2
