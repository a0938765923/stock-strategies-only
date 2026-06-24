"""ELITE 策略額外品質過濾：籌碼面 + 營收面。

ELITE 策略在 evaluate() 給出 BUY 後，會額外查：
  1. 外資 20 日累積淨買 > 0（避免籌碼鬆動）
  2. 近月營收 YoY > -5%（避免基本面惡化）
任一不過關 → BUY 降為 WATCH，並在 risk_notes 註明原因。

WATCH 不額外過濾（讓使用者保留觀察的可能）。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .datasources import get_institutional, get_month_revenue


def elite_quality_check(stock_id: str) -> tuple[bool, list[str]]:
    """ELITE 額外品質檢查。回傳 (是否通過, 失敗原因清單)。

    失敗原因為 None 時代表那項資料抓不到（不算扣分，但記下來）。
    """
    issues: list[str] = []

    # 1. 外資 20 日累積淨買必須 > 0
    try:
        start = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
        inst = get_institutional(stock_id, start)
        if not inst.empty and "foreign_net" in inst.columns and len(inst) >= 5:
            last_n = inst.tail(20)
            cum = float(last_n["foreign_net"].sum())
            if cum < 0:
                issues.append(f"外資近 {len(last_n)} 日累積淨賣 {int(cum/1000):,}K 股")
    except Exception as e:
        # 抓不到資料 → 不扣分
        pass

    # 2. 近月營收 YoY > -5%
    try:
        rev_start = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
        rev = get_month_revenue(stock_id, rev_start)
        if not rev.empty and "yoy" in rev.columns:
            yoy = rev.iloc[-1].get("yoy")
            if yoy is not None and yoy < -0.05:
                issues.append(f"近月營收 YoY {yoy * 100:+.1f}% 衰退")
    except Exception:
        pass

    return len(issues) == 0, issues


def apply_elite_filter(results: list[dict]) -> int:
    """對 ELITE 策略產出的 BUY 訊號做額外過濾。
    BUY 不過關 → 降為 WATCH 並記 risk_notes。
    回傳被降級的數量。
    """
    downgraded = 0
    for r in results:
        if r.get("strategy_id") != "elite":
            continue
        if r.get("action") != "BUY":
            continue
        ok, issues = elite_quality_check(r["stock_id"])
        if not ok:
            r["action"] = "WATCH"
            r.setdefault("risk_notes", []).extend(
                [f"ELITE 過濾：{x}" for x in issues]
            )
            downgraded += 1
    return downgraded
