"""§7 §7 測試點 4-5：價值派。"""
import pandas as pd

import stock_strategies.factors.value  # noqa: F401  觸發註冊
from stock_strategies.context import FactorContext
from stock_strategies.factors.registry import compute_factor


def _ctx(valuation: pd.DataFrame) -> FactorContext:
    px = pd.DataFrame({"date": pd.bdate_range("2023-01-02", periods=80), "close": 10.0})
    return FactorContext(
        stock_id="x", as_of=pd.Timestamp("2024-01-01"),
        price_df=px, index_df=pd.DataFrame(), inst=pd.DataFrame(),
        revenue=pd.DataFrame(), valuation=valuation,
        margin=pd.DataFrame(), shareholding=pd.DataFrame(), fundamentals={},
    )


def _val(per=None, pbr=None, yld=None, n=60):
    dates = pd.bdate_range("2023-01-02", periods=n)
    data = {"date": dates}
    if per is not None:
        data["per"] = per
    if pbr is not None:
        data["pbr"] = pbr
    if yld is not None:
        data["dividend_yield"] = yld
    return pd.DataFrame(data)


# 測試點 4：value.cheap_pb
def test_cheap_pb_lowest_today_bullish():
    pbr = [5.0] * 59 + [1.0]  # 當日最低 → 越便宜越高
    ctx = _ctx(_val(pbr=pbr))
    assert compute_factor("value.cheap_pb", ctx, {}) > 0.9


def test_cheap_pb_highest_today_bearish():
    pbr = [1.0] * 59 + [5.0]  # 當日最高 → 越貴越低
    ctx = _ctx(_val(pbr=pbr))
    assert compute_factor("value.cheap_pb", ctx, {}) < 0.1


def test_cheap_pb_negative_neutral():
    pbr = [1.0] * 59 + [-2.0]  # pbr_t <= 0 → 0.5
    ctx = _ctx(_val(pbr=pbr))
    assert compute_factor("value.cheap_pb", ctx, {}) == 0.5


def test_cheap_pb_insufficient_sample_neutral():
    ctx = _ctx(_val(pbr=[1.0] * 10, n=10))  # <20 樣本 → 0.5
    assert compute_factor("value.cheap_pb", ctx, {}) == 0.5


def test_cheap_pb_missing_returns_none():
    ctx = _ctx(pd.DataFrame())  # required_data 整缺 → None
    assert compute_factor("value.cheap_pb", ctx, {}) is None


# value.cheap_pe
def test_cheap_pe_lowest_today_bullish():
    per = [30.0] * 59 + [8.0]
    ctx = _ctx(_val(per=per))
    assert compute_factor("value.cheap_pe", ctx, {}) > 0.9


def test_cheap_pe_loss_neutral():
    per = [10.0] * 59 + [-3.0]  # per_t <= 0（虧損）→ 0.5
    ctx = _ctx(_val(per=per))
    assert compute_factor("value.cheap_pe", ctx, {}) == 0.5


# 測試點 5：value.high_yield
def test_high_yield_highest_today_bullish():
    yld = [1.0] * 59 + [8.0]  # 殖利率當日最高 → ≈1.0
    ctx = _ctx(_val(yld=yld))
    assert compute_factor("value.high_yield", ctx, {}) > 0.9


def test_high_yield_no_dividend_slightly_bearish():
    yld = [3.0] * 59 + [0.0]  # 不配息 → 0.3
    ctx = _ctx(_val(yld=yld))
    assert compute_factor("value.high_yield", ctx, {}) == 0.3
