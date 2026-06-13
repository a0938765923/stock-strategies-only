"""價值派因子（§7 §3.1）——「便宜相對自身歷史」。

正規化選百分位而非 z-score：估值分布長尾、右偏，z-score 會被極端高估值拉爆。
缺料判定由 registry（required_data=["valuation"]）負責回 None；
本體有 df 但樣本不足/NaN → 回 NEUTRAL(0.5)。
"""
from __future__ import annotations

import pandas as pd

from .base import NEUTRAL, clip01, rank_pct
from .registry import register


@register("value.cheap_pb", "value", ["valuation"],
          "PBR 相對自身 3 年歷史越低越便宜", lookback_min=1)
def cheap_pb(ctx, params):
    if "pbr" not in ctx.valuation.columns:
        return NEUTRAL
    w = params.get("pb_window", 756)
    s = pd.to_numeric(ctx.valuation["pbr"], errors="coerce").dropna().iloc[-w:]
    if len(s) < 20:                       # 樣本太少，百分位不可靠
        return NEUTRAL
    pbr_t = float(s.iloc[-1])
    if pbr_t <= 0:                        # 負淨值無意義
        return NEUTRAL
    return clip01(1.0 - rank_pct(s, pbr_t))


@register("value.cheap_pe", "value", ["valuation"],
          "PER 相對自身 3 年歷史越低越便宜；虧損(≤0)回中性", lookback_min=1)
def cheap_pe(ctx, params):
    if "per" not in ctx.valuation.columns:
        return NEUTRAL
    w = params.get("pe_window", 756)
    s = pd.to_numeric(ctx.valuation["per"], errors="coerce").dropna().iloc[-w:]
    if len(s) < 20:
        return NEUTRAL
    per_t = float(s.iloc[-1])
    if per_t <= 0:                        # 虧損 → PER 無意義，交給成長/營收因子判
        return NEUTRAL
    return clip01(1.0 - rank_pct(s, per_t))


@register("value.high_yield", "value", ["valuation"],
          "殖利率相對自身越高越便宜；不配息輕度偏空", lookback_min=1)
def high_yield(ctx, params):
    if "dividend_yield" not in ctx.valuation.columns:
        return NEUTRAL
    w = params.get("yield_window", 756)
    s = pd.to_numeric(ctx.valuation["dividend_yield"], errors="coerce").dropna().iloc[-w:]
    if len(s) < 20:
        return NEUTRAL
    yield_t = float(s.iloc[-1])
    if yield_t == 0:                      # 不配息 → 輕度偏空（非中性）
        return 0.3
    return clip01(rank_pct(s, yield_t))
