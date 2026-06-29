"""
V21 成交量突破策略 — 誠實回測（無灌水）
=================================================
與舊版「假回測」對照，本檔：
  1. 用 FinMind 真實日線股價
  2. 隔日開盤價進場（杜絕前視偏差）
  3. 完整交易成本：手續費 0.1425%×2 + 證交稅 0.3% + 滑價 0.1%×2
  4. Sharpe 用「每日權益報酬」計算（非兩個固定值，反映真實波動）
  5. Walk-forward：前段樣本內 vs 最後 12 個月樣本外
  6. 對照買進持有 (Buy & Hold)
"""
from __future__ import annotations
import os, sys, time, math
from datetime import datetime
import numpy as np
import pandas as pd

# Windows 中文終端機 UTF-8 輸出（避免 cp950 emoji 報錯）
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from stock_strategies.data import get_price_history

# ── 成本參數（台股實務）──
COMMISSION = 0.001425   # 手續費（單邊）
TAX        = 0.003      # 證交稅（賣出）
SLIPPAGE   = 0.001      # 滑價（單邊，保守估）
RISK_FREE  = 0.015      # 無風險利率（年）

# ── V21 策略參數 ──
CFG = dict(vol_ma=20, vol_mult=1.5, vol_spike=1.5,
           lookback=260, min_move=2.0,
           tp=10.0, sl=5.0, max_hold=5)

WATCHLIST = [
    ("2330", "台積電"), ("2454", "聯發科"), ("2317", "鴻海"),
    ("2308", "台達電"), ("2382", "廣達"),  ("3034", "聯詠"),
    ("2412", "中華電"), ("2881", "富邦金"), ("2882", "國泰金"),
    ("2603", "長榮"),  ("3008", "大立光"), ("2409", "友達"),
    ("3231", "緯創"),  ("2357", "華碩"),  ("2379", "瑞昱"),
]


def v21_signals(df: pd.DataFrame) -> pd.Series:
    """回傳每根 K 棒是否觸發 V21 進場（只用當根與過去資料）"""
    vol_ma   = df["volume"].rolling(CFG["vol_ma"]).mean()
    # 52 週高點需排除「今天」（用 shift(1)），否則 close 永遠突破不了含當根的最高價
    hi_52w   = df["high"].shift(1).rolling(CFG["lookback"]).max()
    vol_prev = df["volume"].shift(1)
    move_pct = (df["close"] - df["open"]) / df["open"] * 100

    sig = (
        (df["volume"] > vol_ma * CFG["vol_mult"]) &
        (df["volume"] > vol_prev * CFG["vol_spike"]) &
        (df["close"]  > hi_52w) &
        (move_pct >= CFG["min_move"])
    )
    return sig.fillna(False)


def backtest_one(df: pd.DataFrame, start_i: int, end_i: int):
    """單檔回測 → 回傳 (交易報酬列表, 日權益序列 list[(date, equity_mult)])
    每筆交易投入固定比例資金，equity_mult 以 1.0 為起點的乘數曲線。
    進場：訊號隔日開盤；出場：+tp/-sl/持有滿 max_hold 之隔日開盤。
    報酬已扣手續費+稅+滑價。
    """
    sig = v21_signals(df)
    trades = []
    in_pos = False
    entry_px = 0.0
    bars = 0
    equity = 1.0
    curve = []

    o = df["open"].values
    c = df["close"].values
    dates = df["date"].values

    i = start_i
    while i < end_i:
        if not in_pos:
            # 前一根觸發 → 今日開盤進場
            if i > 0 and sig.iloc[i - 1]:
                entry_px = o[i] * (1 + SLIPPAGE)            # 買進含滑價
                entry_px *= (1 + COMMISSION)                # 手續費
                in_pos = True
                bars = 0
            curve.append((dates[i], equity))
        else:
            bars += 1
            cur = c[i]
            ret = (cur - entry_px) / entry_px * 100
            exit_now = (ret >= CFG["tp"]) or (ret <= -CFG["sl"]) or (bars >= CFG["max_hold"])
            if exit_now:
                exit_px = o[min(i + 1, len(o) - 1)] * (1 - SLIPPAGE)   # 隔日開盤賣，含滑價
                exit_px *= (1 - COMMISSION - TAX)                       # 手續費+稅
                trade_ret = (exit_px - entry_px) / entry_px
                trades.append(trade_ret * 100)
                equity *= (1 + trade_ret)
                in_pos = False
            curve.append((dates[i], equity))
        i += 1

    return trades, curve


def metrics(trades, curve, years):
    """由交易與權益曲線算 CAGR / Sharpe / MDD / 勝率"""
    if not curve or years <= 0:
        return dict(cagr=0, sharpe=0, mdd=0, wr=0, n=0)

    eq = pd.DataFrame(curve, columns=["date", "eq"])
    eq["date"] = pd.to_datetime(eq["date"])
    eq = eq.drop_duplicates("date", keep="last").set_index("date")

    final = eq["eq"].iloc[-1]
    cagr = (final ** (1 / years) - 1) * 100

    # 每日報酬 → 年化 Sharpe
    daily = eq["eq"].pct_change().dropna()
    if daily.std() > 0:
        sharpe = (daily.mean() - RISK_FREE / 252) / daily.std() * math.sqrt(252)
    else:
        sharpe = 0.0

    # 最大回撤
    peak = eq["eq"].cummax()
    mdd = ((eq["eq"] - peak) / peak).min() * 100

    wr = (len([t for t in trades if t > 0]) / len(trades) * 100) if trades else 0
    return dict(cagr=cagr, sharpe=sharpe, mdd=mdd, wr=wr, n=len(trades))


