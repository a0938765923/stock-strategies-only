"""策略對戰回測

對 ~100 檔個股跑 3 年歷史資料，並排比較 3 個策略的：
- 總訊號樣本（多少次進場機會）
- 勝率（多少次賺錢）
- 平均報酬 / 最佳 / 最差
- 風險調整後報酬（avg_return / std，類 Sharpe）
- 期望值（勝率 * 平均贏 - 敗率 * 平均輸）

執行: uv run python backtest_compare.py
"""

import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from statistics import mean, pstdev

import pandas as pd

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from stock_strategies.data import get_price_history
from stock_strategies.indicators import add_indicators, tech_score_at


# 跟 backtest.py 同邏輯，但回傳「每筆交易報酬」的完整 list，方便算 Sharpe 等
def backtest_detailed(df: pd.DataFrame, params: dict) -> dict:
    hold_days = int(params["hold_days"])
    min_score = int(params["min_tech_score_for_signal"])
    target = float(params["target_return"])
    stop = float(params["stop_loss"])

    returns = []
    for i in range(60, len(df) - hold_days - 1):
        if tech_score_at(df.iloc[i], params)["score"] >= min_score:
            next_day = df.iloc[i + 1]
            entry = next_day.get("open")
            if entry is None or pd.isna(entry) or entry <= 0:
                continue
            future = df.iloc[i + 2 : i + 2 + hold_days]
            if len(future) < hold_days:
                continue
            hi, lo = future["high"].max(), future["low"].min()
            fc = future.iloc[-1]["close"]
            hit_target = hi >= entry * (1 + target)
            hit_stop = lo <= entry * (1 - stop)
            if hit_target and not hit_stop:
                returns.append(target)
            elif hit_stop:
                returns.append(-stop)
            else:
                returns.append((fc - entry) / entry)
    return {"returns": returns}


# 使用跟 market_scanner 同一份宇宙（去重後 ~112 檔）
from market_scanner import SCAN_UNIVERSE, _dedupe_universe  # noqa: E402


