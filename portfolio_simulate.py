"""投資組合模擬 — 3 年回測你 Watchlist 的真實表現

跟之前的 backtest_compare.py 不同：
- 不是看「每筆訊號的平均報酬」（抽象數字）
- 而是模擬「**100 萬本金、跟著訊號操作 3 年後變多少**」（真實結果）

模擬流程：
  1. 抓你 Watchlist 19 檔的 3 年股價
  2. 用每檔的 strategy_id 跑策略，找出歷史所有訊號日
  3. 依時間順序執行：
     - 訊號日 +1 開盤進場（real-world 可執行）
     - 每筆用 Half-Kelly 算倉位（高勝率股大倉位）
     - 觸及停利、停損或持有滿 N 日 → 出場
     - 同檔個股有部位時不重複進場
     - 總曝險不超過 80%（保留 20% 現金）
  4. 算最終資產、CAGR、Sharpe、最大回撤
  5. 跟「同期間台灣 50 ETF」做基準比較

執行: uv run python portfolio_simulate.py
"""

import json
import sys
import time
from collections import defaultdict
from datetime import datetime
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
from stock_strategies.loader import get_strategy, merge_params
from stock_strategies.sheet import read_watchlist
from stock_strategies.kelly import half_kelly


INITIAL_CAPITAL = 1_000_000  # 100 萬本金
MAX_EXPOSURE = 0.80  # 最高總曝險 80%（保留 20% 現金應變）
DEFAULT_POSITION = 0.05  # Kelly 無資料時的預設倉位


def find_signals(df: pd.DataFrame, params: dict) -> list[dict]:
    """掃描每一根 K 棒，找出技術分達門檻的訊號日。
    回傳 [{idx, date, score, entry_idx, ...}]
    """
    min_score = int(params["min_tech_score_for_signal"])
    hold = int(params["hold_days"])
    target = float(params["target_return"])
    stop = float(params["stop_loss"])

    signals = []
    for i in range(60, len(df) - hold - 1):
        score = tech_score_at(df.iloc[i], params)["score"]
        if score < min_score:
            continue
        next_open = df.iloc[i + 1].get("open")
        if next_open is None or pd.isna(next_open) or next_open <= 0:
            continue
        signals.append({
            "signal_idx": i,
            "signal_date": df.iloc[i]["date"],
            "entry_idx": i + 1,
            "entry_date": df.iloc[i + 1]["date"],
            "entry_price": float(next_open),
            "score": score,
            "target": target,
            "stop": stop,
            "hold": hold,
        })
    return signals


def simulate_trade(df: pd.DataFrame, sig: dict) -> dict:
    """從進場開始算出場日與報酬"""
    entry = sig["entry_price"]
    target_price = entry * (1 + sig["target"])
    stop_price = entry * (1 - sig["stop"])
    for j in range(sig["entry_idx"] + 1, min(sig["entry_idx"] + 1 + sig["hold"], len(df))):
        row = df.iloc[j]
        hi = float(row["high"]) if not pd.isna(row["high"]) else entry
        lo = float(row["low"]) if not pd.isna(row["low"]) else entry
        cl = float(row["close"]) if not pd.isna(row["close"]) else entry
        if hi >= target_price:
            return {"exit_idx": j, "exit_date": row["date"], "exit_price": target_price,
                    "ret": sig["target"], "exit_type": "TARGET"}
        if lo <= stop_price:
            return {"exit_idx": j, "exit_date": row["date"], "exit_price": stop_price,
                    "ret": -sig["stop"], "exit_type": "STOP"}
    # 時間到出場
    end_idx = min(sig["entry_idx"] + sig["hold"], len(df) - 1)
    end_row = df.iloc[end_idx]
    end_price = float(end_row["close"])
    return {"exit_idx": end_idx, "exit_date": end_row["date"], "exit_price": end_price,
            "ret": (end_price - entry) / entry, "exit_type": "TIME"}


