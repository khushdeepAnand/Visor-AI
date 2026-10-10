import warnings
import sys

import pandas as pd
from sklearn.exceptions import ConvergenceWarning

from forecasting import interval_forecast


def test_nonconverged_member_is_excluded_without_losing_baseline(monkeypatch):
    def train(name, X, y):
        if name == "ElasticNet":
            warnings.warn("Objective did not converge", ConvergenceWarning)
        return interval_forecast._PersistenceRegressor()

    monkeypatch.setattr(interval_forecast.legacy_models, "_train_model_by_name", train)
    monkeypatch.setitem(sys.modules, "lightgbm", None)
    monkeypatch.setitem(sys.modules, "catboost", None)
    with warnings.catch_warnings():
        warnings.simplefilter("error", ConvergenceWarning)
        models = interval_forecast._fit_base_models(pd.DataFrame({"Close": [1., 2., 3.]}), pd.Series([2., 3., 4.]))
    assert "ElasticNet" not in models
    assert "Naive Persistence" in models
    assert "Linear Regression" in models
