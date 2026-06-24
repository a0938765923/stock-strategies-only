"""技術面警示偵測

4 種典型反轉訊號：
  1. RSI 背離：股價創高、RSI 沒創高 → 動能衰竭
  2. MACD 背離：股價創高、MACD 柱沒創高
  3. 爆量長上影線：今日上影線長度 > K 棒實體 2 倍 + 量增 50%
  4. 跌破 5MA + 爆量：收盤跌破 5 日均線且量增 30%

回 list[str]，每行一則警示。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .data import get_price_history


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd_hist(series: pd.Series) -> pd.Series:
    ema12 = series.ewm(span=12).mean()
    ema26 = series.ewm(span=26).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9).mean()
    return macd - signal


def detect_technical_warnings(stock_id: str, px: pd.DataFrame | None = None) -> list[str]:
    """回傳技術面警示清單。px 可預先傳入避免重抓。"""
    warnings: list[str] = []
    try:
        if px is None:
            px = get_price_history(stock_id, 1)
        if len(px) < 30:
            return warnings
    except Exception:
        return warnings

    px = px.copy()
    px["close"] = pd.to_numeric(px["close"], errors="coerce")
    px["open"] = pd.to_numeric(px["open"], errors="coerce")
    px["high"] = pd.to_numeric(px["high"], errors="coerce")
    px["low"] = pd.to_numeric(px["low"], errors="coerce")
    px["volume"] = pd.to_numeric(px["volume"], errors="coerce")

    # 計算指標
    px["rsi"] = _rsi(px["close"])
    px["macd_hist"] = _macd_hist(px["close"])
    px["ma5"] = px["close"].rolling(5).mean()
    px["vol_ma20"] = px["volume"].rolling(20).mean()

    latest = px.iloc[-1]
    close = float(latest["close"])
    open_ = float(latest["open"])
    high = float(latest["high"])
    low = float(latest["low"])
    vol = float(latest["volume"])
    rsi_now = float(latest["rsi"]) if not pd.isna(latest["rsi"]) else None
    macd_now = float(latest["macd_hist"]) if not pd.isna(latest["macd_hist"]) else None
    ma5 = float(latest["ma5"]) if not pd.isna(latest["ma5"]) else None
    vol_ma = float(latest["vol_ma20"]) if not pd.isna(latest["vol_ma20"]) else None

    # 1. RSI 背離（股價創 10 日新高，RSI 沒創 10 日新高）
    if rsi_now is not None and len(px) >= 11:
        close_10d = px["close"].tail(10)
        rsi_10d = px["rsi"].tail(10).dropna()
        if not rsi_10d.empty:
            if close >= float(close_10d.max()) and rsi_now < float(rsi_10d.max()) * 0.97:
                warnings.append(
                    f"📉 RSI 背離：股價創 10 日新高但 RSI ({rsi_now:.1f}) 沒跟上 → 動能衰竭"
                )

    # 2. MACD 柱背離
    if macd_now is not None and len(px) >= 11:
        close_10d = px["close"].tail(10)
        hist_10d = px["macd_hist"].tail(10).dropna()
        if not hist_10d.empty:
            if (close >= float(close_10d.max())
                    and macd_now > 0
                    and macd_now < float(hist_10d.max()) * 0.85):
                warnings.append(
                    f"📉 MACD 柱背離：股價新高但 MACD 柱在縮短 → 多頭力道減弱"
                )

    # 3. 爆量長上影
    if vol_ma is not None and vol > vol_ma * 1.5:
        body = abs(close - open_)
        upper_shadow = high - max(close, open_)
        if body > 0 and upper_shadow > body * 2:
            warnings.append(
                f"⚠️ 爆量長上影：量增 {vol/vol_ma*100-100:.0f}%、上影是實體 {upper_shadow/body:.1f}x → 高檔賣壓"
            )

    # 4. 十字星（收盤接近開盤、價差小、量沒縮）
    if vol_ma is not None:
        body = abs(close - open_)
        range_ = high - low
        if range_ > 0 and body / range_ < 0.15 and vol > vol_ma * 0.8:
            warnings.append(
                f"⚠️ 十字星型態：開盤收盤接近、價差大 → 多空轉折點"
            )

    # 5. 跌破 5MA + 帶量
    if ma5 is not None and vol_ma is not None:
        if len(px) >= 2:
            prev_close = float(px.iloc[-2]["close"])
            if prev_close > ma5 and close < ma5 and vol > vol_ma * 1.3:
                warnings.append(
                    f"🔻 跌破 5MA + 帶量：今日收 {close:.1f} 跌破 5MA {ma5:.1f}、量增 {vol/vol_ma*100-100:.0f}% → 短期反轉確認"
                )

    return warnings
