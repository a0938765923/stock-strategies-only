"""因子工具：值域夾擠、百分位、z-score、缺料判定。"""
from __future__ import annotations

import numpy as np
import pandas as pd

NEUTRAL = 0.5


def clip01(x) -> float:
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return 0.5
    return float(min(1.0, max(0.0, x)))


def rank_pct(series: pd.Series, value: float) -> float:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) < 2 or value is None or pd.isna(value):
        return 0.5
    return float((s <= value).mean())


def zscore_clip(value, mean, std, lo=-2.0, hi=2.0) -> float:
    if std is None or std == 0 or pd.isna(std) or pd.isna(value) or pd.isna(mean):
        return 0.5
    z = max(lo, min(hi, (value - mean) / std))
    return (z - lo) / (hi - lo)


def has_rows(ctx, attr: str, min_rows: int = 1) -> bool:
    df = getattr(ctx, attr, None)
    return isinstance(df, pd.DataFrame) and len(df) >= min_rows
