"""FactorContext（C1 唯一定義）與 point-in-time 建構器。

契約：欄位名一律 price_df/index_df；as_of 為 pd.Timestamp；
後續因子層/回測層一律 `from .context import FactorContext`，禁止 redefine。
price_df 進 ctx 時尚未 add_indicators，由消費端統一呼叫一次。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from .config import MIN_PRICE_ROWS
from . import datasources as ds
from .cache import fetch_finmind_cached, FinMindRateLimitError
from .indicators import add_indicators


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

    def asof_row(self, df_name: str, date_col: str = "date") -> pd.Series | None:
        """對不規則頻率資料取 <=as_of 的最後一筆。
        注意：revenue 的時間欄是 avail_date（非 date），須以
        asof_row("revenue", "avail_date") 取用，否則回 None。"""
        df = getattr(self, df_name, None)
        if df is None or df.empty or date_col not in df.columns:
            return None
        sub = df[df[date_col] <= self.as_of]
        return sub.iloc[-1] if len(sub) else None


def _fundamentals_asof(fund_raw: dict, as_of: pd.Timestamp) -> dict:
    """年度 EPS/ROE 以發布日切片：年度 y 的可用日 = (y+1)-03-31。
    eps_q（單季 EPS）原樣帶過、不在此截片——成長因子 growth._available_quarters
    已依 as_of 做各季 deadline 過濾（Q1→5/15…Q4→隔年3/31），無 look-ahead。"""
    out = {"eps": {}, "roe": {}, "eps_q": fund_raw.get("eps_q") or {}}
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
    if price_df is not None and not price_df.empty:
        price_df = add_indicators(price_df)

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

    # market_cap 一律以 as_of 切片後的最後收盤動態重算（股本/10 × 收盤），
    # 不用 bundle 內預存的市值——否則回測逐日切同一 bundle 時會用到最新市值（look-ahead）。
    shares = capital.get("shares_outstanding")
    market_cap = None
    if shares and price_df is not None and len(price_df) and "close" in price_df.columns:
        closes = pd.to_numeric(price_df["close"], errors="coerce").dropna()
        if len(closes):
            market_cap = shares / 10 * float(closes.iloc[-1])

    return FactorContext(
        stock_id=stock_id, as_of=as_of,
        price_df=price_df if price_df is not None else pd.DataFrame(),
        index_df=index_df, inst=inst, revenue=revenue, valuation=valuation,
        margin=margin, shareholding=shareholding, fundamentals=fundamentals,
        industry=capital.get("industry"),
        shares_outstanding=shares,
        market_cap=market_cap,
        meta=meta,
    )


def get_price_history_cached(stock_id: str, start: str, as_of: str | None = None) -> pd.DataFrame:
    """走快取的個股日 K（取代 data.get_price_history 在 context 內的用途）。"""
    df = fetch_finmind_cached("TaiwanStockPrice", stock_id, start, end_date=as_of)
    if df.empty:
        return df
    df = df.rename(columns={"max": "high", "min": "low", "Trading_Volume": "volume"})
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    keep = [c for c in ["date", "open", "high", "low", "close", "volume"] if c in df.columns]
    return df[keep].sort_values("date").reset_index(drop=True)


def _get_fundamentals_raw(stock_id: str) -> dict:
    """年度 EPS/ROE 原始值（不切發布日，由 from_bundle 切）。"""
    try:
        df = fetch_finmind_cached("TaiwanStockFinancialStatements", stock_id, "2015-01-01")
    except FinMindRateLimitError:
        return {"eps": {}, "roe": {}, "eps_q": {}}
    if df.empty or not all(c in df.columns for c in ["date", "type", "value"]):
        return {"eps": {}, "roe": {}, "eps_q": {}}
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["year"] = df["date"].dt.year
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    eps = df[df["type"] == "EPS"].groupby("year")["value"].sum().to_dict()
    roe = df[df["type"] == "ROE"].groupby("year")["value"].sum().to_dict()
    eps_rows = df[df["type"] == "EPS"].copy()
    eps_q = {}
    if not eps_rows.empty:
        eps_rows["quarter"] = eps_rows["date"].dt.month.map({3: 1, 6: 2, 9: 3, 12: 4})
        for _, r in eps_rows.dropna(subset=["quarter", "value"]).iterrows():
            eps_q[(int(r["year"]), int(r["quarter"]))] = round(float(r["value"]), 2)
    return {"eps": {int(y): round(float(v), 2) for y, v in eps.items()},
            "roe": {int(y): round(float(v), 2) for y, v in roe.items()},
            "eps_q": eps_q}


def _gather_raw_bundle(stock_id: str, start: str, lookback_years: int,
                       as_of: str | None = None) -> dict:
    """一次抓全期資料（回測前置）。時間序列不切 as_of（逐日切交給 from_bundle）；
    僅 capital(股本/產業) 以 as_of 取點，避免市值/股本用到未來資訊（look-ahead）。"""
    return {
        "price": get_price_history_cached(stock_id, start),
        "index": ds.get_index_history("TAIEX", start),
        "inst": ds.get_institutional(stock_id, start),
        "revenue": ds.get_month_revenue(stock_id, start),
        "valuation": ds.get_valuation(stock_id, start),
        "margin": ds.get_margin(stock_id, start),
        "shareholding": ds.get_shareholding(stock_id, start),
        "fundamentals_raw": _get_fundamentals_raw(stock_id),
        "capital": ds.get_capital_and_industry(stock_id, as_of=as_of),
    }


def build_context(
    stock_id: str,
    as_of_date: str,
    *,
    lookback_years: int = 5,
    info_df: pd.DataFrame | None = None,
    strict: bool = False,
) -> FactorContext:
    """runtime 單檔用：抓一次全期 → from_bundle 切到 as_of。
    strict=True 時資料缺漏 raise；False(預設) 記 meta 回中性。"""
    as_of = pd.Timestamp(as_of_date)
    start = (as_of - pd.DateOffset(years=lookback_years) - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
    try:
        bundle = _gather_raw_bundle(stock_id, start, lookback_years, as_of=as_of_date)
    except Exception:
        if strict:
            raise
        logging.getLogger(__name__).warning(
            "build_context(%s, %s) 抓取失敗，改回空 bundle", stock_id, as_of_date, exc_info=True
        )
        bundle = {"price": pd.DataFrame(), "index": pd.DataFrame(), "inst": pd.DataFrame(),
                  "revenue": pd.DataFrame(), "valuation": pd.DataFrame(), "margin": pd.DataFrame(),
                  "shareholding": pd.DataFrame(),
                  "fundamentals_raw": {"eps": {}, "roe": {}, "eps_q": {}},
                  "capital": {}}
    ctx = build_context_from_bundle(stock_id, as_of, bundle)
    if strict and ctx.meta.get("missing"):
        raise RuntimeError(f"build_context strict: 缺資料 {ctx.meta['missing']}")
    return ctx
