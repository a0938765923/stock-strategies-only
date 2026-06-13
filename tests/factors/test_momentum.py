"""§7 §7 測試點 8-10：動能派。"""
import pandas as pd

import stock_strategies.factors.momentum  # noqa: F401  觸發註冊
from stock_strategies.context import FactorContext
from stock_strategies.indicators import add_indicators
from stock_strategies.factors.registry import compute_factor


def _ctx_from_prices(closes, highs=None, n_indicators=True) -> FactorContext:
    n = len(closes)
    highs = highs if highs is not None else closes
    px = pd.DataFrame({
        "date": pd.bdate_range("2022-01-03", periods=n),
        "open": closes, "high": highs,
        "low": [c * 0.98 for c in closes], "close": closes,
        "volume": [1000] * n,
    })
    if n_indicators:
        px = add_indicators(px)
    return FactorContext(
        stock_id="x", as_of=pd.Timestamp("2024-01-01"),
        price_df=px, index_df=pd.DataFrame(), inst=pd.DataFrame(),
        revenue=pd.DataFrame(), valuation=pd.DataFrame(),
        margin=pd.DataFrame(), shareholding=pd.DataFrame(), fundamentals={},
    )


# 測試點 8：momentum.dist_52w_high
def test_dist_52w_high_at_peak():
    # close == 252日最高 → 1.0
    closes = [10.0 + i * 0.1 for i in range(260)]  # 單調上升，當日為最高
    ctx = _ctx_from_prices(closes)
    assert compute_factor("momentum.dist_52w_high", ctx, {}) == 1.0


def test_dist_52w_high_at_70pct():
    # close == 0.7 * 高 → 0.0
    closes = [100.0] * 259 + [70.0]
    ctx = _ctx_from_prices(closes, highs=[100.0] * 259 + [70.0])
    assert abs(compute_factor("momentum.dist_52w_high", ctx, {}) - 0.0) < 0.01


def test_dist_52w_high_at_85pct():
    # close == 0.85 * 高 → 0.5
    closes = [100.0] * 259 + [85.0]
    ctx = _ctx_from_prices(closes, highs=[100.0] * 259 + [85.0])
    assert abs(compute_factor("momentum.dist_52w_high", ctx, {}) - 0.5) < 0.01


def test_dist_52w_high_missing_returns_none():
    ctx = FactorContext(
        stock_id="x", as_of=pd.Timestamp("2024-01-01"),
        price_df=pd.DataFrame(), index_df=pd.DataFrame(), inst=pd.DataFrame(),
        revenue=pd.DataFrame(), valuation=pd.DataFrame(),
        margin=pd.DataFrame(), shareholding=pd.DataFrame(), fundamentals={},
    )
    assert compute_factor("momentum.dist_52w_high", ctx, {}) is None


def test_dist_52w_high_too_few_rows_returns_none():
    # < lookback_min(60) → registry 回 None
    closes = [10.0] * 30
    ctx = _ctx_from_prices(closes)
    assert compute_factor("momentum.dist_52w_high", ctx, {}) is None


# 測試點 9：momentum.above_mas
def test_above_mas_full_alignment():
    # 單調上升 → close>ma5>ma20>ma60 全中 → 1.0
    closes = [10.0 + i * 0.5 for i in range(80)]
    ctx = _ctx_from_prices(closes)
    assert compute_factor("momentum.above_mas", ctx, {}) == 1.0


def test_above_mas_full_breakdown():
    # 單調下跌 → close<ma5<ma20<ma60 全跌破 → 0.0
    closes = [100.0 - i * 0.5 for i in range(80)]
    ctx = _ctx_from_prices(closes)
    assert compute_factor("momentum.above_mas", ctx, {}) == 0.0


# 測試點 10：momentum.ma_slope
def test_ma_slope_rising_bullish():
    # 前段平盤、近段轉強加速 → 最新 MA20 斜率高於近 120 日均值 → >0.5
    closes = [50.0] * 120 + [50.0 + i ** 1.6 for i in range(1, 31)]
    ctx = _ctx_from_prices(closes)
    assert compute_factor("momentum.ma_slope", ctx, {}) > 0.5


def test_ma_slope_falling_bearish():
    # 前段平盤、近段轉弱加速下跌 → 最新 MA20 斜率低於近 120 日均值 → <0.5
    closes = [50.0] * 120 + [50.0 - i ** 1.6 for i in range(1, 31)]
    ctx = _ctx_from_prices(closes)
    assert compute_factor("momentum.ma_slope", ctx, {}) < 0.5


# momentum.rs_self
def test_rs_self_strong_bullish():
    # 前段平緩、後段加速複利上升 → 最新 60 日報酬率為歷史最高
    closes = [10.0 + i * 0.005 for i in range(200)] + [11.0 * (1.02 ** i) for i in range(120)]
    ctx = _ctx_from_prices(closes)
    assert compute_factor("momentum.rs_self", ctx, {}) > 0.7


def test_rs_self_weak_bearish():
    # 前段平緩上升、後段加速崩跌 → 最新 60 日報酬率為歷史最低
    closes = [10.0 + i * 0.05 for i in range(200)] + [20.0 * (0.98 ** i) for i in range(120)]
    ctx = _ctx_from_prices(closes)
    assert compute_factor("momentum.rs_self", ctx, {}) < 0.3
