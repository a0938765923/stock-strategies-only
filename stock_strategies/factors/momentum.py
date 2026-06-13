"""動能派因子（§7 §3.3）——「相對強弱 / 距 52 週高 / MA 斜率」。

price_df 已含 add_indicators 的 ma5/ma20/ma60，直接讀取。
rolling/iloc 只到當日（price_df 已被 build_context 切到 ≤ as_of），無 look-ahead。
缺料 / 不足 lookback_min(60) 由 registry 回 None；本體樣本不足 → NEUTRAL(0.5)。
"""
from __future__ import annotations

import pandas as pd

from .base import NEUTRAL, clip01, rank_pct, zscore_clip
from .registry import register


@register("momentum.rs_self", "momentum", ["price_df"],
          "自身相對強弱：近 N 日報酬率在歷史百分位", lookback_min=60)
def rs_self(ctx, params):
    n = params.get("rs_window", 60)
    rank_window = params.get("rank_window", 252)
    c = pd.to_numeric(ctx.price_df["close"], errors="coerce")
    if len(c) < n + 2:
        return NEUTRAL
    ret_n = (c / c.shift(n) - 1.0).dropna()
    if len(ret_n) < 2:
        return NEUTRAL
    s = ret_n.iloc[-rank_window:]
    return clip01(rank_pct(s, float(s.iloc[-1])))


@register("momentum.dist_52w_high", "momentum", ["price_df"],
          "距 52 週高點，越接近動能越強", lookback_min=60)
def dist_52w_high(ctx, params):
    c = pd.to_numeric(ctx.price_df["close"], errors="coerce")
    h = pd.to_numeric(ctx.price_df["high"], errors="coerce")
    n = min(252, len(c))
    high_n = h.iloc[-n:].max()
    if pd.isna(high_n) or high_n <= 0:
        return NEUTRAL
    ratio = float(c.iloc[-1]) / float(high_n)
    return clip01((ratio - 0.7) / 0.3)    # 0.7→0、1.0→1


@register("momentum.ma_slope", "momentum", ["price_df"],
          "MA20 斜率相對自身近 120 日 z-score", lookback_min=60)
def ma_slope(ctx, params):
    slope_n = params.get("slope_n", 10)
    ma20 = pd.to_numeric(ctx.price_df["ma20"], errors="coerce")
    slope = (ma20 / ma20.shift(slope_n) - 1.0).dropna()
    if len(slope) < 2 or pd.isna(slope.iloc[-1]):
        return NEUTRAL
    s = slope.iloc[-120:]
    return clip01(zscore_clip(float(slope.iloc[-1]), s.mean(), s.std()))


@register("momentum.above_mas", "momentum", ["price_df"],
          "多頭排列強度 close>ma5>ma20>ma60", lookback_min=60)
def above_mas(ctx, params):
    row = ctx.price_df.iloc[-1]
    close, ma5, ma20, ma60 = row.get("close"), row.get("ma5"), row.get("ma20"), row.get("ma60")
    if pd.isna(close) or pd.isna(ma5) or pd.isna(ma20) or pd.isna(ma60):
        return NEUTRAL
    conds = [close > ma5, ma5 > ma20, ma20 > ma60, close > ma60]
    return clip01(sum(0.25 for c in conds if c))
