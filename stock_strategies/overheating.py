"""追高偵測 + 波動度自適應 — V5 學術級

引用：
  - Barroso & Santa-Clara (2015) "Momentum Has Its Moments"
    （波動度自適應倉位調整可讓 Sharpe 翻倍）
  - Connors & Alvarez "Short Term Trading Strategies That Work"
    （2 期 RSI 拉回入場）

3 大功能：
  1. detect_overheating()       — 連漲過多偵測（追高警告）
  2. detect_pullback_buy()      — Connors 2-RSI 拉回買訊號
  3. volatility_scale_multiplier() — Barroso 波動度自適應倉位

整合：
  - main.py 後加 apply_overheating_filter() → HIGH 風險 BUY 降 WATCH
  - kelly.py 把 position_pct × volatility_scale_multiplier
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .data import get_price_history


# ============================================================
# 1. 追高偵測（Overheating）
# ============================================================
def detect_overheating(stock_id: str, px: pd.DataFrame | None = None,
                       green_bars_threshold: int = 5,
                       rally_pct_threshold: float = 8.0) -> dict:
    """偵測股票是否處於「追高」狀態。

    判定條件：
      - 連續 N 根綠 K（close > open）
      - N 根內漲幅 > X%
      - 上影過長或量增

    回傳 dict：
      overheating: bool
      severity: "none" / "mild" / "strong"
      green_count: int
      rally_pct: float
      reasons: list[str]
    """
    result = {
        "overheating": False,
        "severity": "none",
        "green_count": 0,
        "rally_pct": 0.0,
        "reasons": [],
    }
    try:
        if px is None:
            px = get_price_history(stock_id, 1)
        if len(px) < 20:
            return result
    except Exception:
        return result

    px = px.copy()
    for c in ["open", "close", "high", "low", "volume"]:
        if c in px.columns:
            px[c] = pd.to_numeric(px[c], errors="coerce")

    # 連續綠 K 計數
    tail = px.tail(green_bars_threshold + 5)
    is_green = tail["close"] > tail["open"]
    green_count = 0
    for v in is_green.iloc[::-1].values:
        if v:
            green_count += 1
        else:
            break
    result["green_count"] = int(green_count)

    # N 根漲幅
    if len(px) > green_bars_threshold:
        cur = float(px.iloc[-1]["close"])
        base = float(px.iloc[-green_bars_threshold - 1]["close"])
        if base > 0:
            rally = (cur - base) / base * 100
            result["rally_pct"] = round(rally, 2)
        else:
            rally = 0
    else:
        rally = 0

    # 判定追高
    is_overheat = green_count >= green_bars_threshold and rally > rally_pct_threshold

    # 強度判斷
    severity = "none"
    if is_overheat:
        # 額外風險指標
        latest = px.iloc[-1]
        body = abs(float(latest["close"]) - float(latest["open"]))
        upper_shadow = float(latest["high"]) - max(float(latest["close"]), float(latest["open"]))
        vol_ma = float(px["volume"].tail(20).mean()) if len(px) >= 20 else 0
        vol_now = float(latest["volume"]) if not pd.isna(latest["volume"]) else 0

        extra_warnings = 0
        if body > 0 and upper_shadow > body * 2:
            extra_warnings += 1
            result["reasons"].append("爆量長上影")
        if vol_ma > 0 and vol_now > vol_ma * 2:
            extra_warnings += 1
            result["reasons"].append(f"量增 {vol_now/vol_ma*100-100:.0f}%")

        # RSI 14
        delta = px["close"].diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - 100 / (1 + rs)
        rsi_now = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50
        if rsi_now > 75:
            extra_warnings += 1
            result["reasons"].append(f"RSI {rsi_now:.0f} 超買")

        severity = "strong" if extra_warnings >= 2 else "mild"
        result["overheating"] = True
        result["severity"] = severity
        result["reasons"].insert(0, f"連漲 {green_count} 日 + 漲幅 {rally:.1f}%")

    return result


def apply_overheating_filter(results: list[dict]) -> dict:
    """對訊號清單套用追高過濾。
    強追高 BUY → 自動降 WATCH，輕追高 BUY → 標記警告。
    回傳 stats dict。
    """
    stats = {"strong_downgraded": 0, "mild_warned": 0}
    for r in results:
        if r.get("action") != "BUY":
            continue
        sid = r.get("stock_id")
        if not sid:
            continue
        try:
            check = detect_overheating(sid)
        except Exception:
            continue

        if not check["overheating"]:
            continue

        msg = f"🔥 追高偵測：{', '.join(check['reasons'])}"
        if check["severity"] == "strong":
            r["action"] = "WATCH"
            r.setdefault("risk_notes", []).insert(0, msg + "（強追高 → 自動降 WATCH）")
            stats["strong_downgraded"] += 1
        else:
            r.setdefault("risk_notes", []).append(msg + "（建議倉位 × 0.5）")
            r["overheat_position_mul"] = 0.5
            stats["mild_warned"] += 1

    return stats


# ============================================================
# 2. Connors 2-Period RSI 拉回偵測
# ============================================================
def detect_pullback_buy(stock_id: str, px: pd.DataFrame | None = None,
                       ma_len: int = 50,
                       rsi2_oversold: int = 20) -> dict:
    """Connors 2-RSI 拉回入場訊號。

    條件：
      1. 趨勢過濾：close > MA50 且 MA50 向上
      2. 拉回訊號：2 期 RSI 跌破 20
      3. 進場觸發：昨日 RSI2 < 20 且今天 close > open 且 close > 昨日 close

    回傳 dict：
      pullback_buy: bool
      reason: str
      rsi2: float
    """
    result = {"pullback_buy": False, "reason": "", "rsi2": None}
    try:
        if px is None:
            px = get_price_history(stock_id, 1)
        if len(px) < ma_len + 5:
            return result
    except Exception:
        return result

    px = px.copy()
    for c in ["open", "close"]:
        if c in px.columns:
            px[c] = pd.to_numeric(px[c], errors="coerce")

    ma50 = px["close"].rolling(ma_len).mean()
    cur_close = float(px.iloc[-1]["close"])
    cur_open = float(px.iloc[-1]["open"])
    prev_close = float(px.iloc[-2]["close"])
    ma50_now = float(ma50.iloc[-1])
    ma50_prev = float(ma50.iloc[-6]) if len(px) >= 6 else ma50_now

    # 2 期 RSI
    delta = px["close"].diff()
    gain = delta.clip(lower=0).rolling(2).mean()
    loss = (-delta.clip(upper=0)).rolling(2).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi2 = 100 - 100 / (1 + rs)
    rsi2_now = float(rsi2.iloc[-1]) if not pd.isna(rsi2.iloc[-1]) else 50
    rsi2_prev = float(rsi2.iloc[-2]) if not pd.isna(rsi2.iloc[-2]) else 50
    result["rsi2"] = round(rsi2_now, 1)

    # 趨勢上漲
    trend_up = cur_close > ma50_now and ma50_now > ma50_prev
    # 昨日超賣
    yesterday_oversold = rsi2_prev < rsi2_oversold
    # 今天反彈
    today_bounce = cur_close > cur_open and cur_close > prev_close

    if trend_up and yesterday_oversold and today_bounce:
        result["pullback_buy"] = True
        result["reason"] = (
            f"Connors 拉回買訊：MA{ma_len} 多頭 + 昨日 RSI2={rsi2_prev:.0f} 超賣 + 今日反彈"
        )

    return result


# ============================================================
# 3. Barroso 波動度自適應倉位
# ============================================================
def volatility_scale_multiplier(stock_id: str, px: pd.DataFrame | None = None,
                                base_len: int = 60) -> dict:
    """波動度自適應倉位倍數。

    根據 Barroso & Santa-Clara (2015)：
      高波動 → 倍數 < 1（減倉）
      低波動 → 倍數 > 1（加倉）

    公式：
      ATR_pct = ATR / close × 100
      ratio = ATR_pct / SMA(ATR_pct, 60)
      multiplier = 1 / ratio，夾擠在 [0.3, 1.5]

    回傳 dict：
      multiplier: float
      atr_pct: float
      ratio: float（>1 = 波動高、<1 = 波動低）
      note: str
    """
    result = {"multiplier": 1.0, "atr_pct": 0.0, "ratio": 1.0, "note": ""}
    try:
        if px is None:
            px = get_price_history(stock_id, 1)
        if len(px) < base_len + 14:
            return result
    except Exception:
        return result

    px = px.copy()
    for c in ["close", "high", "low"]:
        if c in px.columns:
            px[c] = pd.to_numeric(px[c], errors="coerce")

    # ATR 14
    high_low = px["high"] - px["low"]
    high_close = (px["high"] - px["close"].shift()).abs()
    low_close = (px["low"] - px["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    atr_pct = atr / px["close"] * 100

    atr_pct_now = float(atr_pct.iloc[-1])
    atr_base = float(atr_pct.tail(base_len).mean())

    if atr_base <= 0 or np.isnan(atr_pct_now) or np.isnan(atr_base):
        return result

    ratio = atr_pct_now / atr_base
    multiplier = max(0.3, min(1.5, 1.0 / ratio))

    result["atr_pct"] = round(atr_pct_now, 2)
    result["ratio"] = round(ratio, 2)
    result["multiplier"] = round(multiplier, 2)

    if multiplier < 0.7:
        result["note"] = f"高波動（{ratio:.1f}x 基準）→ 倉位 × {multiplier:.2f}"
    elif multiplier > 1.2:
        result["note"] = f"低波動（{ratio:.1f}x 基準）→ 倉位 × {multiplier:.2f}"
    else:
        result["note"] = f"波動正常 → 倉位 × {multiplier:.2f}"

    return result
