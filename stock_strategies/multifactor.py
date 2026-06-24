"""多因子複合評分（業界標準 6 因子）

參考 FinLab、FinPilot 等量化平台慣用的多因子組合：
  1. 法人籌碼（外資 20 日累積淨買）
  2. 月營收 YoY 成長
  3. 動能 120 日漲幅
  4. 低波動 60 日報酬標準差（負相關）
  5. ROE（沿用 evaluate 已算的）
  6. 技術分（沿用 evaluate 已算的）

每因子標準化到 0~1（1 = 最好），加權平均後得到複合分數 0~100。
與 evaluate 原本的 signal_score 50/50 加權，產出最終分數。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .datasources import get_institutional, get_month_revenue
from .data import get_price_history


# 各因子權重（總和為 1.0）
DEFAULT_FACTOR_WEIGHTS = {
    "chips": 0.25,
    "revenue": 0.20,
    "momentum": 0.20,
    "lowvol": 0.15,
    "tech": 0.10,
    "fund": 0.10,
}


def _clip01(x) -> float:
    if x is None:
        return 0.5
    try:
        return float(max(0.0, min(1.0, x)))
    except (TypeError, ValueError):
        return 0.5


def _factor_chips(stock_id: str) -> tuple[float, str]:
    """外資 20 日累積淨買量化分數。"""
    try:
        start = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
        inst = get_institutional(stock_id, start)
        if inst.empty or "foreign_net" in inst.columns and len(inst) < 5:
            return 0.5, "資料不足"
        last_n = inst.tail(20)
        cum = float(last_n["foreign_net"].sum())
        # cum > 5M 股 → 1.0；cum < -5M → 0.0
        if cum > 0:
            score = _clip01(0.5 + min(0.5, cum / 5_000_000))
        else:
            score = _clip01(0.5 - min(0.5, abs(cum) / 5_000_000))
        return score, f"外資 {len(last_n)} 日累積 {int(cum/1000):+,}K"
    except Exception:
        return 0.5, "抓不到"


def _factor_revenue(stock_id: str) -> tuple[float, str]:
    """月營收 YoY 分數。"""
    try:
        rev_start = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
        rev = get_month_revenue(stock_id, rev_start)
        if rev.empty or "yoy" not in rev.columns:
            return 0.5, "資料不足"
        yoy = rev.iloc[-1].get("yoy")
        if yoy is None or pd.isna(yoy):
            return 0.5, "資料不足"
        # +20% -> 1.0；-20% -> 0.0；線性
        score = _clip01((float(yoy) + 0.2) / 0.4)
        return score, f"YoY {float(yoy)*100:+.1f}%"
    except Exception:
        return 0.5, "抓不到"


def _factor_momentum_and_vol(stock_id: str, px: pd.DataFrame | None = None) -> tuple[tuple[float, str], tuple[float, str]]:
    """120 日動能 + 60 日低波動，共用一次價格資料。回 ((mom_score, mom_note), (vol_score, vol_note))。"""
    try:
        if px is None:
            px = get_price_history(stock_id, 1)
        if px is None or len(px) < 120:
            return (0.5, "資料不足"), (0.5, "資料不足")

        # 動能：120 日漲跌幅
        ret120 = (px.iloc[-1]["close"] - px.iloc[-120]["close"]) / px.iloc[-120]["close"]
        # +30% -> 1.0；-30% -> 0.0
        mom_score = _clip01((float(ret120) + 0.3) / 0.6)
        mom_note = f"120日 {float(ret120)*100:+.1f}%"

        # 低波動：60 日報酬標準差。日波動 < 1% -> 1.0，> 3% -> 0.0
        rets = px["close"].pct_change().tail(60).dropna()
        if len(rets) < 30:
            return (mom_score, mom_note), (0.5, "資料不足")
        vol = float(rets.std())
        vol_score = _clip01((0.03 - vol) / 0.02)
        vol_note = f"60日σ {vol*100:.2f}%"

        return (mom_score, mom_note), (vol_score, vol_note)
    except Exception:
        return (0.5, "錯誤"), (0.5, "錯誤")


def compute_factor_scores(stock_id: str) -> dict:
    """為一檔個股算 6 因子分數（不含 tech / fund，那兩個由 evaluate 帶入）。
    回 dict: {chips, revenue, momentum, lowvol, notes}
    """
    chips_score, chips_note = _factor_chips(stock_id)
    rev_score, rev_note = _factor_revenue(stock_id)
    (mom_score, mom_note), (vol_score, vol_note) = _factor_momentum_and_vol(stock_id)

    return {
        "chips": chips_score,
        "revenue": rev_score,
        "momentum": mom_score,
        "lowvol": vol_score,
        "notes": {
            "chips": chips_note,
            "revenue": rev_note,
            "momentum": mom_note,
            "lowvol": vol_note,
        },
    }


def composite_score(factor_scores: dict, tech_score: float, fund_score: float,
                    weights: dict | None = None) -> float:
    """6 因子加權平均 → 複合分數 0~100。
    tech_score / fund_score 來自 evaluate 已算的部分（0~100）。
    """
    w = weights or DEFAULT_FACTOR_WEIGHTS
    s = (
        factor_scores.get("chips", 0.5) * 100 * w["chips"]
        + factor_scores.get("revenue", 0.5) * 100 * w["revenue"]
        + factor_scores.get("momentum", 0.5) * 100 * w["momentum"]
        + factor_scores.get("lowvol", 0.5) * 100 * w["lowvol"]
        + float(tech_score) * w["tech"]
        + float(fund_score) * w["fund"]
    )
    return round(s, 1)


def apply_multifactor_filter(results: list[dict]) -> dict:
    """對 multifactor 策略的訊號加上 6 因子複合分數，並依分數調整 action。

    調整規則：
      - 複合分數 < 50 且原本 BUY → 降 WATCH
      - 複合分數 ≥ 75 → 在 risk_notes 加 ⭐ 高分強訊號標記
      - signal_score 更新為複合分數（取代原本）

    回傳 stats: {evaluated, demoted, strong}
    """
    stats = {"evaluated": 0, "demoted": 0, "strong": 0}
    for r in results:
        if r.get("strategy_id") != "multifactor":
            continue
        sid = r.get("stock_id")
        if not sid:
            continue
        fscores = compute_factor_scores(sid)
        r["multifactor"] = fscores

        # 沿用 evaluate 算的技術分與基本面分（在 components）
        comp = r.get("components", {}) or {}
        tech_s = float(comp.get("tech_score", 50) or 50)
        fund_s = float(comp.get("fund_score", 50) or 50)
        composite = composite_score(fscores, tech_s, fund_s)
        r["signal_score"] = composite

        # action 調整
        if composite < 50 and r.get("action") == "BUY":
            r["action"] = "WATCH"
            r.setdefault("risk_notes", []).append(
                f"多因子複合分 {composite:.0f}/100 偏低，降 WATCH"
            )
            stats["demoted"] += 1
        elif composite >= 75:
            r.setdefault("risk_notes", []).append(
                f"⭐ 多因子複合分 {composite:.0f}/100 高分（"
                f"籌 {fscores['chips']*100:.0f} · "
                f"營 {fscores['revenue']*100:.0f} · "
                f"動 {fscores['momentum']*100:.0f} · "
                f"波 {fscores['lowvol']*100:.0f}）"
            )
            stats["strong"] += 1

        stats["evaluated"] += 1

    return stats
