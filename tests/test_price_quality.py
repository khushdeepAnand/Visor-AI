import pandas as pd
import pytest
from services.market_data.quality import validate_prices


def test_price_contract_rejects_impossible_bars_and_accepts_zero_index_volume():
    frame = pd.DataFrame({"Open": [100.], "High": [102.], "Low": [99.], "Close": [101.], "Volume": [0.]},
                         index=pd.date_range("2026-10-05", periods=1))
    assert validate_prices(frame).attrs["data_quality"]["validated_rows"] == 1
    for field, value in [("High", 98.), ("Low", 103.), ("Close", float("nan")), ("Volume", -1.)]:
        invalid = frame.copy()
        invalid[field] = value
        with pytest.raises(ValueError, match="Pandera"):
            validate_prices(invalid)
    with pytest.raises(ValueError, match="unique"):
        validate_prices(pd.concat([frame, frame]))
