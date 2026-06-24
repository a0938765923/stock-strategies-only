"""FinLab 自動策略搜尋器 — 跑 12 種策略變化、按 Sharpe 排名、找冠軍

12 個策略涵蓋：
  動能、營收、低波動、多因子、KD、突破、借券、高 ROE
評估指標：
  CAGR（年化）、MDD（最大回撤）、Sharpe（風險調整）、勝率、平均持股
排名加權：
  Sharpe × 0.4 + (CAGR/30) × 0.3 + (1 + MDD/0.5) × 0.2 + (勝率-0.5) × 2 × 0.1
  → 越穩、越賺、回撤越小、越精準 = 越高分

執行: uv run python finlab_strategy_finder.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def login():
    import finlab
    token = os.environ.get("FINLAB_TOKEN", "").strip()
    if not token:
        print("❌ FINLAB_TOKEN 未設定", file=sys.stderr)
        sys.exit(1)
    finlab.login(api_token=token)


def safe_sim(signal, name, **kwargs):
    """安全跑回測，失敗回 None"""
    from finlab import backtest
    try:
        report = backtest.sim(signal, name=name, **kwargs)
        stats = report.get_stats()
        metrics = report.get_metrics()
        return {
            "name": name,
            "cagr": float(stats.get("cagr", 0)) * 100,
            "mdd": float(stats.get("max_drawdown", 0)) * 100,
            "sharpe": float(stats.get("daily_sharpe", 0)),
            "win_ratio": float(stats.get("win_ratio", 0)) * 100,
            "total_return": float(stats.get("total_return", 0)) * 100,
            "avg_n_stock": float(metrics.get("profitability", {}).get("avgNStock", 0)),
            "best_month": float(stats.get("best_month", 0)) * 100,
            "worst_month": float(stats.get("worst_month", 0)) * 100,
            "twelve_m_win": float(stats.get("twelve_month_win_perc", 0)) * 100,
        }
    except Exception as e:
        print(f"  ⚠️ {name} 失敗: {str(e)[:100]}")
        return None


def score_strategy(s: dict) -> float:
    """綜合評分：Sharpe×0.4 + (CAGR/30)×0.3 + (1+MDD/0.5)×0.2 + 勝率×0.1"""
    if not s or s["cagr"] <= 0:
        return -999
    sharpe_score = max(-1, min(2, s["sharpe"])) / 2  # 正規化 -1~2 → -0.5~1
    cagr_score = max(0, min(30, s["cagr"])) / 30      # 0~30% → 0~1
    mdd_score = max(0, 1 + s["mdd"] / 50)             # mdd -50% → 0；0% → 1
    win_score = max(0, (s["win_ratio"] - 40) / 20)    # 40~60% → 0~1
    return round(sharpe_score * 0.4 + cagr_score * 0.3 + mdd_score * 0.2 + win_score * 0.1, 3)


def main():
    print("=" * 78)
    print("🔍 FinLab 自動策略搜尋器 — 找出最強組合")
    print("=" * 78)
    print()
    login()
    print("✅ FinLab 已登入")
    print()

    from finlab import data

    print("📥 抓取所有需要的資料...")
    close = data.get("price:收盤價")
    open_ = data.get("price:開盤價")
    high = data.get("price:最高價")
    low = data.get("price:最低價")
    volume = data.get("price:成交股數")
    rev_yoy = data.get("monthly_revenue:去年同月增減(%)").index_str_to_date()
    rev_mom = data.get("monthly_revenue:上月比較增減(%)").index_str_to_date()
    try:
        sl = data.get("security_lending:借券餘額")
    except Exception:
        sl = None
    print(f"  收盤價: {close.shape}, 借券: {'有' if sl is not None else '無'}")
    print()

    # 預算指標
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    ma120 = close.rolling(120).mean()
    ret60 = close.pct_change(60)
    ret120 = close.pct_change(120)
    rets = close.pct_change()
    vol_60 = volume.rolling(60).mean()
    vol60_rank = vol_60.rank(axis=1, pct=True)
    # KD 簡易版
    high20 = high.rolling(20).max()
    low20 = low.rolling(20).min()
    k = ((close - low20) / (high20 - low20) * 100).fillna(50).clip(0, 100)
    d = k.rolling(3).mean()
    # 60 日波動
    vol60_ret = rets.rolling(60).std()
    lowvol = vol60_ret.rank(axis=1, pct=True) < 0.3

    # 通用流動性過濾
    liquid_strict = vol60_rank > 0.7
    liquid_loose = vol60_rank > 0.5

    # ============================================================
    # 12 個策略候選
    # ============================================================
    candidates = [
        # 1. 純動能：站上 60MA + 60d 正報酬
        ("S01_動能突破",
         (close > ma60) & (ret60 > 0) & liquid_strict,
         {"resample": "W"}),

        # 2. 黃金交叉：5MA 上穿 20MA
        ("S02_黃金交叉",
         (ma5 > ma20) & (ma5.shift(5) < ma20.shift(5)) & liquid_strict,
         {"resample": "W"}),

        # 3. 多重均線：站上 20MA, 60MA, 120MA
        ("S03_均線多排",
         (close > ma20) & (ma20 > ma60) & (ma60 > ma120) & liquid_strict,
         {"resample": "M"}),

        # 4. 月營收 YoY > 0 + 站上 60MA
        ("S04_營收+動能",
         (rev_yoy.reindex(close.index).ffill() > 0) & (close > ma60) & liquid_loose,
         {"resample": "M"}),

        # 5. 月營收 YoY > 20% (高成長)
        ("S05_營收強勢",
         (rev_yoy.reindex(close.index).ffill() > 20) & (close > ma20) & liquid_loose,
         {"resample": "M"}),

        # 6. 營收 YoY > 0 AND MoM > 0
        ("S06_營收雙增",
         (rev_yoy.reindex(close.index).ffill() > 0) & (rev_mom.reindex(close.index).ffill() > 0) & liquid_loose,
         {"resample": "M"}),

        # 7. 低波動 + 站上 60MA
        ("S07_低波動+趨勢",
         lowvol & (close > ma60) & liquid_strict,
         {"resample": "W"}),

        # 8. KD 黃金交叉（K 上穿 D 且 K < 70）
        ("S08_KD黃金交叉",
         (k > d) & (k.shift() < d.shift()) & (k < 70) & liquid_strict,
         {"resample": "W"}),

        # 9. 動能排名前 20%
        ("S09_動能排名前20",
         (ret60.rank(axis=1, pct=True) > 0.80) & liquid_strict,
         {"resample": "M"}),

        # 10. 多因子前 20%：動能 + 營收
        ("S10_多因子前20",
         ((ret60.rank(axis=1, pct=True) + (rev_yoy.reindex(close.index).ffill()).rank(axis=1, pct=True)) / 2 > 0.80) & liquid_strict,
         {"resample": "M"}),

        # 11. 突破前 60 日高 + 量增
        ("S11_突破+爆量",
         (close >= close.rolling(60).max()) & (volume > vol_60 * 1.5) & liquid_strict,
         {"resample": "W"}),

        # 12. 借券下降 + 動能（FinLab 獨家）
        # 借券 5 日減少 → 機構回補空單 → 多方訊號
        ("S12_借券回補+動能",
         (sl.diff(5) < 0) & (close > ma60) & liquid_strict if sl is not None else None,
         {"resample": "W"}),
    ]

    # 加上統一的停損停利
    results = []
    for name, signal, kwargs in candidates:
        if signal is None:
            print(f"⏭️  {name} 跳過（資料缺）")
            continue
        print(f"🏃 {name} 跑回測中...")
        kwargs.setdefault("stop_loss", 0.08)
        kwargs.setdefault("take_profit", 0.15)
        r = safe_sim(signal, name, **kwargs)
        if r:
            r["score"] = score_strategy(r)
            results.append(r)
            print(f"   CAGR {r['cagr']:+.2f}% / MDD {r['mdd']:+.2f}% / Sharpe {r['sharpe']:.3f} / 評分 {r['score']:.3f}")
        print()

    # ============================================================
    # 排名輸出
    # ============================================================
    if not results:
        print("❌ 沒有任何策略成功")
        return

    results.sort(key=lambda x: -x["score"])

    print("=" * 78)
    print(f"🏆 排名（共 {len(results)} 個策略，按綜合評分）")
    print("=" * 78)
    print(f"{'#':<3}{'策略':<22}{'CAGR':>9}{'MDD':>10}{'Sharpe':>9}{'勝率':>8}{'評分':>9}")
    print("-" * 78)
    for i, r in enumerate(results, 1):
        marker = " 👑" if i == 1 else ""
        print(f"{i:<3}{r['name']:<22}{r['cagr']:>+8.2f}%{r['mdd']:>+9.2f}%"
              f"{r['sharpe']:>9.3f}{r['win_ratio']:>7.1f}%{r['score']:>9.3f}{marker}")
    print("=" * 78)

    # 寫入冠軍策略
    winner = results[0]
    print(f"\n👑 冠軍：{winner['name']}")
    print(f"   CAGR: {winner['cagr']:+.2f}% | MDD: {winner['mdd']:+.2f}% | Sharpe: {winner['sharpe']:.3f}")
    print(f"   勝率: {winner['win_ratio']:.1f}% | 平均持股: {winner['avg_n_stock']:.0f} 檔")

    # 儲存所有結果與冠軍
    out = {
        "rankings": results,
        "winner": winner,
    }
    out_path = Path("finlab_strategy_finder_result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n💾 完整結果存到 {out_path.name}")


if __name__ == "__main__":
    main()
