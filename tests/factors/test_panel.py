"""build_panel 測試（§4 C7 因子欄攤平）。"""
import pandas as pd

from stock_strategies.factors.panel import build_panel


def test_build_panel_adds_factor_columns(monkeypatch):
    from stock_strategies import context as ctxmod
    dates = pd.bdate_range("2022-01-03", periods=300)
    price = pd.DataFrame({"date": dates, "open": 1.0,
                          "high": [10.0 + i * 0.1 for i in range(len(dates))], "low": 1.0,
                          "close": [10.0 + i * 0.1 for i in range(len(dates))], "volume": 1000})
    bundle = {"price": price, "index": pd.DataFrame(), "inst": pd.DataFrame(),
              "revenue": pd.DataFrame(), "valuation": pd.DataFrame(), "margin": pd.DataFrame(),
              "shareholding": pd.DataFrame(), "fundamentals_raw": {"eps": {}, "roe": {}}, "capital": {}}
    monkeypatch.setattr(ctxmod, "_gather_raw_bundle", lambda *a, **k: bundle)
    panel = build_panel(["2330"], ["momentum.dist_52w_high", "momentum.above_mas"],
                        as_of_dates=[pd.Timestamp("2022-12-30"), pd.Timestamp("2023-06-30")])
    assert "factor__momentum.dist_52w_high" in panel.columns
    assert "stock_id" in panel.columns and "date" in panel.columns
    assert len(panel) == 2


def test_build_panel_missing_data_is_nan(monkeypatch):
    """缺 required_data 的因子在 panel 應為 NaN（None→NaN 映射，review minor #5）。"""
    from stock_strategies import context as ctxmod
    dates = pd.bdate_range("2022-01-03", periods=300)
    price = pd.DataFrame({"date": dates, "open": 1.0, "high": 1.0, "low": 1.0,
                          "close": [10.0 + i * 0.1 for i in range(len(dates))], "volume": 1000})
    bundle = {"price": price, "index": pd.DataFrame(), "inst": pd.DataFrame(),  # inst 空
              "revenue": pd.DataFrame(), "valuation": pd.DataFrame(), "margin": pd.DataFrame(),
              "shareholding": pd.DataFrame(), "fundamentals_raw": {"eps": {}, "roe": {}, "eps_q": {}}, "capital": {}}
    monkeypatch.setattr(ctxmod, "_gather_raw_bundle", lambda *a, **k: bundle)
    panel = build_panel(["2330"], ["chips.foreign_buy_streak"],  # 需 inst → 缺 → None → NaN
                        as_of_dates=[pd.Timestamp("2022-12-30")])
    assert pd.isna(panel["factor__chips.foreign_buy_streak"].iloc[0])
