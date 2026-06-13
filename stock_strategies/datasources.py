"""各 FinMind dataset 的 point-in-time loader。

通則：每個 loader (a) 呼叫 fetch_finmind_cached；(b) rename 正規化；
(c) to_datetime + to_numeric(coerce)；(d) 依 as_of 切片（傳 end_date）；
(e) 空資料回空 DataFrame（不 raise），讓因子層判中性。
as_of 是避免 look-ahead 的單一機制。
"""
from __future__ import annotations

import pandas as pd

from .cache import fetch_finmind_cached, FinMindRateLimitError


def _require_cols(df: pd.DataFrame, cols: list[str]) -> bool:
    return all(c in df.columns for c in cols)


def get_institutional(stock_id: str, start: str, as_of: str | None = None) -> pd.DataFrame:
    """三大法人買賣超（日）。回欄位:
       date, foreign_net, trust_net, dealer_net, total_net（單位：股）。
    FinMind name 欄分桶：Foreign* → 外資、Investment_Trust → 投信、
    Dealer*（self+Hedging）→ 自營；net = buy - sell。"""
    try:
        df = fetch_finmind_cached(
            "TaiwanStockInstitutionalInvestorsBuySell", stock_id, start, end_date=as_of
        )
    except FinMindRateLimitError:
        return pd.DataFrame()
    if df.empty or not _require_cols(df, ["date", "name", "buy", "sell"]):
        return pd.DataFrame()
    df = df.copy()
    df["buy"] = pd.to_numeric(df["buy"], errors="coerce")
    df["sell"] = pd.to_numeric(df["sell"], errors="coerce")
    df["net"] = df["buy"] - df["sell"]

    def bucket(name: str) -> str:
        n = str(name)
        if n.startswith("Foreign"):
            return "foreign_net"
        if n.startswith("Investment_Trust"):
            return "trust_net"
        if n.startswith("Dealer"):
            return "dealer_net"
        return "other"

    df["bucket"] = df["name"].map(bucket)
    df = df[df["bucket"] != "other"]
    wide = df.pivot_table(index="date", columns="bucket", values="net",
                          aggfunc="sum", fill_value=0).reset_index()
    for col in ["foreign_net", "trust_net", "dealer_net"]:
        if col not in wide.columns:
            wide[col] = 0
    wide["total_net"] = wide["foreign_net"] + wide["trust_net"] + wide["dealer_net"]
    return wide[["date", "foreign_net", "trust_net", "dealer_net", "total_net"]].sort_values("date").reset_index(drop=True)


def get_month_revenue(stock_id: str, start: str, as_of: str | None = None) -> pd.DataFrame:
    """月營收（月）。回欄位:
       avail_date(資料可得日≈次月10日), revenue_year, revenue_month, revenue, mom, yoy。
    防 look-ahead：as_of 切片用 avail_date <= as_of（非所屬月）。"""
    try:
        # 月營收全抓（不在快取層用 as_of 切，因 avail_date 在此才算得出）
        df = fetch_finmind_cached("TaiwanStockMonthRevenue", stock_id, start)
    except FinMindRateLimitError:
        return pd.DataFrame()
    if df.empty or not _require_cols(df, ["revenue_year", "revenue_month", "revenue"]):
        return pd.DataFrame()
    df = df.copy()
    for c in ["revenue_year", "revenue_month", "revenue"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["revenue_year", "revenue_month"])
    df["period"] = pd.to_datetime(
        df["revenue_year"].astype(int).astype(str) + "-"
        + df["revenue_month"].astype(int).astype(str).str.zfill(2) + "-01"
    )
    # 公布日保守估：所屬月底 + 10 天（次月 10 日，法規上限）
    df["avail_date"] = df["period"] + pd.offsets.MonthEnd(0) + pd.Timedelta(days=10)
    df = df.sort_values("period").reset_index(drop=True)
    df["mom"] = df["revenue"].pct_change()
    df["yoy"] = df["revenue"].pct_change(periods=12)
    if as_of:
        df = df[df["avail_date"] <= pd.to_datetime(as_of)]
    return df[["avail_date", "period", "revenue_year", "revenue_month",
               "revenue", "mom", "yoy"]].reset_index(drop=True)


def get_valuation(stock_id: str, start: str, as_of: str | None = None) -> pd.DataFrame:
    """估值（日）。回 date, per, pbr, dividend_yield。per<=0(虧損)→NaN。
    缺關鍵欄位 → 回空（韌性，不 KeyError）。"""
    try:
        df = fetch_finmind_cached("TaiwanStockPER", stock_id, start, end_date=as_of)
    except FinMindRateLimitError:
        return pd.DataFrame()
    if df.empty:
        return pd.DataFrame()
    rename = {"PER": "per", "PBR": "pbr", "dividend_yield": "dividend_yield"}
    df = df.rename(columns=rename)
    if not _require_cols(df, ["date", "per", "pbr"]):
        return pd.DataFrame()
    df = df.copy()
    for c in ["per", "pbr", "dividend_yield"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df.loc[df["per"] <= 0, "per"] = pd.NA
    df["per"] = pd.to_numeric(df["per"], errors="coerce")
    keep = [c for c in ["date", "per", "pbr", "dividend_yield"] if c in df.columns]
    return df[keep].sort_values("date").reset_index(drop=True)


def get_margin(stock_id: str, start: str, as_of: str | None = None) -> pd.DataFrame:
    """融資融券（日）。回 date, margin_balance, short_balance,
       margin_chg, short_chg, short_margin_ratio(券資比)。"""
    try:
        df = fetch_finmind_cached(
            "TaiwanStockMarginPurchaseShortSale", stock_id, start, end_date=as_of
        )
    except FinMindRateLimitError:
        return pd.DataFrame()
    rename = {
        "MarginPurchaseTodayBalance": "margin_balance",
        "ShortSaleTodayBalance": "short_balance",
    }
    df = df.rename(columns=rename)
    if df.empty or not _require_cols(df, ["date", "margin_balance", "short_balance"]):
        return pd.DataFrame()
    df = df.copy()
    for c in ["margin_balance", "short_balance"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("date").reset_index(drop=True)
    df["margin_chg"] = df["margin_balance"].diff()
    df["short_chg"] = df["short_balance"].diff()
    df["short_margin_ratio"] = df["short_balance"] / df["margin_balance"].replace(0, pd.NA)
    return df[["date", "margin_balance", "short_balance",
               "margin_chg", "short_chg", "short_margin_ratio"]].reset_index(drop=True)


def get_shareholding(stock_id: str, start: str, as_of: str | None = None) -> pd.DataFrame:
    """外資持股比例（週/不定期）。回 date, foreign_ratio（pct）。
    頻率不規則 → 因子層用 asof_row 取最近一筆。"""
    try:
        df = fetch_finmind_cached("TaiwanStockShareholding", stock_id, start, end_date=as_of)
    except FinMindRateLimitError:
        return pd.DataFrame()
    df = df.rename(columns={"ForeignInvestmentSharesRatio": "foreign_ratio"})
    if df.empty or not _require_cols(df, ["date", "foreign_ratio"]):
        return pd.DataFrame()
    df = df.copy()
    df["foreign_ratio"] = pd.to_numeric(df["foreign_ratio"], errors="coerce")
    if as_of:
        df = df[df["date"] <= pd.to_datetime(as_of)]
    return df[["date", "foreign_ratio"]].sort_values("date").reset_index(drop=True)