def load_strategy(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def aggregate(returns: list[float]) -> dict:
    n = len(returns)
    if n == 0:
        return {
            "samples": 0, "winrate": None, "avg": None, "best": None, "worst": None,
            "std": None, "sharpe_like": None, "expected_value": None,
        }
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    avg = mean(returns)
    std = pstdev(returns) if n > 1 else 0.0
    sharpe = (avg / std) if std > 0 else None
    win_avg = mean(wins) if wins else 0.0
    loss_avg = mean(losses) if losses else 0.0
    p_win = len(wins) / n
    ev = p_win * win_avg + (1 - p_win) * loss_avg
    return {
        "samples": n,
        "winrate": round(p_win * 100, 1),
        "avg": round(avg * 100, 2),
        "best": round(max(returns) * 100, 2),
        "worst": round(min(returns) * 100, 2),
        "std": round(std * 100, 2),
        "sharpe_like": round(sharpe, 3) if sharpe is not None else None,
        "expected_value": round(ev * 100, 2),
    }


def main():
    strategies_dir = Path(__file__).parent / "strategies"
    strategies = []
    for name in ["default", "conservative", "pro", "elite"]:
        p = strategies_dir / f"{name}.json"
        if not p.exists():
            print(f"⚠️ 找不到 {p}, 跳過", file=sys.stderr)
            continue
        strategies.append(load_strategy(p))

    print(f"準備對戰 {len(strategies)} 個策略：")
    for s in strategies:
        print(f"  - {s['id']:14s} | {s['name']}")
    print()

    universe = _dedupe_universe(SCAN_UNIVERSE)
    print(f"宇宙：{len(universe)} 檔個股，3 年歷史資料")
    print("（先抓所有股價，再分別跑 3 個策略）\n")

    # 每檔的所有交易結果，key = strategy_id
    all_returns: dict[str, list[float]] = defaultdict(list)
    per_stock: dict[str, dict] = defaultdict(dict)  # per_stock[strategy_id][stock_id] = stats

    success = 0
    fail = 0
    for i, (sid, name, cat) in enumerate(universe, 1):
        if i % 10 == 0 or i == len(universe):
            print(f"  進度 [{i}/{len(universe)}] 成功 {success} 失敗 {fail}")
        try:
            px = get_price_history(sid, 3)
            if len(px) < 100:
                fail += 1
                continue
            px = add_indicators(px)
            for s in strategies:
                params = s["params"]
                bt = backtest_detailed(px, params)
                if bt["returns"]:
                    all_returns[s["id"]].extend(bt["returns"])
                    per_stock[s["id"]][sid] = aggregate(bt["returns"])
            success += 1
        except Exception as e:
            fail += 1
            print(f"  ⚠️ {sid} {name} 失敗: {str(e)[:60]}")
        time.sleep(0.3)  # FinMind rate limit

    print(f"\n完成：成功 {success} 檔，失敗 {fail} 檔\n")

    # === 總結報告 ===
    print("=" * 78)
    print("📊 策略對戰總結（橫向比較全宇宙）")
    print("=" * 78)
    print(f"{'策略':<18} {'樣本':>7} {'勝率':>8} {'平均':>8} {'期望值':>9} {'σ':>7} {'Sharpe':>8} {'最佳':>8} {'最差':>8}")
    print("-" * 78)
    summaries = {}
    for s in strategies:
        agg = aggregate(all_returns[s["id"]])
        summaries[s["id"]] = agg
        if agg["samples"] == 0:
            print(f"{s['id']:<18} {'(無樣本)':>30}")
            continue
        print(
            f"{s['id']:<18} {agg['samples']:>7} "
            f"{agg['winrate']:>7.1f}% {agg['avg']:>+7.2f}% "
            f"{agg['expected_value']:>+8.2f}% {agg['std']:>+6.2f}% "
            f"{(agg['sharpe_like'] or 0):>+7.3f} "
            f"{agg['best']:>+7.2f}% {agg['worst']:>+7.2f}%"
        )
    print("=" * 78)

    # === 推薦 ===
    print("\n🏆 推薦：")
    # 三個維度排名
    if summaries:
        by_winrate = sorted(summaries.items(), key=lambda kv: -(kv[1]["winrate"] or 0))
        by_ev = sorted(summaries.items(), key=lambda kv: -(kv[1]["expected_value"] or 0))
        by_sharpe = sorted(summaries.items(), key=lambda kv: -(kv[1].get("sharpe_like") or 0))
        print(f"  勝率最高：    {by_winrate[0][0]}  ({by_winrate[0][1]['winrate']}%)")
        print(f"  期望值最高：  {by_ev[0][0]}  ({by_ev[0][1]['expected_value']:+.2f}%/交易)")
        print(f"  風險調整後最高：{by_sharpe[0][0]}  (Sharpe-like {by_sharpe[0][1]['sharpe_like']})")

    # === 把每檔對 PRO 策略的細部寫進 Sheet（讓使用者深入看哪些股配 PRO 最好）===
    # （優先寫 PRO，因為使用者目標就是 PRO；其他兩個跳過避免訊息洩太多）
    if "pro" in per_stock and per_stock["pro"]:
        print("\n📋 PRO 策略各股表現 Top 15（依勝率排序）")
        ranked = sorted(
            per_stock["pro"].items(),
            key=lambda kv: -(kv[1]["winrate"] or 0),
        )
        print(f"  {'股號':<8} {'樣本':>5} {'勝率':>7} {'平均':>7}")
        for sid, agg in ranked[:15]:
            if agg["samples"] >= 5:
                print(f"  {sid:<8} {agg['samples']:>5} {agg['winrate']:>6.1f}% {agg['avg']:>+6.2f}%")

    # 簡潔 JSON 落地給後續看
    out_path = Path(__file__).parent / "backtest_compare_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "summaries": summaries,
            "per_stock_pro": per_stock.get("pro", {}),
            "universe_size": len(universe),
            "success": success,
            "fail": fail,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n💾 詳細結果存到 {out_path.name}")


if __name__ == "__main__":
    main()
