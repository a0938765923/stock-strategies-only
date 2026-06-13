"""FactorContext（C1 唯一定義）與 point-in-time 建構器。

契約：欄位名一律 price_df/index_df；as_of 為 pd.Timestamp；
後續因子層/回測層一律 `from .context import FactorContext`，禁止 redefine。
price_df 進 ctx 時尚未 add_indicators，由消費端統一呼叫一次。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .config import MIN_PRICE_ROWS


@dataclass
class FactorContext:
    stock_id: str
    as_of: pd.Timestamp
    price_df: pd.DataFrame
    index_df: pd.DataFrame
    inst: pd.DataFrame
    revenue: pd.DataFrame
    valuation: pd.DataFrame
    margin: pd.DataFrame
    shareholding: pd.DataFrame
    fundamentals: dict
    industry: str | None = None
    shares_outstanding: float | None = None
    market_cap: float | None = None
    meta: dict = field(default_factory=dict)

    def latest_price(self) -> pd.Series | None:
        """取 date<=as_of 的最後一筆報價（停牌則為停牌前最後成交）。"""
        if self.price_df is None or self.price_df.empty:
            return None
        df = self.price_df
        if "date" in df.columns:
            df = df[df["date"] <= self.as_of]
        return df.iloc[-1] if len(df) else None

    def asof_row(self, df_name: str) -> pd.Series | None:
        """對不規則頻率資料取 date<=as_of 的最後一筆。"""
        df = getattr(self, df_name, None)
        if df is None or df.empty or "date" not in df.columns:
            return None
        sub = df[df["date"] <= self.as_of]
        return sub.iloc[-1] if len(sub) else None


def _fundamentals_asof(fund_raw: dict, as_of: pd.Timestamp) -> dict:
    """年度 EPS/ROE 以發布日切片：年度 y 的可用日 = (y+1)-03-31。"""
    out = {"eps": {}, "roe": {}}
    for key in ("eps", "roe"):
        for year, val in (fund_raw.get(key) or {}).items():
            publish = pd.Timestamp(year=int(year) + 1, month=3, day=31)
            if publish <= as_of:
                out[key][int(year)] = val
    return out


def _slice_to(df: pd.DataFrame, as_of: pd.Timestamp, date_col: str = "date") -> pd.DataFrame:
    if df is None or df.empty or date_col not in df.columns:
        return df if df is not None else pd.DataFrame()
    return df[df[date_col] <= as_of].reset_index(drop=True)


def build_context_from_bundle(
    stock_id: str, as_of: pd.Timestamp, raw_bundle: dict
) -> FactorContext:
    """純切片組裝（無 IO）。回測逐日呼叫；raw_bundle 為一次抓好的全期資料。
    各資料塊一律以 as_of 為硬上界；月營收用 avail_date、財報用發布日。"""
    as_of = pd.Timestamp(as_of)
    meta: dict = {"warnings": [], "missing": []}

    price_df = _slice_to(raw_bundle.get("price", pd.DataFrame()), as_of)
    if price_df is None or len(price_df) < MIN_PRICE_ROWS:
        meta["missing"].append("price_history_insufficient")

    index_df = _slice_to(raw_bundle.get("index", pd.DataFrame()), as_of)
    inst = _slice_to(raw_bundle.get("inst", pd.DataFrame()), as_of)
    # 月營收以 avail_date 切（loader 已算 avail_date 欄）
    rev = raw_bundle.get("revenue", pd.DataFrame())
    revenue = _slice_to(rev, as_of, date_col="avail_date") if "avail_date" in getattr(rev, "columns", []) else _slice_to(rev, as_of)
    valuation = _slice_to(raw_bundle.get("valuation", pd.DataFrame()), as_of)
    margin = _slice_to(raw_bundle.get("margin", pd.DataFrame()), as_of)
    shareholding = _slice_to(raw_bundle.get("shareholding", pd.DataFrame()), as_of)
    fundamentals = _fundamentals_asof(raw_bundle.get("fundamentals_raw", {}), as_of)
    capital = raw_bundle.get("capital", {}) or {}

    for name, df in [("inst", inst), ("revenue", revenue), ("valuation", valuation),
                     ("margin", margin), ("shareholding", shareholding)]:
        if df is None or df.empty:
            meta["missing"].append(name)

    return FactorContext(
        stock_id=stock_id, as_of=as_of,
        price_df=price_df if price_df is not None else pd.DataFrame(),
        index_df=index_df, inst=inst, revenue=revenue, valuation=valuation,
        margin=margin, shareholding=shareholding, fundamentals=fundamentals,
        industry=capital.get("industry"),
        shares_outstanding=capital.get("shares_outstanding"),
        market_cap=capital.get("market_cap"),
        meta=meta,
    )
