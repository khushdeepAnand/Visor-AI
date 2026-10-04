import warnings
from unittest.mock import patch
from types import SimpleNamespace

import pandas as pd
from statsmodels.tools.sm_exceptions import ConvergenceWarning

from forecasting.interval_forecast import _arima_baseline


def test_nonconvergent_arima_is_unavailable_instead_of_published():
    def fail_fit(*args, **kwargs):
        warnings.warn("optimizer failed to converge", ConvergenceWarning)
        return SimpleNamespace(forecast=lambda steps: [100.0])
    with patch("statsmodels.tsa.arima.model.ARIMA.fit", side_effect=fail_fit):
        result = _arima_baseline(pd.Series(range(1, 101), dtype=float))
    assert result["available"] is False
    assert "forecast" not in result
