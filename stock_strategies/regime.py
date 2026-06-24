"""市場 Regime 偵測 — BULL / BEAR / SIDEWAYS

業界標準做法（參考 FinPilot 等量化平台）：用多指標複合判定大盤狀態，
而不是只看單一均線。三種 Regime 對應不同的操作策略。

評分組件：
  1. 價格 vs MA200（長期趨勢）
  2. MA50 斜率（中期動能）
  3. MA20/MA60 排列（短中期一致性）
  4. ATR/Close 波動率（區分趨勢 vs 盤整）

分數對照：
  ≥ 70: BULL（牛市）— BUY 照常、可加碼
  30~70: SIDEWAYS（盤整）— 只發前 3 名最強 BUY
  < 30: BEAR（熊市）— BUY 全降 WATCH
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .datasources import get_index_history


def _safe_float(v) -> float | None:
    try:
        f = float(v)
        return f if not (np.isnan(f) or np.isinf(f)) else None
    except (TypeError, ValueError):
        return None


def get_market_regime(index_id: str = "TAIEX") -> dict:
    """偵測大盤 Regime。回傳 dict:
        regime: "BULL" / "BEAR" / "SIDEWAYS"
        score: 0~100
        components: 各組件分數細項
        note: 給訊息用的單行說明
    """
    try:
        df = get_index_history(index_id)
        if df is None or len(df) < 220:
            return {
                "regime": "SIDEWAYS",
                "score": 50,
                "components": {},
                "note": "⚠️ 大盤資料不足（< 220 日），預設盤整",
            }
    except Exception as e:
        return {
            "regime": "SIDEWAYS",
            "score": 50,
            "components": {},
            "note": f"⚠️ Regime 偵測失敗（{str(e)[:60]}），預設盤整",
        }

    df = df.copy().sort_values("date").reset_index(drop=True)
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma50"] = df["close"].rolling(50).mean()
    df["ma60"] = df["close"].rolling(60).mean()
    df["ma200"] = df["close"].rolling(200).mean()
    df["tr"] = (df["high"] - df["low"]).abs()
    df["atr14"] = df["tr"].rolling(14).mean()

    latest = df.iloc[-1]
    close = _safe_float(latest["close"])
    ma20 = _safe_float(latest["ma20"])
    ma50_now = _safe_float(latest["ma50"])
    ma50_prev = _safe_float(df.iloc[-21]["ma50"]) if len(df) >= 21 else None
    ma60 = _safe_float(latest["ma60"])
    ma200 = _safe_float(latest["ma200"])
    atr = _safe_float(latest["atr14"])

    components: dict = {}
    score = 0.0

    # 1. 價格 vs MA200（長期趨勢）— 權重 30
    if close and ma200:
        diff = (close - ma200) / ma200
        # +5% 以上拿滿分；-5% 以下拿 0；中間線性
        s1 = max(0.0, min(1.0, (diff + 0.05) / 0.10)) * 30
        components["price_vs_ma200"] = round(s1, 1)
        score += s1

    # 2. MA50 斜率（中期動能）— 權重 25
    if ma50_now and ma50_prev:
        slope = (ma50_now - ma50_prev) / ma50_prev
        # +3% 以上拿滿；-3% 拿 0
        s2 = max(0.0, min(1.0, (slope + 0.03) / 0.06)) * 25
        components["ma50_slope"] = round(s2, 1)
        score += s2

    # 3. 均線排列 MA20 vs MA60（短中期一致性）— 權重 25
    if ma20 and ma60:
        if ma20 > ma60 * 1.01:
            s3 = 25
        elif ma20 < ma60 * 0.99:
            s3 = 0
        else:
            s3 = 12  # 糾結 → 中性
        components["ma20_vs_ma60"] = s3
        score += s3

    # 4. 波動率（區分趨勢 vs 盤整）— 權重 20
    if atr and close:
        vol_pct = atr / close
        # ATR/Close < 1% → 低波動（給高分代表趨勢明確），> 2.5% → 高波動（低分代表盪）
        if vol_pct < 0.01:
            s4 = 20
        elif vol_pct > 0.025:
            s4 = 5
        else:
            s4 = 20 - (vol_pct - 0.01) / 0.015 * 15
        components["volatility"] = round(s4, 1)
        score += s4

    score = round(score, 1)

    # 分類
    if score >= 70:
        regime = "BULL"
        emoji = "🐂"
        action = "BUY 照常發出、可加碼倉位"
    elif score < 30:
        regime = "BEAR"
        emoji = "🐻"
        action = "BUY 全數降 WATCH、減碼避險"
    else:
        regime = "SIDEWAYS"
        emoji = "🦘"
        action = "只發前 3 名最強 BUY、不追高"

    note = f"{emoji} 大盤 Regime: *{regime}* ({score:.0f}/100) → {action}"

    return {
        "regime": regime,
        "score": score,
        "components": components,
        "note": note,
    }


def apply_regime_filter(results: list[dict], regime: dict) -> dict:
    """依 Regime 對訊號做風控調整。

    BULL → 不動
    BEAR → 所有 BUY 降為 WATCH
    SIDEWAYS → 只保留前 N 名最強 BUY（依 signal_score）

    回傳 stats dict: {bear_downgraded, sideways_kept}
    """
    stats = {"bear_downgraded": 0, "sideways_kept": 0}
    if not regime or not results:
        return stats

    r_type = regime.get("regime", "SIDEWAYS")
    if r_type == "BEAR":
        for r in results:
            if r.get("action") == "BUY":
                r["action"] = "WATCH"
                r.setdefault("risk_notes", []).append(
                    f"Regime=BEAR ({regime.get('score', 0):.0f}/100)，BUY 自動降 WATCH"
                )
                stats["bear_downgraded"] += 1
    elif r_type == "SIDEWAYS":
        top_n = 3
        buys = sorted(
            [r for r in results if r.get("action") == "BUY"],
            key=lambda x: -x.get("signal_score", 0),
        )
        keep_ids = {r["stock_id"] for r in buys[:top_n]}
        stats["sideways_kept"] = len(keep_ids)
        for r in buys[top_n:]:
            r["action"] = "WATCH"
            r.setdefault("risk_notes", []).append(
                f"Regime=SIDEWAYS，僅保留前 {top_n} 名最強 BUY，本檔降 WATCH"
            )

    return stats