def collect_trades_for_stock(stock_id: str, name: str, strategy_id: str) -> list[dict]:
    """跑該股 3 年所有訊號 + 模擬出場，回傳交易清單"""
    strategy = get_strategy(strategy_id) or get_strategy("default")
    params = merge_params(strategy)
    try:
        px = get_price_history(stock_id, 3)
        if len(px) < 100:
            return []
        px = add_indicators(px)
    except Exception as e:
        print(f"  ⚠️ {stock_id} 抓不到資料: {e}")
        return []

    signals = find_signals(px, params)
    trades = []
    last_exit_idx = -1
    for sig in signals:
        if sig["entry_idx"] <= last_exit_idx:
            continue  # 還持有中，跳過下一個訊號
        exit_info = simulate_trade(px, sig)
        last_exit_idx = exit_info["exit_idx"]
        trades.append({
            "stock_id": stock_id, "name": name, "strategy_id": strategy_id,
            **sig, **exit_info,
        })
    return trades


def kelly_position_for_trade(trade: dict, params: dict, prior_trades: list[dict]) -> float:
    """根據該股之前的歷史 trades 算 Kelly 倉位（首筆用回測預設）"""
    history = [t for t in prior_trades if t["stock_id"] == trade["stock_id"]]
    if len(history) < 3:
        # 用回測 winrate（從 target/stop 推算理論 R/R）
        winrate_guess = 0.5
        avg_win = params["target_return"]
        avg_loss = params["stop_loss"]
        return half_kelly(winrate_guess, avg_win, avg_loss)
    wins = [t["ret"] for t in history if t["ret"] > 0]
    losses = [-t["ret"] for t in history if t["ret"] <= 0]
    if not wins or not losses:
        return DEFAULT_POSITION
    p = len(wins) / len(history)
    return half_kelly(p, mean(wins), mean(losses))


