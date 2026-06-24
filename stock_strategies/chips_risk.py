"""籌碼風險偵測 — 主力「邊拉邊出」、融資暴增、券資異常

3 種典型頂部訊號：
  1. 主力邊拉邊出：股價創 20 日新高、但外資 5 日累積轉賣
  2. 融資暴增警示：5 日融資餘額 +20% 以上（散戶接刀）
  3. 券資比異常：> 60% 或 < 10%（軋空/極弱）

回 list[str]，每行一則警示。沒有警示就回空 list。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from .datasources import get_institutional
from .data import get_price_history


def _safe_float(v, default=None):
    if v in (None, "", "—"):
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def detect_chip_risks(stock_id: str) -> list[str]:
    """回傳該股的籌碼風險警示清單"""
    warnings: list[str] = []

    # 1. 主力邊拉邊出檢測
    try:
        px = get_price_history(stock_id, 1)
        if len(px) >= 20:
            cur_close = float(px.iloc[-1]["close"])
            high20 = float(px["close"].tail(20).max())
            # 近 3 日內有觸及 20 日新高？
            recent_high = float(px["close"].tail(3).max())
            near_high = recent_high >= high20 * 0.98

            if near_high:
                start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
                inst = get_institutional(stock_id, start)
                if not inst.empty and "foreign_net" in inst.columns and len(inst) >= 5:
                    last5_foreign = float(inst.tail(5)["foreign_net"].sum())
                    if last5_foreign < 0:
                        warnings.append(
                            f"🚨 主力邊拉邊出：股價接近 20 日高 {high20:.1f}，"
                            f"但外資近 5 日累積淨賣 {int(last5_foreign/1000):,}K 股"
                        )
    except Exception:
        pass

    # 2. 融資暴增（散戶接刀警示）
    try:
        from .datasources import fetch_finmind_cached
        start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        margin = fetch_finmind_cached("TaiwanStockMarginPurchaseShortSale", stock_id, start)
        if not margin.empty and "MarginPurchaseTodayBalance" in margin.columns:
            bal = pd.to_numeric(margin["MarginPurchaseTodayBalance"], errors="coerce").dropna()
            if len(bal) >= 6:
                latest = float(bal.iloc[-1])
                base = float(bal.iloc[-6])  # 5 日前
                if base > 0:
                    growth = (latest - base) / base
                    if growth > 0.20:
                        warnings.append(
                            f"⚠️ 融資暴增：5 日內融資餘額 +{growth*100:.1f}%（散戶大舉買進，主力可能準備出貨）"
                        )
    except Exception:
        pass

    # 3. 券資比異常
    try:
        if not margin.empty:
            margin_cols = [
                "MarginPurchaseTodayBalance",  # 融資餘額
                "ShortSaleTodayBalance",        # 融券餘額
            ]
            if all(c in margin.columns for c in margin_cols):
                latest = margin.iloc[-1]
                m = _safe_float(latest.get("MarginPurchaseTodayBalance"), 0) or 0
                s = _safe_float(latest.get("ShortSaleTodayBalance"), 0) or 0
                if m > 0:
                    ratio = s / m * 100
                    if ratio > 60:
                        warnings.append(
                            f"🟠 券資比 {ratio:.1f}%（極高，軋空行情接近尾聲、隨時可能崩）"
                        )
                    elif ratio < 5 and s > 0:
                        warnings.append(
                            f"🔵 券資比 {ratio:.1f}%（極低，融券回補力道弱）"
                        )
    except Exception:
        pass

    return warnings
