import pandas as pd
from stock_strategies.context import build_context_from_bundle


def _bundle(n=120):
    dates = pd.bdate_range("2022-01-03", periods=n)
    price = pd.DataFrame({"date": dates, "open": 1.0, "high": 1.0, "low": 1.0,
                          "close": [10.0 + i * 0.1 for i in range(n)], "volume": 1000})
    return {"price": price, "index": pd.DataFrame(), "inst": pd.DataFrame(),
            "revenue": pd.DataFrame(), "valuation": pd.DataFrame(), "margin": pd.DataFrame(),
            "shareholding": pd.DataFrame(), "fundamentals_raw": {"eps": {}, "roe": {}},
            "capital": {}}


def test_price_df_has_indicators():
    ctx = build_context_from_bundle("2330", pd.Timestamp("2022-12-31"), _bundle())
    for col in ["ma5", "ma20", "ma60", "bb_upper", "bb_lower", "k", "d", "macd_hist", "atr"]:
        assert col in ctx.price_df.columns


def test_fundamentals_raw_has_eps_q(monkeypatch):
    from stock_strategies import context as ctxmod
    fin = pd.DataFrame({"date": pd.to_datetime(["2023-03-31", "2023-06-30"]),
                        "type": ["EPS", "EPS"], "value": [2.5, 3.0]})
    monkeypatch.setattr(ctxmod, "fetch_finmind_cached", lambda *a, **k: fin.copy())
    out = ctxmod._get_fundamentals_raw("2330")
    assert "eps_q" in out
    assert out["eps_q"][(2023, 1)] == 2.5
    assert out["eps_q"][(2023, 2)] == 3.0


def test_eps_q_survives_bundle_pipeline():
    """eps_q 必須通過 build_context_from_bundle 進入 ctx.fundamentals（review Critical #1）。
    否則 growth.eps_yoy / growth.eps_accel 在真實路徑恆回 0.5。"""
    import pandas as pd
    from stock_strategies.context import build_context_from_bundle
    bundle = {"price": pd.DataFrame(), "index": pd.DataFrame(), "inst": pd.DataFrame(),
              "revenue": pd.DataFrame(), "valuation": pd.DataFrame(), "margin": pd.DataFrame(),
              "shareholding": pd.DataFrame(),
              "fundamentals_raw": {"eps": {}, "roe": {}, "eps_q": {(2023, 1): 2.5, (2023, 2): 3.0}},
              "capital": {}}
    ctx = build_context_from_bundle("test", pd.Timestamp("2024-05-20"), bundle)
    assert ctx.fundamentals.get("eps_q") == {(2023, 1): 2.5, (2023, 2): 3.0}