def run_portfolio(watchlist: list[dict]) -> dict:
    """主模擬流程。回傳 dict 含交易、權益曲線、統計指標。

    若設定環境變數 STRATEGY_OVERRIDE → 所有股票都用同一個策略（用於對比實驗）
    """
    import os as _os
    override = _os.environ.get("STRATEGY_OVERRIDE", "").strip()
    all_trades = []
    for i, row in enumerate(watchlist, 1):
        sid = str(row["stock_id"]).zfill(4) if str(row["stock_id"]).isdigit() else str(row["stock_id"])
        name = row.get("name", "")
        strategy_id = override or (str(row.get("strategy_id", "default")).strip() or "default")
        print(f"[{i}/{len(watchlist)}] {sid} {name} ({strategy_id})")
        trades = collect_trades_for_stock(sid, name, strategy_id)
        all_trades.extend(trades)
        time.sleep(0.5)

    if not all_trades:
        return {"error": "沒有任何訊號"}

    # 依時間排序
    all_trades.sort(key=lambda t: t["entry_date"])

    # 模擬資金流：每筆進場用 Kelly 算倉位
    cash = INITIAL_CAPITAL
    open_positions: dict[str, dict] = {}  # stock_id → {entry_date, exit_date, capital_used, ret}
    executed_trades = []
    equity_curve = []  # [(date, total_equity)]

    # 依 entry_date / exit_date 走時間軸
    events = []
    for t in all_trades:
        events.append((t["entry_date"], "ENTRY", t))
        events.append((t["exit_date"], "EXIT", t))
    events.sort(key=lambda e: (e[0], 0 if e[1] == "EXIT" else 1))  # 同日先出再進

    cumulative_pnl = 0.0
    for date, etype, t in events:
        if etype == "ENTRY":
            sid = t["stock_id"]
            if sid in open_positions:
                continue  # 已有部位
            # 計算 Kelly 倉位
            strategy = get_strategy(t["strategy_id"]) or get_strategy("default")
            params = merge_params(strategy)
            pos_pct = kelly_position_for_trade(t, params, executed_trades)
            # 檢查總曝險 + 現金
            current_exposure = sum(p["capital"] for p in open_positions.values())
            available = INITIAL_CAPITAL * MAX_EXPOSURE - current_exposure
            capital = min(INITIAL_CAPITAL * pos_pct, available, cash)
            if capital <= 0:
                continue
            cash -= capital
            open_positions[sid] = {
                "trade": t, "capital": capital, "entry_date": date,
                "pos_pct": pos_pct,
            }
        else:  # EXIT
            sid = t["stock_id"]
            pos = open_positions.pop(sid, None)
            if pos is None or pos["trade"] is not t:
                continue
            pnl = pos["capital"] * t["ret"]
            cash += pos["capital"] + pnl
            cumulative_pnl += pnl
            executed_trades.append({
                **t,
                "capital_used": pos["capital"],
                "pos_pct": pos["pos_pct"],
                "pnl": pnl,
            })

        # 記錄當下權益（持倉以未實現損益估）
        total_equity = cash
        for p in open_positions.values():
            # 用最後成交價估目前未實現損益（簡化：用 entry 不計浮動）
            total_equity += p["capital"]
        equity_curve.append((date, total_equity))

    # 收尾：強制平掉所有剩餘部位
    for sid, p in open_positions.items():
        cash += p["capital"]
    open_positions.clear()

    final_value = cash
    total_return = (final_value - INITIAL_CAPITAL) / INITIAL_CAPITAL

    # 算 CAGR / MDD / Sharpe（用權益曲線）
    if equity_curve:
        equity_curve.sort(key=lambda x: x[0])
        # CAGR
        first_date = pd.to_datetime(equity_curve[0][0])
        last_date = pd.to_datetime(equity_curve[-1][0])
        years = (last_date - first_date).days / 365.25 if last_date > first_date else 1
        cagr = (final_value / INITIAL_CAPITAL) ** (1 / years) - 1 if years > 0 else 0
        # MDD
        peak = INITIAL_CAPITAL
        mdd = 0
        for _, eq in equity_curve:
            peak = max(peak, eq)
            dd = (eq - peak) / peak
            mdd = min(mdd, dd)
        # 月報酬 std → Sharpe
        df_eq = pd.DataFrame(equity_curve, columns=["date", "equity"])
        df_eq["date"] = pd.to_datetime(df_eq["date"])
        df_eq = df_eq.set_index("date").resample("ME").last().dropna()
        monthly_rets = df_eq["equity"].pct_change().dropna()
        sharpe = (monthly_rets.mean() / monthly_rets.std() * (12 ** 0.5)) if monthly_rets.std() > 0 else 0
    else:
        cagr = mdd = sharpe = 0
        years = 0

    return {
        "initial_capital": INITIAL_CAPITAL,
        "final_value": final_value,
        "total_return": total_return,
        "years": years,
        "cagr": cagr,
        "max_drawdown": mdd,
        "sharpe": sharpe,
        "total_trades": len(executed_trades),
        "win_rate": sum(1 for t in executed_trades if t["ret"] > 0) / len(executed_trades) if executed_trades else 0,
        "executed_trades": executed_trades,
        "equity_curve": equity_curve,
    }


def benchmark_buy_and_hold(stock_id: str = "0050") -> dict:
    """同期間 buy & hold 0050 作為基準"""
    try:
        px = get_price_history(stock_id, 3)
        if len(px) < 100:
            return {"error": "資料不足"}
        first = float(px.iloc[0]["close"])
        last = float(px.iloc[-1]["close"])
        ret = (last - first) / first
        days = (pd.to_datetime(px.iloc[-1]["date"]) - pd.to_datetime(px.iloc[0]["date"])).days
        years = days / 365.25
        cagr = (last / first) ** (1 / years) - 1 if years > 0 else 0
        return {"return": ret, "cagr": cagr, "years": years, "first": first, "last": last}
    except Exception as e:
        return {"error": str(e)}


