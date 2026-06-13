import pandas as pd
from stock_strategies.factors.base import clip01, rank_pct, zscore_clip


def test_clip01_bounds():
    assert clip01(1.5) == 1.0
    assert clip01(-0.2) == 0.0
    assert clip01(float("nan")) == 0.5
    assert clip01(None) == 0.5


def test_rank_pct():
    s = pd.Series([1, 2, 3, 4])
    assert rank_pct(s, 4) == 1.0
    assert rank_pct(s, 1) == 0.25
    assert rank_pct(pd.Series([], dtype=float), 1) == 0.5


def test_zscore_clip():
    assert zscore_clip(10, 10, 2) == 0.5
    assert zscore_clip(14, 10, 2) == 1.0
    assert zscore_clip(10, 10, 0) == 0.5