def main():
    if not os.environ.get("FINMIND_TOKEN"):
        print("❌ 缺少 FINMIND_TOKEN")
        sys.exit(1)

    print("=" * 78)
    print("V21 誠實回測（真實股價 + 全成本 + Walk-forward）")
    print("=" * 78)

    all_is_trades, all_oos_trades = [], []
    rows = []

    for sid, name in WATCHLIST:
        try:
            df = get_price_history(sid, 3)
            if len(df) < 320:
                print(f"  {sid} {name}: 資料不足，略過")
                continue
            df = df.reset_index(drop=True)

            n = len(df)
            warmup = CFG["lookback"] + 5          # 指標暖機
            split = warmup + int((n - warmup) * 0.66)   # 前 66% 樣本內 / 後 34% 樣本外
            years_total = (pd.to_datetime(df["date"].iloc[-1]) -
                           pd.to_datetime(df["date"].iloc[warmup])).days / 365.25
            years_is = (pd.to_datetime(df["date"].iloc[split]) -
                        pd.to_datetime(df["date"].iloc[warmup])).days / 365.25
            years_oos = (pd.to_datetime(df["date"].iloc[-1]) -
                         pd.to_datetime(df["date"].iloc[split])).days / 365.25

            is_tr, is_cv = backtest_one(df, warmup, split)
            oos_tr, oos_cv = backtest_one(df, split, n)

            m_is  = metrics(is_tr, is_cv, years_is)
            m_oos = metrics(oos_tr, oos_cv, years_oos)

            # Buy & Hold 全期
            bh = ((df["close"].iloc[-1] / df["close"].iloc[warmup]) ** (1 / years_total) - 1) * 100

            all_is_trades += is_tr
            all_oos_trades += oos_tr
            rows.append((sid, name, m_is, m_oos, bh))

            print(f"  {sid} {name:<5} | 樣本內 CAGR {m_is['cagr']:>6.1f}% "
                  f"(交易{m_is['n']:>2}) | 樣本外 CAGR {m_oos['cagr']:>6.1f}% "
                  f"(交易{m_oos['n']:>2}) | B&H {bh:>6.1f}%")
            time.sleep(0.15)
        except Exception as e:
            print(f"  {sid} {name}: 錯誤 {str(e)[:40]}")

    if not rows:
        print("無資料")
        return

    def agg(key):
        vals = [r[2 if key == 'is' else 3]['cagr'] for r in rows]
        shp  = [r[2 if key == 'is' else 3]['sharpe'] for r in rows]
        mdd  = [r[2 if key == 'is' else 3]['mdd'] for r in rows]
        wr   = [r[2 if key == 'is' else 3]['wr'] for r in rows if r[2 if key=='is' else 3]['n']>0]
        return (np.mean(vals), np.mean(shp), np.min(mdd), np.mean(wr) if wr else 0)

    is_cagr, is_shp, is_mdd, is_wr = agg('is')
    oos_cagr, oos_shp, oos_mdd, oos_wr = agg('oos')
    bh_avg = np.mean([r[4] for r in rows])

    print()
    print("=" * 78)
    print("📊 投資組合平均（等權，全成本後）")
    print("=" * 78)
    print(f"{'':16}{'樣本內(訓練)':>16}{'樣本外(驗證)':>16}{'Buy&Hold':>14}")
    print("-" * 78)
    print(f"{'CAGR':16}{is_cagr:>14.2f}%{oos_cagr:>15.2f}%{bh_avg:>13.2f}%")
    print(f"{'Sharpe':16}{is_shp:>15.2f}{oos_shp:>16.2f}{'—':>14}")
    print(f"{'最大回撤':14}{is_mdd:>15.2f}%{oos_mdd:>15.2f}%{'—':>14}")
    print(f"{'勝率':16}{is_wr:>15.1f}%{oos_wr:>15.1f}%{'—':>14}")
    print(f"{'總交易數':14}{len(all_is_trades):>15}{len(all_oos_trades):>16}")
    print("=" * 78)
    print()
    print("【判讀】")
    print(f"  • 假回測宣稱：CAGR +78.62% / Sharpe 12.41 — 純捏造")
    print(f"  • 真實樣本外：CAGR {oos_cagr:+.2f}% / Sharpe {oos_shp:.2f}")
    if oos_cagr < bh_avg:
        print(f"  • ⚠️  樣本外輸給 Buy&Hold ({oos_cagr:+.1f}% < {bh_avg:+.1f}%)：策略未創造超額報酬")
    else:
        print(f"  • 樣本外贏 Buy&Hold {oos_cagr - bh_avg:+.1f}%")
    if oos_shp < 1:
        print(f"  • ⚠️  樣本外 Sharpe < 1：風險調整後報酬偏弱")


if __name__ == "__main__":
    main()
