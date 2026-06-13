import pandas as pd
from stock_strategies import datasources as ds


def test_shareholding_normalize_and_asof(monkeypatch):
    raw = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-05", "2024-01-12", "2024-01-19"]),
        "ForeignInvestmentSharesRatio": [40.1, 40.5, 41.0],
    })
    monkeypatch.setattr(ds, "fetch_finmind_cached", lambda *a, **k: raw.copy())
    out = ds.get_shareholding("2330", "2024-01-01", as_of="2024-01-15")
    assert list(out.columns) == ["date", "foreign_ratio"]
    assert out["date"].max() <= pd.Timestamp("2024-01-15")  # 不含 01-19
    assert out.iloc[-1]["foreign_ratio"] == 40.5
