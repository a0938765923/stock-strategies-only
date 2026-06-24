"""FinLab 策略對戰 — 用 FinLab 業界級回測引擎驗證我們的策略

特色：
  ✅ FinLab 回測引擎內建公告日對齊、合理手續費、滑價、漲跌停未成交處理
  ✅ 比我們自寫的 backtest 更接近真實表現
  ✅ 跑 3 個經典策略：動能、價值、低波動
  ✅ 跟我們的 portfolio_simulate 結果並排對比

執行: uv run python finlab_compare.py
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


def login_finlab():
    """登入 FinLab。已登入過就跳過"""
    import finlab
    token = os.environ.get("FINLAB_TOKEN", "").strip()
    if not token:
        print("❌ FINLAB_TOKEN 未設定", file=sys.stderr)
        sys.exit(1)
    finlab.login(api_token=token)


def run_simple_strategies() -> dict:
    """跑 3 個經典策略，回傳每個策略的指標 dict"""
    from finlab import data, backtest

    print("📥 抓取 FinLab 資料...")
    close = data.get("price:收盤價")
    rev = data.get("monthly_revenue:當月營收")
    print(f"  收盤價 shape: {close.shape}")
    print(f"  月營收 shape: {rev.shape}")
    print()

    results = {}

    # 策略 1: 動能突破（站上 60 日均線且 60 日報酬 > 0）
    print("🏃 策略 1: 動能突破（60MA + 60 日正報酬）")
    ma60 = close.rolling(60).mean()
    ret60 = close.pct_change(60)
    pos1 = (close > ma60) & (ret60 > 0)
    try:
        r1 = backtest.sim(pos1, name="動能突破", resample="W")
        s = r1.get_stats()
        results["momentum"] = {
            "name": "動能突破",
            "cagr": float(s.get("cagr", 0)) * 100,
            "mdd": float(s.get("max_drawdown", 0)) * 100,
            "sharpe": float(s.get("daily_sharpe", 0)),
            "total_return": float(s.get("total_return", 0)) * 100,
            "n_stocks_avg": float(r1.get_metrics().get("profitability", {}).get("avgNStock", 0)),
        }
        print(f"  CAGR: {results['momentum']['cagr']:+.2f}%")
        print(f"  MDD:  {results['momentum']['mdd']:+.2f}%")
        print(f"  Sharpe: {results['momentum']['sharpe']:.3f}")
    except Exception as e:
        print(f"  ⚠️ 回測失敗: {str(e)[:100]}")
        results["momentum"] = {"error": str(e)[:100]}
    print()

    # 策略 2: 月營收動能（YoY > 20% 且 MoM > 0）
    print("📈 策略 2: 月營收動能（YoY > 20% + MoM > 0）")
    try:
        rev_yoy = rev.pct_change(12)
        rev_mom = rev.pct_change(1)
        pos2 = (rev_yoy > 0.2) & (rev_mom > 0)
        r2 = backtest.sim(pos2, name="月營收動能", resample="M")
        s = r2.get_stats()
        results["revenue"] = {
            "name": "月營收動能",
            "cagr": float(s.get("cagr", 0)) * 100,
            "mdd": float(s.get("max_drawdown", 0)) * 100,
            "sharpe": float(s.get("daily_sharpe", 0)),
            "total_return": float(s.get("total_return", 0)) * 100,
            "n_stocks_avg": float(r2.get_metrics().get("profitability", {}).get("avgNStock", 0)),
        }
        print(f"  CAGR: {results['revenue']['cagr']:+.2f}%")
        print(f"  MDD:  {results['revenue']['mdd']:+.2f}%")
        print(f"  Sharpe: {results['revenue']['sharpe']:.3f}")
    except Exception as e:
        print(f"  ⚠️ 回測失敗: {str(e)[:100]}")
        results["revenue"] = {"error": str(e)[:100]}
    print()

    # 策略 3: 低波動 + 動能複合
    print("🎯 策略 3: 低波動 + 動能（60 日波動低 + 站上 20MA）")
    try:
        rets = close.pct_change()
        vol60 = rets.rolling(60).std()
        ma20 = close.rolling(20).mean()
        # 取每天波動最低的前 50 檔且站上 20MA
        low_vol_rank = vol60.rank(axis=1, pct=True) < 0.20
        pos3 = low_vol_rank & (close > ma20)
        r3 = backtest.sim(pos3, name="低波動+動能", resample="W")
        s = r3.get_stats()
        results["lowvol_momentum"] = {
            "name": "低波動+動能",
            "cagr": float(s.get("cagr", 0)) * 100,
            "mdd": float(s.get("max_drawdown", 0)) * 100,
            "sharpe": float(s.get("daily_sharpe", 0)),
            "total_return": float(s.get("total_return", 0)) * 100,
            "n_stocks_avg": float(r3.get_metrics().get("profitability", {}).get("avgNStock", 0)),
        }
        print(f"  CAGR: {results['lowvol_momentum']['cagr']:+.2f}%")
        print(f"  MDD:  {results['lowvol_momentum']['mdd']:+.2f}%")
        print(f"  Sharpe: {results['lowvol_momentum']['sharpe']:.3f}")
    except Exception as e:
        print(f"  ⚠️ 回測失敗: {str(e)[:100]}")
        results["lowvol_momentum"] = {"error": str(e)[:100]}
    print()

    return results


def compare_with_ours(finlab_results: dict):
    """跟我們的 portfolio_simulate 結果對比"""
    print("=" * 75)
    print("🏆 FinLab 策略 vs 你的系統（業界引擎 vs 自製引擎）")
    print("=" * 75)

    # 讀取我們之前的 portfolio_simulate_result.json
    ours_path = Path("portfolio_simulate_result.json")
    if not ours_path.exists():
        print("⚠️ 找不到 portfolio_simulate_result.json，跑一次 portfolio_simulate.py 後再對比")
        ours = None
    else:
        ours = json.loads(ours_path.read_text(encoding="utf-8"))

    print(f"{'策略':<22}{'CAGR':>10}{'MDD':>10}{'Sharpe':>10}")
    print("-" * 75)
    if ours:
        print(f"{'你的系統 (19 檔)':<22}"
              f"{ours['cagr']*100:>+9.2f}%"
              f"{ours['max_drawdown']*100:>+9.2f}%"
              f"{ours['sharpe']:>10.3f}")

    for key, name in [
        ("momentum", "FinLab: 動能突破"),
        ("revenue", "FinLab: 月營收動能"),
        ("lowvol_momentum", "FinLab: 低波動+動能"),
    ]:
        r = finlab_results.get(key, {})
        if "error" in r:
            print(f"{name:<22}{'(失敗)':>30}")
        elif r:
            print(f"{name:<22}{r['cagr']:>+9.2f}%{r['mdd']:>+9.2f}%{r['sharpe']:>10.3f}")
    print("=" * 75)
    print()

    print("📊 解讀：")
    if ours:
        ours_cagr = ours["cagr"] * 100
        finlab_best_cagr = max(
            (r["cagr"] for r in finlab_results.values() if "cagr" in r),
            default=0,
        )
        if ours_cagr > finlab_best_cagr:
            print(f"  ✅ 你的系統 CAGR ({ours_cagr:+.1f}%) > FinLab 最佳 ({finlab_best_cagr:+.1f}%)")
            print(f"     → 你的精選 Watchlist 確實有 alpha")
        else:
            print(f"  📊 FinLab 最佳 CAGR ({finlab_best_cagr:+.1f}%) > 你的系統 ({ours_cagr:+.1f}%)")
            print(f"     → 可以參考 FinLab 策略結構")


def format_telegram_summary(finlab_results: dict) -> str:
    """月度自驗證 Telegram 摘要"""
    from datetime import datetime
    lines = [
        f"📊 *月度系統自我驗證* {datetime.now().strftime('%Y/%m/%d')}",
        "",
        "_用 FinLab 業界引擎跑 3 種經典策略對比我們系統_",
        "",
    ]
    # 讀我們的結果
    ours_path = Path("portfolio_simulate_result.json")
    if ours_path.exists():
        ours = json.loads(ours_path.read_text(encoding="utf-8"))
        lines.append("🏆 *你的系統*")
        lines.append(f"  CAGR: *{ours['cagr']*100:+.2f}%*")
        lines.append(f"  MDD:  {ours['max_drawdown']*100:+.2f}%")
        lines.append(f"  Sharpe: *{ours['sharpe']:.2f}*")
        lines.append(f"  勝率: {ours['win_rate']*100:.1f}%（{ours['total_trades']} 筆）")
        lines.append("")

    lines.append("📈 *FinLab 基準策略*")
    for key, label in [
        ("momentum", "動能突破"),
        ("revenue", "月營收動能"),
        ("lowvol_momentum", "低波動+動能"),
    ]:
        r = finlab_results.get(key, {})
        if "error" in r:
            lines.append(f"  {label}: ⚠️ 失敗")
        elif r:
            lines.append(
                f"  {label}: CAGR {r['cagr']:+.2f}% / Sharpe {r['sharpe']:.2f} / MDD {r['mdd']:+.1f}%"
            )
    lines.append("")
    lines.append("━━━━━━━━━━")
    lines.append("📝 _Sharpe > 1 即業界等級、> 1.5 為頂級。_")
    return "\n".join(lines)


def main():
    print("=" * 75)
    print("🎯 FinLab 業界級回測引擎對戰")
    print("=" * 75)
    print()

    login_finlab()
    print("✅ FinLab 已登入")
    print()

    finlab_results = run_simple_strategies()
    compare_with_ours(finlab_results)

    # 持久化
    out_path = Path("finlab_compare_result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(finlab_results, f, ensure_ascii=False, indent=2)
    print(f"\n💾 詳細結果存到 {out_path.name}")

    # Telegram 推播（環境變數啟用）
    if os.environ.get("PUSH_TELEGRAM") in ("1", "true", "True", "yes"):
        try:
            from stock_strategies.notify import send_telegram
            send_telegram(format_telegram_summary(finlab_results))
            print("📱 Telegram 已推播")
        except Exception as e:
            print(f"⚠️ Telegram 失敗: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