def main():
    print("=" * 75)
    print("🎯 投資組合 3 年回測模擬")
    print(f"💰 起始資金: {INITIAL_CAPITAL:,} TWD")
    print(f"📊 最大曝險: {MAX_EXPOSURE*100:.0f}%（保留 {(1-MAX_EXPOSURE)*100:.0f}% 現金）")
    print(f"📈 倉位: Half-Kelly 動態（依該股歷史勝率）")
    print("=" * 75)
    print()

    print(f"[{datetime.now()}] 讀取 Watchlist...")
    watchlist = read_watchlist()
    print(f"  → {len(watchlist)} 檔啟用中\n")

    result = run_portfolio(watchlist)
    if "error" in result:
        print(f"❌ {result['error']}")
        return

    print()
    print("=" * 75)
    print("📊 模擬結果")
    print("=" * 75)
    print(f"  起始: {result['initial_capital']:>14,.0f} TWD")
    print(f"  結束: {result['final_value']:>14,.0f} TWD")
    print(f"  總報酬: {result['total_return']*100:>+13.2f}%")
    print(f"  期間:   {result['years']:>13.2f} 年")
    print(f"  CAGR:   {result['cagr']*100:>+13.2f}%/年")
    print(f"  最大回撤: {result['max_drawdown']*100:>+11.2f}%")
    print(f"  Sharpe: {result['sharpe']:>13.2f}")
    print(f"  交易次數: {result['total_trades']:>11}")
    print(f"  勝率:   {result['win_rate']*100:>13.1f}%")
    print()

    # === Benchmark vs 0050 ===
    print("=" * 75)
    print("📊 基準對照（同期間 buy & hold 0050）")
    print("=" * 75)
    bm = benchmark_buy_and_hold("0050")
    if "error" not in bm:
        print(f"  0050 總報酬: {bm['return']*100:>+10.2f}%")
        print(f"  0050 CAGR:   {bm['cagr']*100:>+10.2f}%/年")
        print()
        alpha = result["cagr"] - bm["cagr"]
        print(f"  📈 你的策略 vs 0050 (alpha): {alpha*100:+.2f}%/年")
    else:
        print(f"  0050 基準抓不到: {bm['error']}")
    print()

    # === Top 10 / Bottom 10 ===
    trades = result["executed_trades"]
    if trades:
        ranked = sorted(trades, key=lambda t: -t["pnl"])
        print("=" * 75)
        print("🏆 最賺前 5 筆")
        print("=" * 75)
        for t in ranked[:5]:
            print(f"  {t['entry_date'].strftime('%Y-%m-%d') if hasattr(t['entry_date'], 'strftime') else t['entry_date']} "
                  f"→ {t['exit_date'].strftime('%Y-%m-%d') if hasattr(t['exit_date'], 'strftime') else t['exit_date']} "
                  f"{t['stock_id']} {t['name']} "
                  f"投入 {t['capital_used']/1000:>5.0f}K · "
                  f"報酬 {t['ret']*100:+5.1f}% · "
                  f"獲利 {t['pnl']/1000:+6.1f}K · "
                  f"出場 {t['exit_type']}")

        print()
        print("=" * 75)
        print("😱 最虧後 5 筆")
        print("=" * 75)
        for t in ranked[-5:]:
            print(f"  {t['entry_date'].strftime('%Y-%m-%d') if hasattr(t['entry_date'], 'strftime') else t['entry_date']} "
                  f"→ {t['exit_date'].strftime('%Y-%m-%d') if hasattr(t['exit_date'], 'strftime') else t['exit_date']} "
                  f"{t['stock_id']} {t['name']} "
                  f"投入 {t['capital_used']/1000:>5.0f}K · "
                  f"報酬 {t['ret']*100:+5.1f}% · "
                  f"獲利 {t['pnl']/1000:+6.1f}K · "
                  f"出場 {t['exit_type']}")

    # === 持久化 ===
    out_path = Path(__file__).parent / "portfolio_simulate_result.json"
    summary = {
        "initial_capital": result["initial_capital"],
        "final_value": result["final_value"],
        "total_return": result["total_return"],
        "years": result["years"],
        "cagr": result["cagr"],
        "max_drawdown": result["max_drawdown"],
        "sharpe": result["sharpe"],
        "total_trades": result["total_trades"],
        "win_rate": result["win_rate"],
        "benchmark_0050_cagr": bm.get("cagr", 0) if "error" not in bm else None,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n💾 詳細結果存到 {out_path.name}")


if __name__ == "__main__":
    main()
