"""Executable Pandera contract at the normalized price-pipeline boundary."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pandera.pandas as pa
from typing import cast

PRICE_SCHEMA = pa.DataFrameSchema(
    {**{name: pa.Column(float, checks=[pa.Check.gt(0), pa.Check(lambda s: np.isfinite(s))], coerce=True)
        for name in ("Open", "High", "Low", "Close")},
     "Volume": pa.Column(float, checks=[pa.Check.ge(0), pa.Check(lambda s: np.isfinite(s))], coerce=True)},
    checks=[pa.Check(lambda f: (f.High >= f[["Open", "Close", "Low"]].max(axis=1)).all(), error="High must bound OHLC"),
            pa.Check(lambda f: (f.Low <= f[["Open", "Close", "High"]].min(axis=1)).all(), error="Low must bound OHLC")],
    strict=False,
)


def validate_prices(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or not isinstance(frame.index, pd.DatetimeIndex) or frame.index.hasnans or frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError("Price data requires nonempty, unique chronological session timestamps")
    try:
        validated = PRICE_SCHEMA.validate(frame, lazy=True)
    except pa.errors.SchemaErrors as exc:
        # Do not embed provider payloads or credentials in API-visible errors.
        raise ValueError("Price data failed Pandera OHLCV validation") from exc
    validated.attrs.update(frame.attrs)
    validated.attrs["data_quality"] = {"engine": "pandera", "schema": "ohlcv-v1", "validated_rows": len(validated)}
    return cast(pd.DataFrame, validated)
