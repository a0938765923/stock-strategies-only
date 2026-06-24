"""Half-Kelly 倉位計算 — 業界標準的動態倉位

Kelly 公式：f* = (p × W - q) / W
  p = 勝率
  q = 1 - p（敗率）
  W = 平均贏 / 平均輸（贏輸比）

實務上業界**幾乎都不用全 Kelly**（太激進、回撤大），而是用：
  - Half-Kelly = f* / 2（業界主流，平衡成長與回撤）
  - Quarter-Kelly = f* / 4（保守派）

本模組回傳 Half-Kelly，並夾擠在 [1%, 20%] 之間（避免極端值）。

兩種輸入來源：
  1. 個股歷史 Performance（最準）
  2. 策略回測 winrate（沒歷史時的 fallback）
"""

from __future__ import annotations

POSITION_MIN = 0.01   # 最少 1%
POSITION_MAX = 0.20   # 最多 20%（避免重押）


def _safe_float(v, default: float | None = None) -> float | None:
    if v in (None, "", "—"):
        return default
    try:
        f = float(v)
        return f
    except (TypeError, ValueError):
        return default


def half_kelly(winrate: float, avg_win: float, avg_loss: float) -> float:
    """計算 Half-Kelly 比例（0~1）。

    winrate: 0~1
    avg_win: 平均贏的報酬（正數，例如 0.1 = +10%）
    avg_loss: 平均輸的報酬（正數，例如 0.05 = -5% → 傳 0.05）
    """
    if winrate <= 0 or winrate >= 1 or avg_win <= 0 or avg_loss <= 0:
        return POSITION_MIN  # 資料異常 → 給最小倉位

    win_loss_ratio = avg_win / avg_loss
    p = winrate
    q = 1 - p
    f_full = (p * win_loss_ratio - q) / win_loss_ratio
    if f_full <= 0:
        return POSITION_MIN  # 期望值負 → 最小倉位（其實該不進場）
    return max(POSITION_MIN, min(POSITION_MAX, f_full / 2))


def stock_history_stats(performance_rows: list[dict], stock_id: str) -> dict | None:
    """從 Performance 分頁找該股的歷史勝率與賠率。
    需要至少 3 筆完成追蹤的紀錄才回傳。"""
    rows = [
        r for r in performance_rows
        if str(r.get("stock_id", "")).strip() == str(stock_id).strip()
        and r.get("status") in ("DONE", "CLOSED", "完成")
    ]
    if len(rows) < 3:
        return None

    rets = []
    for r in rows:
        # 用 t20_ret 為主、退而求其次 t10、t5
        for key in ("t20_ret", "t10_ret", "t5_ret"):
            v = _safe_float(r.get(key))
            if v is not None:
                rets.append(v)
                break

    if len(rets) < 3:
        return None

    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    n = len(rets)
    p = len(wins) / n
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = -sum(losses) / len(losses) if losses else 0
    return {
        "samples": n,
        "winrate": p,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
    }


def position_for_signal(signal: dict, performance_rows: list[dict] | None = None) -> dict:
    """給定一個訊號，回傳建議倉位 dict:
        position_pct: 0~1
        source: "history" 或 "backtest" 或 "default"
        note: 中文說明
    """
    sid = signal.get("stock_id", "")
    components = signal.get("components", {}) or {}

    # 1. 優先用該股的真實歷史
    if performance_rows:
        stats = stock_history_stats(performance_rows, sid)
        if stats:
            f = half_kelly(stats["winrate"], stats["avg_win"], stats["avg_loss"])
            return {
                "position_pct": round(f, 4),
                "source": "history",
                "note": (
                    f"Half-Kelly (歷史 {stats['samples']} 筆 · "
                    f"勝率 {stats['winrate']*100:.0f}% · "
                    f"贏輸比 {stats['avg_win']/stats['avg_loss']:.2f})"
                    if stats["avg_loss"] > 0 else
                    f"Half-Kelly (歷史 {stats['samples']} 筆 · 勝率 {stats['winrate']*100:.0f}%)"
                ),
            }

    # 2. 退而用回測 winrate + 預設目標/停損當贏輸
    bt_winrate = _safe_float(components.get("backtest_winrate"))
    target = _safe_float(signal.get("target_price"))
    stop = _safe_float(signal.get("stop_loss_price"))
    entry = _safe_float(signal.get("entry_price"))

    if bt_winrate and entry and target and stop and target > entry > stop:
        # backtest_winrate 在 components 是 0~100，需 / 100
        if bt_winrate > 1:
            bt_winrate = bt_winrate / 100
        avg_win = (target - entry) / entry
        avg_loss = (entry - stop) / entry
        f = half_kelly(bt_winrate, avg_win, avg_loss)
        return {
            "position_pct": round(f, 4),
            "source": "backtest",
            "note": (
                f"Half-Kelly (回測勝率 {bt_winrate*100:.0f}% · "
                f"R/R {avg_win/avg_loss:.2f})"
            ),
        }

    # 3. 完全沒資料 → 給保守預設 5%
    return {
        "position_pct": 0.05,
        "source": "default",
        "note": "Half-Kelly 資料不足，給保守預設 5%",
    }
